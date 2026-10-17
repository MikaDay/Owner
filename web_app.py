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
import time
import zipfile
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, redirect, render_template, request, send_file,
                   session, url_for)

from werkzeug.middleware.proxy_fix import ProxyFix

from mailbox_client import Mailbox
from process_pdf import first_page_text, merge, page_count, process_bytes, safe_filename
from transport_order import build_register, is_transport_order, parse_order

app = Flask(__name__)
# Render pune serverul în spatele unui proxy HTTPS; așa linkurile generate încep cu https://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
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
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
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
            return redirect(url_for("login"))
        return view(*a, **kw)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if not SITE_PASSWORD:
        return render_template("message.html", title="Lipsește parola site-ului",
                               text="Setează variabila SITE_PASSWORD pe server, ca nimeni altcineva să nu-ți poată citi mailul.")
    error = ""
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), SITE_PASSWORD):
            session.permanent = True
            session["ok"] = True
            return redirect(url_for("index"))
        error = "Parolă greșită."
    return render_template("login.html", error=error)


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
        try:
            with Mailbox(**MAIL) as mb:
                messages = mb.list_messages(days=days, sender=sender, subject=subject)
        except Exception as e:
            error = _friendly_error(e)
        seen = _load_state()
        for m in messages:
            m["downloaded"] = m["message_id"] in seen
    return render_template("index.html", messages=messages, searched=searched, error=error,
                           days=days, sender=sender, subject=subject, copies=COPIES, last=LAST,
                           account=MAIL["user"], mail_ready=mail_ready, default_from=DEFAULT_FROM)


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
    """Descarcă și procesează mailurile bifate, apoi arată pagina de verificare."""
    uids = request.form.getlist("uid")
    if not uids:
        return redirect(request.referrer or url_for("index"))
    labels = {u: (request.form.get(f"subj_{u}", u), request.form.get(f"mid_{u}", "")) for u in uids}
    try:
        with Mailbox(**MAIL) as mb:
            token, problems = _job_from_mail(mb, labels)
    except Exception as e:
        return render_template("message.html", title="Nu m-am putut conecta la mail", text=_friendly_error(e))
    return _job_response(token, problems, "Mailurile alese nu au PDF-uri.")


@app.route("/noi")
@login_required
def new_orders():
    """Un singur pas: toate comenzile necitite din ultimele 30 de zile → pagina de verificare."""
    if not (MAIL["user"] and MAIL["password"]):
        return redirect(url_for("index"))
    try:
        with Mailbox(**MAIL) as mb:
            found = mb.list_messages(days=30, sender=DEFAULT_FROM, subject=DEFAULT_SUBJECT, unread_only=True)
            if not found:
                return render_template("message.html", title="Nicio comandă nouă",
                                       text="Nu există comenzi necitite în ultimele 30 de zile. "
                                            "Comenzile deja descărcate de aici sunt marcate ca citite în mail.",
                                       back=url_for("index"))
            token, problems = _job_from_mail(mb, {m["uid"]: (m["subject"], m["message_id"]) for m in found},
                                             orders_only=True)
    except Exception as e:
        return render_template("message.html", title="Nu m-am putut conecta la mail", text=_friendly_error(e))
    if not token:
        return render_template("message.html", title="Nicio comandă nouă",
                               text="Mailurile necitite nu conțin comenzi de transport. " + " ".join(problems),
                               back=url_for("index"))
    return _job_response(token, problems, "")


def _job_from_mail(mb, labels, orders_only=False):
    """labels: {uid: (subiect, message_id)} → (token, probleme).

    Cu orders_only, PDF-urile care nu sunt comenzi de transport (facturi, situații…)
    sunt sărite — comanda se recunoaște după conținut, nu după subiectul mailului."""
    path, items, problems = _new_job()
    skipped = 0
    for uid, (subj, mid) in labels.items():
        pdfs = mb.pdf_attachments(uid)
        if orders_only:
            pdfs = [(n, d) for n, d in pdfs if is_transport_order(first_page_text(d))]
            if not pdfs:
                skipped += 1
                continue
        if not pdfs:
            problems.append(f"Mailul „{subj}” nu are PDF atașat.")
            continue
        for name, data in pdfs:
            _add_item(path, items, problems, name, data, mid, uid)
    if skipped:
        problems.append(f"Am sărit {skipped} mailuri care nu conțin o comandă de transport "
                        "(rămân necitite în mail).")
    return _save_job(path, items, problems), problems


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
    if not SITE_PASSWORD or not hmac.compare_digest(given, SITE_PASSWORD):
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


