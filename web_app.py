"""Aplicația web: se conectează la mail (ex. Yahoo), listează mailurile cu
PDF-uri de transport și descarcă PDF-urile reorganizate
(prima pagină x2 + ultimele 3 pagini), fie unul singur, fie ZIP.

Setări prin variabile de mediu — vezi README.md.
"""

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, redirect, render_template, request, send_file,
                   session, url_for)

from werkzeug.middleware.proxy_fix import ProxyFix

import journal
import registry
from mailbox_client import Mailbox, record_download, state_from_journal
from process_pdf import analyse, merge, safe_filename
from new_orders import RO, describe, pick_new, when
from transport_order import build_register, is_transport_order, parse_order

app = Flask(__name__)
# Render pune serverul în spatele unui proxy HTTPS; așa linkurile generate încep cu https://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Fără SECRET_KEY, cheia e derivată din parole, ca toate procesele serverului să o folosească pe aceeași.
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    ("transport:" + os.environ.get("SITE_PASSWORD", "") + os.environ.get("MAIL_PASSWORD", "")).encode()).hexdigest()
app.config.update(MAX_CONTENT_LENGTH=200 * 1024 * 1024,
                  SESSION_COOKIE_SECURE=os.environ.get("HTTPS", "1") == "1",
                  SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)

MAIL = dict(
    server=os.environ.get("MAIL_SERVER", "imap.mail.yahoo.com"),
    port=int(os.environ.get("MAIL_PORT", "993")),
    user=os.environ.get("MAIL_USER", ""),
    password=os.environ.get("MAIL_PASSWORD", ""),
    folder=os.environ.get("MAIL_FOLDER", "INBOX"),
)
DEFAULT_FROM = os.environ.get("FILTER_FROM", "")
DEFAULT_SUBJECT = os.environ.get("FILTER_SUBJECT", "")
COPIES = int(os.environ.get("FIRST_PAGE_COPIES", "2"))
LAST = int(os.environ.get("LAST_PAGES", "3"))
EXPECTED_PAGES = int(os.environ.get("EXPECTED_PAGES", "6"))
NEW_ORDERS_DAYS = int(os.environ.get("NEW_ORDERS_DAYS", "45"))   # cât de departe caută „Comenzi noi”
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
journal.configure(lambda: Mailbox(**MAIL))
STATE = Path(os.environ.get("DATA_DIR", ".")) / "downloaded.json"


def _load_state():
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _mark_downloaded(ids):
    seen = _load_state() | {i for i in ids if i}
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    except OSError:
        pass  # pe găzduiri fără disc permanent doar pierdem marcajul „descărcat”


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not session.get("ok"):
            # După intrare, înapoi unde era omul (pentru un POST: pagina de unde a venit).
            back = request.full_path if request.method == "GET" else (request.referrer or "")
            return redirect(url_for("login", next=_safe_next(back)))
        return view(*a, **kw)
    return wrapper


def _safe_next(target):
    """Doar adrese din site (fără „//alt-site” sau „https://…”), ca linkul de intrare să nu poată trimite în altă parte."""
    if not target:
        return None
    if target.startswith(request.host_url):
        target = "/" + target[len(request.host_url):]
    target = target.rstrip("?")
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else None


# --- Limita de încercări pentru parolă -----------------------------------------------------------
# 5 parole greșite de la aceeași adresă în 15 minute → blocare 15 minute; peste 30 în total pe oră
# → blocare pentru toți (cineva încearcă din mai multe locuri). Ținută într-un fișier, ca toate
# procesele serverului s-o vadă.
RATE_FILE = Path(tempfile.gettempdir()) / "cirforvest-login-attempts.json"
RATE_IP, RATE_IP_WINDOW, RATE_ALL, RATE_ALL_WINDOW = 5, 15 * 60, 30, 60 * 60


def _attempts(update=None):
    import fcntl
    with open(RATE_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            data = json.loads(f.read() or "[]")
        except ValueError:
            data = []
        now = time.time()
        data = [a for a in data if now - a[1] < RATE_ALL_WINDOW]
        if update:
            data.append(update)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data))
        return data


def _client_ip():
    return request.remote_addr or "?"


def _login_blocked():
    """Câte minute mai e blocată intrarea (0 = nu e blocată)."""
    now, ip = time.time(), _client_ip()
    data = _attempts()
    mine = [t for a_ip, t in data if a_ip == ip and now - t < RATE_IP_WINDOW]
    if len(mine) >= RATE_IP:
        return max(1, round((RATE_IP_WINDOW - (now - mine[-RATE_IP])) / 60))
    if len(data) >= RATE_ALL:
        return max(1, round((RATE_ALL_WINDOW - (now - data[-RATE_ALL][1])) / 60))
    return 0


