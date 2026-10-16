"""Descarcă atașamentele PDF din căsuța de mail prin IMAP."""

import email
import imaplib
import json
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from pathlib import Path

STATE_FILE = Path(".processed.json")
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _decode(value):
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _safe_name(name):
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip(" .")
    return name or "atasament.pdf"


def _load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def _save_state(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=1), encoding="utf-8")


def _search_criteria(sender, days, unseen_only):
    crit = []
    if unseen_only:
        crit.append("UNSEEN")
    if days:
        d = date.today() - timedelta(days=days)
        # IMAP cere luna în engleză, indiferent de limba sistemului.
        since = f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"
        crit += ["SINCE", since]
    if sender:
        crit += ["FROM", f'"{sender}"']
    return crit or ["ALL"]


def fetch_pdfs(server, port, user, password, folder, download_dir,
               sender="", subject="", days=30, unseen_only=False):
    """Descarcă PDF-urile noi și returnează lista căilor salvate.

    Mailurile deja procesate (după Message-ID) sunt ținute minte în
    .processed.json, ca să nu fie descărcate de două ori.
    """
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    seen = _load_state()
    saved = []

    with imaplib.IMAP4_SSL(server, port) as imap:
        imap.login(user, password)
        imap.select(f'"{folder}"', readonly=True)
        crit = _search_criteria(sender, days, unseen_only)
        typ, data = imap.search(None, *crit)
        if typ != "OK":
            raise RuntimeError(f"Căutarea IMAP a eșuat: {data}")

        for num in data[0].split():
            typ, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            msg_id = msg.get("Message-ID") or f"{folder}:{num.decode()}"
            if msg_id in seen:
                continue
            # Subiectul e filtrat aici (nu pe server) ca să meargă și cu diacritice.
            if subject and subject.lower() not in _decode(msg.get("Subject")).lower():
                continue

            for part in msg.walk():
                filename = _decode(part.get_filename())
                is_pdf = (part.get_content_type() == "application/pdf"
                          or filename.lower().endswith(".pdf"))
                if not is_pdf or part.get_content_maintype() == "multipart":
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                name = _safe_name(filename or "atasament.pdf")
                if not name.lower().endswith(".pdf"):
                    name += ".pdf"
                path = _unique_path(download_dir / name)
                path.write_bytes(payload)
                saved.append(path)
                print(f"  descărcat: {path.name}  (din: {_decode(msg.get('Subject'))})")

            seen.add(msg_id)
            _save_state(seen)

    return saved


def _unique_path(path):
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 2
    while (candidate := path.with_name(f"{stem} ({i}){suffix}")).exists():
        i += 1
    return candidate
