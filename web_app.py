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
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, redirect, render_template, request, send_file,
                   session, url_for)

from mailbox_client import Mailbox
from process_pdf import merge, order_number, page_count, process_bytes, safe_filename

app = Flask(__name__)
# Fără SECRET_KEY, cheia e derivată din parole, ca toate procesele serverului să o folosească pe aceeași.
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    ("transport:" + os.environ.get("SITE_PASSWORD", "") + os.environ.get("MAIL_PASSWORD", "")).encode()).hexdigest()
app.config.update(SESSION_COOKIE_SECURE=os.environ.get("HTTPS", "1") == "1",
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
    if not (MAIL["user"] and MAIL["password"]):
        return render_template("message.html", title="Mailul nu e configurat",
                               text="Setează MAIL_USER și MAIL_PASSWORD (parola de aplicație Yahoo) pe server.")
    searched = "days" in request.args
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
                           account=MAIL["user"])


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

    token = secrets.token_hex(16)
    path = _jobs_dir() / token
    path.mkdir()
    items, problems = [], []
    try:
        with Mailbox(**MAIL) as mb:
            for uid in uids:
                pdfs = mb.pdf_attachments(uid)
                if not pdfs:
                    problems.append(f"Mailul „{request.form.get(f'subj_{uid}', uid)}” nu are PDF atașat.")
                    continue
                for name, data in pdfs:
                    try:
                        out, pages = process_bytes(data, COPIES, LAST, name)
                    except Exception as e:
                        problems.append(f"{name}: nu pot citi PDF-ul ({e}).")
                        continue
                    number = order_number(data)
                    i = len(items)
                    (path / f"{i}.pdf").write_bytes(out)
                    items.append({
                        "source": name,
                        "order_no": number,
                        "name": safe_filename(f"Comanda {number}" if number else name[:-4] + "_procesat"),
                        "total": page_count(data),
                        "pages": pages,
                        "message_id": request.form.get(f"mid_{uid}", ""),
                    })
    except Exception as e:
        shutil.rmtree(path, ignore_errors=True)
        return render_template("message.html", title="Nu m-am putut conecta la mail", text=_friendly_error(e))

    if not items:
        shutil.rmtree(path, ignore_errors=True)
        return render_template("message.html", title="Niciun PDF procesat",
                               text=" ".join(problems) or "Mailurile alese nu au PDF-uri.")
    (path / "job.json").write_text(json.dumps({"items": items, "problems": problems}), encoding="utf-8")
    return redirect(url_for("review", token=token))


@app.route("/review/<token>")
@login_required
def review(token):
    _, job = _job(token)
    return render_template("review.html", token=token, items=job["items"], problems=job["problems"],
                           expected=EXPECTED_PAGES)


@app.route("/file/<token>/<int:i>.pdf")
@login_required
def job_file(token, i):
    path, job = _job(token)
    if not 0 <= i < len(job["items"]):
        abort(404)
    return send_file(path / f"{i}.pdf", mimetype="application/pdf")


@app.route("/export/<token>", methods=["POST"])
@login_required
def export(token):
    path, job = _job(token)
    items = job["items"]
    chosen = [i for i in range(len(items)) if request.form.get(f"use_{i}")]
    if not chosen:
        return redirect(url_for("review", token=token))
    names = {i: safe_filename(request.form.get(f"name_{i}", "").strip() or items[i]["name"]) for i in chosen}
    _mark_downloaded([items[i]["message_id"] for i in chosen])

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
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


def _friendly_error(e):
    text = str(e)
    if "AUTHENTICATIONFAILED" in text.upper() or "INVALID CREDENTIALS" in text.upper():
        return ("Yahoo a refuzat autentificarea. Verifică adresa și folosește o parolă de aplicație "
                "(login.yahoo.com → Securitatea contului → Generează parolă de aplicație), nu parola obișnuită.")
    return f"Eroare: {text}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