def _login_failed():
    _attempts(update=[_client_ip(), time.time()])


@app.context_processor
def _template_helpers():
    return {"has_view": lambda name: name in app.view_functions}


@app.route("/manifest.webmanifest")
def manifest():
    """Descrierea aplicației pentru „Adaugă pe ecranul principal” (iconiță, deschidere fără bara browserului)."""
    data = {
        "name": "Cirforvest", "short_name": "Cirforvest", "lang": "ro",
        "description": "Comenzile de transport DSV, gata de printat.",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#f3f4f1", "theme_color": "#f3f4f1",
        "icons": [
            {"src": url_for("static", filename="icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": url_for("static", filename="icon-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    return app.response_class(json.dumps(data), mimetype="application/manifest+json")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not SITE_PASSWORD:
        return render_template("message.html", title="Lipsește parola site-ului",
                               text="Setează variabila SITE_PASSWORD pe server, ca nimeni altcineva să nu-ți poată citi mailul.")
    error = ""
    nxt = _safe_next(request.values.get("next", ""))
    if request.method == "POST":
        wait = _login_blocked()
        if wait:
            error = f"Prea multe parole greșite. Încearcă din nou peste {wait} minute."
        elif hmac.compare_digest(request.form.get("password", ""), SITE_PASSWORD):
            session.permanent = True
            session["ok"] = True
            return redirect(nxt or url_for("index"))
        else:
            _login_failed()
            error = "Parolă greșită."
    return render_template("login.html", error=error, next=nxt or "")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    mail_ready = bool(MAIL["user"] and MAIL["password"])
    searched = mail_ready and "days" in request.args
    days = max(1, min(365, request.args.get("days", 7, type=int)))
    sender = request.args.get("from", DEFAULT_FROM).strip()
    subject = request.args.get("subject", DEFAULT_SUBJECT).strip()
    messages, error = [], ""
    if searched:
        seen = _load_state()
        try:
            with Mailbox(**MAIL) as mb:
                messages = mb.list_messages(days=days, sender=sender, subject=subject)
                try:
                    seen |= set(state_from_journal(journal.read(mb=mb))["downloaded"])
                except Exception as e:
                    app.logger.warning("Nu am putut citi jurnalul descărcărilor: %s", e)
        except Exception as e:
            error = _friendly_error(e)
        for m in messages:
            m["downloaded"] = m["message_id"] in seen
    return render_template("index.html", messages=messages, searched=searched, error=error,
                           days=days, sender=sender, subject=subject, copies=COPIES, last=LAST,
                           account=MAIL["user"], mail_ready=mail_ready, default_from=DEFAULT_FROM, tab="acasa")


def _jobs_dir():
    d = Path(tempfile.gettempdir()) / "pagini-transport"
    d.mkdir(exist_ok=True)
    # Pregătirile mai vechi de o oră se șterg.
    for old in d.iterdir():
        if time.time() - old.stat().st_mtime > 3600:
            shutil.rmtree(old, ignore_errors=True)
    return d


def _job(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        abort(404)
    path = _jobs_dir() / token
    if not (path / "job.json").exists():
        abort(404)
    return path, json.loads((path / "job.json").read_text(encoding="utf-8"))


@app.route("/prepare", methods=["POST"])
@login_required
def prepare():
    """Descarcă și procesează mailurile bifate în fundal; pagina de progres arată „comanda 7 din 14”."""
    uids = request.form.getlist("uid")
    if not uids:
        return redirect(request.referrer or url_for("index"))
    labels = {u: (request.form.get(f"subj_{u}", u), request.form.get(f"mid_{u}", "")) for u in uids}

    def work(path):
        with Mailbox(**MAIL) as mb:
            _, problems = _job_from_mail(mb, labels, path=path)
        return problems, "Mailurile alese nu au PDF-uri.", {}

    return redirect(url_for("progress", token=_start_job(work, "Se pregătesc comenzile…")))


@app.route("/noi/start", methods=["POST"])
@login_required
def new_orders_start():
    """Spune aplicației când s-au descărcat ultima dată comenzile (ex. înainte să existe jurnalul):
    „Comenzi noi” va lua apoi și comenzile deschise în mail primite după acea zi."""
    try:
        day = date.fromisoformat(request.form.get("day", ""))
    except ValueError:
        return redirect(url_for("new_orders"))
    if day > datetime.now(RO).date():
        day = datetime.now(RO).date()
    start = datetime.combine(day, datetime.min.time(), RO)   # începutul zilei alese
    try:
        journal.update(lambda raw: record_download(raw, [], when=start))
    except Exception as e:
        return render_template("message.html", title="Nu am putut salva data", text=_friendly_error(e))
    return redirect(url_for("new_orders"))


@app.route("/noi")
@login_required
def new_orders():
    """Un singur pas: comenzile de descărcat → pagina de verificare (în fundal, cu progres).

    Necitite + cele deschise în mail dar probabil sărite (vezi new_orders.pick_new),
    fără cele descărcate deja de pe site."""
    if not (MAIL["user"] and MAIL["password"]):
        return redirect(url_for("index"))

    def work(path):
        info = []
        with Mailbox(**MAIL) as mb:
            _set_status(path, phase="Se caută comenzile noi în mail…")
            try:
                state = state_from_journal(journal.read(fresh=True, mb=mb))
            except Exception as e:
                app.logger.warning("Nu am putut citi jurnalul descărcărilor: %s", e)
                state = {"last_download": None, "downloaded": {}}
                info.append("Nu am putut citi jurnalul descărcărilor; am luat doar comenzile necitite.")
            last = state["last_download"]
            info.insert(0, f"Ultima descărcare de pe site: {when(last)}." if last else
                        "Prima descărcare de pe site: iau comenzile necitite și pe cele dintre ele.")
            nothing = {"title": "Nicio comandă nouă", "start_form": True,
                       "last_date": last.astimezone(RO).date().isoformat() if last else ""}
            found = mb.list_messages(days=NEW_ORDERS_DAYS, sender=DEFAULT_FROM, subject=DEFAULT_SUBJECT)
            picked = pick_new(found, state)
            if not picked:
                return [], (f"{info[0]} Nu există comenzi necitite sau nedescărcate "
                            f"în ultimele {NEW_ORDERS_DAYS} de zile."), nothing
            labels = {m["uid"]: (m["subject"], m["message_id"], describe(reason, last)) for m, reason in picked}
            _, problems = _job_from_mail(mb, labels, orders_only=True, info=info, path=path)
        return problems, f"{info[0]} Nu sunt comenzi noi de descărcat.", nothing

    return redirect(url_for("progress", token=_start_job(work, "Se caută comenzile noi în mail…")))


@app.route("/cauta")
@login_required
def search():
    """Caută una sau mai multe comenzi după număr (ex. „ARWY02628” sau „ARWY1, ARWY2”) în mailurile
    din ultimul an și le pregătește din nou — și pe cele vechi, citite sau descărcate."""
    numbers = [n.strip().upper() for n in re.split(r"[,\s;]+", request.args.get("nr", "")) if n.strip()][:30]
    if not numbers:
        return redirect(url_for("orders"))

    def work(path):
        with Mailbox(**MAIL) as mb:
            _set_status(path, phase=f"Se caută {', '.join(numbers[:3])}{' …' if len(numbers) > 3 else ''} în mail…")
            found = mb.list_messages(days=365, texts=numbers)
            labels = {m["uid"]: (m["subject"], m["message_id"]) for m in found}
            _, problems = _job_from_mail(mb, labels, orders_only=True, path=path, wanted=set(numbers))
        return problems, f"Nu am găsit {', '.join(numbers)} în mailurile din ultimul an.", \
            {"title": "Nu am găsit comanda", "back": url_for("orders")}

    return redirect(url_for("progress", token=_start_job(work, "Se caută în mail…")))


@app.route("/istoric")
@login_required
def history():
    try:
        sends = journal.read().get("sends") or []
    except Exception as e:
        return render_template("message.html", title="Nu am putut citi istoricul", text=_friendly_error(e))
    return render_template("history.html", sends=sends, tab="comenzi")


@app.route("/api/noi")
@login_required
def new_count():
    """Câte mailuri ar lua acum „Comenzi noi” (fără să le deschidă) — pentru numărul de pe buton."""
    if not (MAIL["user"] and MAIL["password"]):
        return {"count": 0}
    try:
        with Mailbox(**MAIL) as mb:
            state = state_from_journal(journal.read(mb=mb))
            found = mb.list_messages(days=NEW_ORDERS_DAYS, sender=DEFAULT_FROM, subject=DEFAULT_SUBJECT)
        return {"count": len(pick_new(found, state))}
    except Exception as e:
        return {"count": None, "error": _friendly_error(e)}, 503


def calendar_key():
    return hmac.new(str(app.secret_key).encode(), b"calendar", hashlib.sha256).hexdigest()[:32]


@app.route("/calendar.ics")
def calendar():
    """Calendarul comenzilor (încărcări, descărcări, termene pentru documente). Se abonează din
    telefon cu linkul care conține cheia — calendarul nu poate trimite parola site-ului."""
    if not hmac.compare_digest(request.args.get("key", ""), calendar_key()):
        abort(403)
    try:
        orders_ = list((journal.read().get("orders") or {}).values())
    except Exception:
        abort(503)

    def esc(text):
        return str(text).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Cirforvest//Comenzi//RO", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Cirforvest", "X-WR-TIMEZONE:Europe/Bucharest"]
    for o in orders_:
        no, truck = o.get("order_no"), o.get("truck") or ""
        st = registry.status(o)
        events = [("incarcare", _iso_day(o.get("loading_date")), f"Încărcare {no}", o.get("loading")),
                  ("descarcare", _iso_day(o.get("unloading_date")), f"Descărcare {no}", o.get("unloading"))]
        if not o.get("docs_sent") and st["deadline"]:
            events.append(("documente", st["deadline"], f"Termen documente {no}", "Documentele originale trebuie să ajungă la DSV"))
        for kind, day, title, where in events:
            if not day:
                continue
            lines += ["BEGIN:VEVENT", f"UID:{no}-{kind}@cirforvest", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                      f"SUMMARY:{esc(title + (' · ' + truck if truck else ''))}", f"LOCATION:{esc(where or '')}",
                      f"DESCRIPTION:{esc(url_for('order', no=no, _external=True))}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return app.response_class("\r\n".join(lines) + "\r\n", mimetype="text/calendar; charset=utf-8",
                              headers={"Content-Disposition": "inline; filename=cirforvest.ics"})


# --- Pregătiri în fundal, cu progres ------------------------------------------------------------
# Citirea mailului și procesarea PDF-urilor durează (zeci de secunde pe serverul gratuit). Rulează
# într-un fir separat; starea stă în status.json lângă pregătire, ca orice proces al serverului s-o
# poată citi, iar pagina de progres o întreabă din secundă în secundă.

def _set_status(path, **fields):
    f = path / "status.json"
    try:
        status = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {}
    status.update(fields)
    tmp = path / "status.tmp"
    tmp.write_text(json.dumps(status), encoding="utf-8")
    tmp.replace(f)


def _start_job(work, phase):
    """work(path) → (probleme, text_dacă_nu_iese_nimic, opțiuni pentru pagina de mesaj)."""
    path = _jobs_dir() / secrets.token_hex(16)
    path.mkdir()
    _set_status(path, state="running", phase=phase, done=0, total=0)

    def run():
        try:
            problems, empty_text, extra = work(path)
            if (path / "job.json").exists():
                _set_status(path, state="done")
            else:
                _set_status(path, **{"title": "Niciun PDF procesat", **extra, "state": "empty",
                                     "text": " ".join([empty_text, *problems]).strip()})
        except Exception as e:
            app.logger.exception("Pregătirea a eșuat")
            _set_status(path, state="error", title="Nu m-am putut conecta la mail", text=_friendly_error(e))

    threading.Thread(target=run, daemon=True).start()
    return path.name


def _status_path(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        abort(404)
    path = _jobs_dir() / token
    if not (path / "status.json").exists():
        abort(404)
    return path


@app.route("/progres/<token>")
@login_required
def progress(token):
    _status_path(token)
    return render_template("progress.html", token=token)


@app.route("/status/<token>")
@login_required
def status(token):
    path = _status_path(token)
    st = json.loads((path / "status.json").read_text(encoding="utf-8"))
    if st.get("state") == "done":
        st["next"] = url_for("review", token=token)
    elif st.get("state") in ("empty", "error"):
        st["next"] = url_for("result", token=token)
    return st


@app.route("/rezultat/<token>")
@login_required
def result(token):
    st = json.loads((_status_path(token) / "status.json").read_text(encoding="utf-8"))
    last = st.get("last_date")
    return render_template("message.html", title=st.get("title", ""), text=st.get("text", ""),
                           back=st.get("back") or url_for("index"), start_form=st.get("start_form"),
                           last_date=date.fromisoformat(last) if last else None)


def _job_from_mail(mb, labels, orders_only=False, info=None, path=None, wanted=None):
    """labels: {uid: (subiect, message_id[, notă])} → (token, probleme).

    Cu orders_only, PDF-urile care nu sunt comenzi de transport (facturi, situații…)
    sunt sărite — comanda se recunoaște după conținut, nu după subiectul mailului."""
    if path is None:
        path, items, problems = _new_job()
    else:
        items, problems = [], []
    skipped = []
    total = len(labels)
    for n, (uid, (subj, mid, *note)) in enumerate(labels.items()):
        _set_status(path, phase="Se pregătesc comenzile…", done=n, total=total)
        pdfs = mb.pdf_attachments(uid)
        if not pdfs and not orders_only:
            problems.append(f"Mailul „{subj}” nu are PDF atașat.")
            continue
        added = 0
        for name, data in pdfs:
            added += _add_item(path, items, problems, name, data, mid, uid, orders_only=orders_only,
                               note=note[0] if note else "", wanted=wanted)
        if orders_only and not added and not wanted:
            skipped.append(subj)
    _set_status(path, done=total, total=total)
    if skipped:
        listed = ", ".join(f"„{x}”" for x in skipped[:5]) + (" …" if len(skipped) > 5 else "")
        problems.append(f"Am sărit {len(skipped)} {'mail' if len(skipped) == 1 else 'mailuri'} fără comandă de "
                        f"transport recunoscută ({listed}); rămân necitite. Dacă e o comandă cu alt format, "
                        "deschide-o din „Caută în mail”.")
    return _save_job(path, items, problems, info), problems


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    """PDF-uri încărcate direct din telefon/calculator, fără mail."""
    uploads = [f for f in request.files.getlist("pdf") if f and f.filename]
    if not uploads:
        return redirect(url_for("index"))
    token, problems = _job_from_uploads(uploads)
    return _job_response(token, problems, "Fișierele alese nu sunt PDF-uri.")


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Primire din scurtătura „Trimite la Cirforvest” de pe iPhone.

    Primește PDF-urile (orice nume de câmp) și parola site-ului în câmpul
    `password`; răspunde cu linkul paginii de verificare, ca text simplu."""
    given = request.form.get("password") or request.headers.get("X-Password", "")
    wait = _login_blocked()
    if wait:
        return (f"Prea multe parole greșite. Încearcă din nou peste {wait} minute.", 429,
                {"Content-Type": "text/plain; charset=utf-8"})
    if not SITE_PASSWORD or not hmac.compare_digest(given, SITE_PASSWORD):
        _login_failed()
        return "Parolă greșită. Verifică parola din scurtătură.", 403, {"Content-Type": "text/plain; charset=utf-8"}
    uploads = [f for key in request.files for f in request.files.getlist(key) if f]
    if not uploads:
        return "Nu am primit niciun PDF.", 400, {"Content-Type": "text/plain; charset=utf-8"}
    token, problems = _job_from_uploads(uploads)
    if not token:
        return "Niciun PDF nu a putut fi procesat. " + " ".join(problems), 422, \
            {"Content-Type": "text/plain; charset=utf-8"}
    return url_for("review", token=token, _external=True), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/scurtatura")
@login_required
def shortcut_help():
    return render_template("shortcut.html", api_url=url_for("api_upload", _external=True))


def _job_from_uploads(uploads):
    path, items, problems = _new_job()
    for i, f in enumerate(uploads, 1):
        # Scurtăturile iOS trimit uneori fișierul fără nume sau fără extensie.
        name = safe_filename(Path(f.filename or f"document {i}.pdf").name)
        data = f.read()
        if not data.startswith(b"%PDF"):
            problems.append(f"{name}: nu e PDF.")
            continue
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        _add_item(path, items, problems, name, data)
    return _save_job(path, items, problems), problems


def _new_job():
    path = _jobs_dir() / secrets.token_hex(16)
    path.mkdir()
    return path, [], []


def _add_item(path, items, problems, name, data, message_id="", uid="", orders_only=False, note="", wanted=None):
    """Procesează un PDF și îl adaugă în pregătire. Întoarce 1 dacă a fost adăugat."""
    try:
        r = analyse(data, COPIES, LAST, name)   # o singură citire a PDF-ului
    except Exception as e:
        if not orders_only:
            problems.append(f"{name}: nu pot citi PDF-ul ({e}).")
        return 0
    if orders_only and not is_transport_order(r["text"]):
        return 0
    out, pages = r["out"], r["pages"]
    details = parse_order(r["text"])
    number = details["order_no"]
    if wanted and number not in wanted:
        return 0
    # Format necunoscut: o comandă din care nu putem citi ce contează trebuie verificată de om.
    missing = [label for key, label in (("price", "prețul"), ("loading_date", "data încărcării"),
                                        ("unloading", "locul descărcării"), ("truck", "camionul"))
               if is_transport_order(r["text"]) and not details.get(key)]
    (path / f"{len(items)}.pdf").write_bytes(out)
    (path / f"{len(items)}.orig.pdf").write_bytes(data)
    items.append({
        "source": name,
        "details": {k: v.isoformat() if isinstance(v, date) else v for k, v in details.items()},
        "summary": _summary(details),
        "order_no": number,
        "name": safe_filename(f"Comanda {number}" if number else name[:-4] + "_procesat"),
        "total": r["total"],
        "pages": pages,
        "message_id": message_id,
        "uid": uid,
        "note": note,
        "detail": r["detail"],
        "parse_warn": (f"Nu am putut citi din PDF: {', '.join(missing)}. Verifică comanda; poate DSV a schimbat "
                       "formatul.") if missing else "",
    })
    return 1


def _summary(d):
    """Rândul scurt de sub fiecare document, ex. „Dragomiresti Deal → Timisoara · 30.09 · 2.300,00 RON”."""
    # Doar orașele: „Altex Depozit Central, Dragomiresti Deal” → „Dragomiresti Deal”
    route = " → ".join(p.split(" → ")[i].rsplit(", ", 1)[-1]
                       for p, i in ((d["loading"], 0), (d["unloading"], -1)) if p)
    when = d["loading_date"].strftime("%d.%m") if d["loading_date"] else ""
    price = (f"{d['price']:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".") + f" {d['currency']}"
             if d["price"] is not None else "")
    toll = "Toll_RO se facturează separat" if d.get("toll_ro") else ""
    return " · ".join(x for x in (route, when, price, toll) if x)


def _annotate(items):
    """Compară comenzile pregătite cu registrul și între ele:
    - aceeași comandă de două ori în listă → rămâne cea mai nouă versiune, cealaltă e debifată;
    - comandă trimisă deja, neschimbată → „duplicat”, debifată;
    - versiune nouă de la DSV → „modificată”, cu ce s-a schimbat (rămâne bifată)."""
    raw = {}
    if MAIL["user"] and MAIL["password"]:
        try:
            raw = journal.read()
        except Exception as e:
            app.logger.warning("Nu am putut citi registrul: %s", e)
    best = {}
    for i, it in enumerate(items):
        no = it.get("order_no")
        if no:
            v = (it.get("details") or {}).get("version") or 1
            if no not in best or v >= ((items[best[no]].get("details") or {}).get("version") or 1):
                best[no] = i
    for i, it in enumerate(items):
        no = it.get("order_no")
        if not no:
            continue
        if best[no] != i:
            it["skip"] = True
            it["flag"] = ("dup", "Aceeași comandă apare de două ori în listă; am bifat doar versiunea cea mai nouă.")
            continue
        kind, changes, old = registry.compare(raw, it.get("details") or {})
        when_sent = (old or {}).get("sent_at", "")
        sent = f"{when_sent[8:10]}.{when_sent[5:7]}" if when_sent else ""
        if kind == "duplicat":
            it["skip"] = True
            it["flag"] = ("dup", f"Trimisă deja pe {sent}, neschimbată (duplicat). Bifeaz-o doar dacă vrei s-o printezi din nou.")
        elif kind == "modificat":
            v = (it.get("details") or {}).get("version") or 1
            diff = "; ".join(f"{label}: {registry.pretty(a)} → {registry.pretty(b)}" for label, a, b in changes) \
                or "fără diferențe în date"
            it["flag"] = ("changed", f"Comandă MODIFICATĂ de DSV (versiunea {v}, trimisă anterior pe {sent}): {diff}.")


def _save_job(path, items, problems, info=None):
    """Salvează pregătirea și întoarce token-ul ei, sau None dacă nu s-a procesat nimic."""
    if not items:
        if not (path / "status.json").exists():      # pregătirile în fundal își păstrează starea
            shutil.rmtree(path, ignore_errors=True)
        return None
    _annotate(items)
    (path / "job.json").write_text(json.dumps({"items": items, "problems": problems, "info": info or []}),
                                   encoding="utf-8")
    return path.name


def _job_response(token, problems, empty_text):
    if not token:
        return render_template("message.html", title="Niciun PDF procesat", text=" ".join(problems) or empty_text)
    return redirect(url_for("review", token=token))


# Paginile unei pregătiri sunt accesibile prin token-ul lor aleator (128 biți, expiră după o oră),
# ca scurtătura de pe iPhone să le poată deschide în Safari fără o nouă logare.
@app.route("/review/<token>")
def review(token):
    _, job = _job(token)
    today = date.today()
    for it in job["items"]:
        d = _iso_day((it.get("details") or {}).get("loading_date"))
        it["urgent"] = ({0: "Încărcare AZI", 1: "Încărcare mâine"}.get((d - today).days, "") if d else "")
    # În fiecare grup: după data încărcării, apoi după camion — ordinea în care se și printează.
    indexed = sorted(enumerate(job["items"]), key=lambda x: _print_key(x[1]))
    groups = [(title, docs) for title, docs in (
        ("Cu Toll_RO", [(i, it) for i, it in indexed if _is_toll(it)]),
        ("Fără Toll_RO", [(i, it) for i, it in indexed if not _is_toll(it)]),
    ) if docs]
    return render_template("review.html", token=token, items=job["items"], groups=groups,
                           grouped=len(groups) > 1, problems=job["problems"], info=job.get("info") or [],
                           mail_ready=bool(MAIL["user"] and MAIL["password"]), mail_user=MAIL["user"],
                           expected=EXPECTED_PAGES)


def _iso_day(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _print_key(item):
    d = item.get("details") or {}
    return (d.get("loading_date") or "9999", d.get("truck") or "", item.get("order_no") or "")


def _is_toll(item):
    """Comanda are mențiunea „taxa Toll_RO se va factura separat”."""
    return bool((item.get("details") or {}).get("toll_ro"))


@app.route("/file/<token>/<int:i>.pdf")
def job_file(token, i):
    path, job = _job(token)
    if not 0 <= i < len(job["items"]):
        abort(404)
    return send_file(path / f"{i}.pdf", mimetype="application/pdf")


@app.route("/file/<token>/<int:i>/sofer.pdf")
def driver_sheet(token, i):
    """Fișa pentru șofer: doar paginile cu detaliile transportului, o singură dată (fără termeni)."""
    path, job = _job(token)
    if not 0 <= i < len(job["items"]) or not (path / f"{i}.orig.pdf").exists():
        abort(404)
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(path / f"{i}.orig.pdf"))
    writer = PdfWriter()
    for k in range(max(1, job["items"][i].get("detail") or 1)):
        writer.add_page(reader.pages[k])
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     download_name=f"{job['items'][i]['name']} - sofer.pdf")


@app.route("/original/<token>/<int:i>.pdf")
def job_original(token, i):
    path, job = _job(token)
    if not 0 <= i < len(job["items"]) or not (path / f"{i}.orig.pdf").exists():
        abort(404)
    return send_file(path / f"{i}.orig.pdf", mimetype="application/pdf",
                     download_name=job["items"][i]["source"])


@app.route("/export/<token>", methods=["POST"])
@login_required
def export(token):
    path, job = _job(token)
    items = job["items"]
    chosen = [i for i in range(len(items)) if request.form.get(f"use_{i}")]
    # „Un singur PDF” se descarcă în două părți: comenzile cu Toll_RO și restul.
    group = request.form.get("group")
    if group in ("toll", "rest"):
        chosen = [i for i in chosen if _is_toll(items[i]) == (group == "toll")]
        if not chosen:
            abort(404)
    if not chosen:
        return redirect(url_for("review", token=token))
    names = {i: safe_filename(request.form.get(f"name_{i}", "").strip() or items[i]["name"]) for i in chosen}
    # Doar trimiterea pe mail (sau, fără mail, PDF-ul de printat) notează comenzile ca descărcate:
    # un Excel sau un ZIP scos pentru evidență nu înseamnă că s-au printat.
    if request.form.get("mode") not in ("mail", "xlsx", "zip", "trucks"):
        _mark_downloaded([items[i]["message_id"] for i in chosen])

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    if request.form.get("mode") == "mail":
        return _send_by_mail(path, items, chosen, names, token)
    if request.form.get("mode") == "xlsx":
        rows = []
        for i in chosen:
            d = dict(items[i].get("details") or {})
            for k in ("order_date", "loading_date", "unloading_date"):
                d[k] = date.fromisoformat(d[k]) if d.get(k) else None
            d["order_no"] = d.get("order_no") or names[i]
            d["file"] = names[i] + ".pdf"
            rows.append(d)
        return send_file(io.BytesIO(build_register(rows)), as_attachment=True,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         download_name=f"registru_transporturi_{stamp}.xlsx")
    if request.form.get("mode") == "trucks":
        by_truck = {}
        for i in sorted(chosen, key=lambda i: _print_key(items[i])):
            by_truck.setdefault((items[i].get("details") or {}).get("truck") or "fara_camion", []).append(i)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for truck, idx in by_truck.items():
                z.writestr(f"{safe_filename(truck)}.pdf", merge([(path / f"{i}.pdf").read_bytes() for i in idx]))
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"transporturi_pe_camion_{stamp}.zip")
    if request.form.get("mode") == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            used = set()
            for i in chosen:
                folder = "Toll_RO/" if _is_toll(items[i]) else ""
                fname, n = f"{folder}{names[i]}.pdf", 2
                while fname in used:
                    fname, n = f"{folder}{names[i]} ({n}).pdf", n + 1
                used.add(fname)
                z.write(path / f"{i}.pdf", fname)
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"transporturi_{stamp}.zip")

    toll = " - Toll_RO" if group == "toll" else ""
    if len(chosen) == 1:
        return send_file(path / f"{chosen[0]}.pdf", mimetype="application/pdf",
                         download_name=f"{names[chosen[0]]}{toll}.pdf")
    data = merge([(path / f"{i}.pdf").read_bytes() for i in chosen])
    prefix = "transporturi_Toll_RO" if group == "toll" else "transporturi"
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=f"{prefix}_{stamp}.pdf")


def _send_by_mail(path, items, chosen, names, token):
    """„Trimite pe mail”: două mailuri în Inbox — întâi comenzile cu Toll_RO (dacă există), apoi restul."""
    now = datetime.now(RO)
    chosen = sorted(chosen, key=lambda i: _print_key(items[i]))
    groups = [(label, [i for i in chosen if _is_toll(items[i]) == toll])
              for label, toll in (("Toll_RO", True), ("fără Toll_RO", False))]
    groups = [(label, idx) for label, idx in groups if idx]
    sent = []
    try:
        with Mailbox(**MAIL) as mb:
            for n, (label, idx) in enumerate(groups):
                fname = (f"transporturi_Toll_RO_{now:%Y-%m-%d_%H%M}.pdf" if label == "Toll_RO"
                         else f"transporturi_{now:%Y-%m-%d_%H%M}.pdf")
                pdf = merge([(path / f"{i}.pdf").read_bytes() for i in idx])
                lines = [f"- {items[i].get('order_no') or names[i]}"
                         + (f" · {items[i]['summary']}" if items[i].get("summary") else "") for i in idx]
                subject = (f"Comenzi {label} - {len(idx)} "
                           f"{'comandă' if len(idx) == 1 else 'comenzi'} - {now:%d.%m.%Y %H:%M}")
                text = (f"{len(idx)} {'comandă' if len(idx) == 1 else 'comenzi'} {label}, gata de printat "
                        f"(detaliile x2 + termenii și condițiile):\n\n" + "\n".join(lines)
                        + "\n\nTrimis de Cirforvest.")
                # Toll_RO primul; restul cu o secundă mai târziu, ca ordinea din Inbox să fie clară.
                mb.deliver_to_inbox(MAIL["user"], subject, text, [(fname, pdf)], when=now.timestamp() + n)
                sent.append(f"{subject} ({fname})")
    except Exception as e:
        return render_template("message.html", title="Nu am putut trimite pe mail",
                               text=(f"Trimise: {'; '.join(sent)}. " if sent else "") + _friendly_error(e),
                               back=url_for("review", token=token))
    _mark_downloaded([items[i]["message_id"] for i in chosen])
    _record_download([items[i] for i in chosen])
    return render_template("message.html", title="Trimis pe mail",
                           text=f"Am pus {len(sent)} {'mail' if len(sent) == 1 else 'mailuri'} în Inbox la "
                                f"{MAIL['user']}: " + "; ".join(sent) + ".",
                           back=url_for("review", token=token))


def _record_download(items):
    """După trimitere: comenzile intră în registru și în jurnal (cu data descărcării), iar mailurile
    devin „citite”. O eroare aici nu trebuie să oprească trimiterea."""
    if not (MAIL["user"] and MAIL["password"]):
        return
    uids = [it.get("uid") for it in items if it.get("uid")]
    message_ids = [it["message_id"] for it in items if it.get("message_id")]
    details = [it.get("details") or {} for it in items]
    user = session.get("user", "")

    def change(raw):
        raw = record_download(raw, message_ids)
        registry.upsert(raw, details, user=user)
        return registry.add_send(raw, [d.get("order_no") for d in details if d.get("order_no")],
                                 sum(1 for d in details if d.get("toll_ro")), user=user)
    try:
        with Mailbox(**MAIL) as mb:
            journal.update(change, mb=mb)
            if uids:
                mb.mark_read(uids)
    except Exception as e:
        app.logger.warning("Nu am putut nota descărcarea în mail: %s", e)


def _friendly_error(e):
    text = str(e)
    if "AUTHENTICATIONFAILED" in text.upper() or "INVALID CREDENTIALS" in text.upper():
        where = ("myaccount.google.com/apppasswords" if "gmail" in MAIL["server"]
                 else "login.yahoo.com → Securitatea contului → Generează parolă de aplicație")
        return ("Serverul de mail a refuzat autentificarea. Verifică adresa și folosește o parolă de aplicație "
                f"({where}), nu parola obișnuită.")
    return f"Eroare: {text}"


import views_orders  # noqa: E402,F401  (paginile registrului; au nevoie de app și de funcțiile de mai sus)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