def _add_item(path, items, problems, name, data, message_id="", uid=""):
    try:
        out, pages = process_bytes(data, COPIES, LAST, name)
    except Exception as e:
        problems.append(f"{name}: nu pot citi PDF-ul ({e}).")
        return
    details = parse_order(first_page_text(data))
    number = details["order_no"]
    (path / f"{len(items)}.pdf").write_bytes(out)
    items.append({
        "source": name,
        "details": {k: v.isoformat() if isinstance(v, date) else v for k, v in details.items()},
        "summary": _summary(details),
        "order_no": number,
        "name": safe_filename(f"Comanda {number}" if number else name[:-4] + "_procesat"),
        "total": page_count(data),
        "pages": pages,
        "message_id": message_id,
        "uid": uid,
    })


def _summary(d):
    """Rândul scurt de sub fiecare document, ex. „Dragomiresti Deal → Timisoara · 30.09 · 2.300,00 RON”."""
    # Doar orașele: „Altex Depozit Central, Dragomiresti Deal” → „Dragomiresti Deal”
    route = " → ".join(p.split(" → ")[i].rsplit(", ", 1)[-1]
                       for p, i in ((d["loading"], 0), (d["unloading"], -1)) if p)
    when = d["loading_date"].strftime("%d.%m") if d["loading_date"] else ""
    price = (f"{d['price']:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".") + f" {d['currency']}"
             if d["price"] is not None else "")
    return " · ".join(x for x in (route, when, price) if x)


def _save_job(path, items, problems):
    """Salvează pregătirea și întoarce token-ul ei, sau None dacă nu s-a procesat nimic."""
    if not items:
        shutil.rmtree(path, ignore_errors=True)
        return None
    (path / "job.json").write_text(json.dumps({"items": items, "problems": problems}), encoding="utf-8")
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
    return render_template("review.html", token=token, items=job["items"], problems=job["problems"],
                           expected=EXPECTED_PAGES)


@app.route("/file/<token>/<int:i>.pdf")
def job_file(token, i):
    path, job = _job(token)
    if not 0 <= i < len(job["items"]):
        abort(404)
    return send_file(path / f"{i}.pdf", mimetype="application/pdf")


@app.route("/export/<token>", methods=["POST"])
def export(token):
    path, job = _job(token)
    items = job["items"]
    chosen = [i for i in range(len(items)) if request.form.get(f"use_{i}")]
    if not chosen:
        return redirect(url_for("review", token=token))
    names = {i: safe_filename(request.form.get(f"name_{i}", "").strip() or items[i]["name"]) for i in chosen}
    _mark_downloaded([items[i]["message_id"] for i in chosen])
    _mark_read([items[i].get("uid") for i in chosen])

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
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
    if request.form.get("mode") == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            used = set()
            for i in chosen:
                fname, n = names[i] + ".pdf", 2
                while fname in used:
                    fname, n = f"{names[i]} ({n}).pdf", n + 1
                used.add(fname)
                z.write(path / f"{i}.pdf", fname)
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"transporturi_{stamp}.zip")

    if len(chosen) == 1:
        return send_file(path / f"{chosen[0]}.pdf", mimetype="application/pdf",
                         download_name=names[chosen[0]] + ".pdf")
    data = merge([(path / f"{i}.pdf").read_bytes() for i in chosen])
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=f"transporturi_{stamp}.pdf")


def _mark_read(uids):
    """După descărcare, mailurile devin „citite”, ca butonul „Comenzi noi” să nu le mai ia.
    O eroare aici nu trebuie să oprească descărcarea."""
    uids = [u for u in uids if u]
    if not uids or not (MAIL["user"] and MAIL["password"]):
        return
    try:
        with Mailbox(**MAIL) as mb:
            mb.mark_read(uids)
    except Exception as e:
        app.logger.warning("Nu am putut marca mailurile ca citite: %s", e)


def _friendly_error(e):
    text = str(e)
    if "AUTHENTICATIONFAILED" in text.upper() or "INVALID CREDENTIALS" in text.upper():
        where = ("myaccount.google.com/apppasswords" if "gmail" in MAIL["server"]
                 else "login.yahoo.com → Securitatea contului → Generează parolă de aplicație")
        return ("Serverul de mail a refuzat autentificarea. Verifică adresa și folosește o parolă de aplicație "
                f"({where}), nu parola obișnuită.")
    return f"Eroare: {text}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
