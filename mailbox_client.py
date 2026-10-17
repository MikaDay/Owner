"""Acces IMAP pentru aplicația web: listează mailurile și scoate PDF-urile.

Listarea citește doar antetele (rapid chiar și cu sute de mailuri);
mesajul complet se descarcă doar pentru mailurile alese.
"""

import email
import imaplib
import re
from datetime import date, timedelta
from email.utils import parsedate_to_datetime

from fetch_mail import _MONTHS, _decode, _safe_name

_HEADERS = "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID CONTENT-TYPE)]"


class Mailbox:
    def __init__(self, server, port, user, password, folder="INBOX"):
        self.imap = imaplib.IMAP4_SSL(server, port, timeout=60)
        self.imap.login(user, password)
        self.folder = folder
        typ, data = self.imap.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Nu pot deschide folderul {folder}: {data}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.imap.logout()
        except Exception:
            pass

    def list_messages(self, days=7, sender="", subject="", limit=300, unread_only=False):
        """Mailurile (cele mai noi primele) care pot avea atașamente."""
        crit = ["UNSEEN"] if unread_only else []
        if days:
            d = date.today() - timedelta(days=days)
            crit += ["SINCE", f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"]
        if sender:
            crit += ["FROM", f'"{sender}"']
        typ, data = self.imap.uid("SEARCH", None, *(crit or ["ALL"]))
        if typ != "OK":
            raise RuntimeError(f"Căutarea a eșuat: {data}")
        uids = data[0].split()[::-1][:limit]
        if not uids:
            return []

        typ, data = self.imap.uid("FETCH", b",".join(uids), f"(UID FLAGS {_HEADERS})")
        if typ != "OK":
            raise RuntimeError(f"Citirea mailurilor a eșuat: {data}")
        out = []
        for item in data:
            if not isinstance(item, tuple):
                continue
            m = re.search(rb"UID (\d+)", item[0])
            if not m:
                continue
            hdr = email.message_from_bytes(item[1])
            subj = _decode(hdr.get("Subject"))
            if subject and subject.lower() not in subj.lower():
                continue
            # Mailurile fără atașamente sunt de obicei text/plain sau multipart/alternative.
            ctype = (hdr.get("Content-Type") or "").lower()
            if not (ctype.startswith("multipart/mixed") or "pdf" in ctype):
                continue
            try:
                when = parsedate_to_datetime(hdr.get("Date"))
            except Exception:
                when = None
            flags = re.search(rb"FLAGS \(([^)]*)\)", item[0])
            out.append({
                "uid": m.group(1).decode(),
                "unread": not (flags and b"\\Seen" in flags.group(1)),
                "subject": subj or "(fără subiect)",
                "from": _decode(hdr.get("From")),
                "date": when,
                "message_id": (hdr.get("Message-ID") or "").strip(),
            })
        out.sort(key=lambda x: x["date"].timestamp() if x["date"] else 0, reverse=True)
        return out

    def mark_read(self, uids):
        """Marchează mailurile ca citite (doar asta se modifică în căsuța de mail)."""
        uids = [str(u) for u in uids if str(u).isdigit()]
        if not uids:
            return
        self.imap.select(f'"{self.folder}"', readonly=False)
        self.imap.uid("STORE", ",".join(uids), "+FLAGS", "(\\Seen)")

    def pdf_attachments(self, uid):
        """[(nume_fisier, bytes)] pentru toate PDF-urile din mailul `uid`."""
        typ, data = self.imap.uid("FETCH", str(uid).encode(), "(BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return []
        msg = email.message_from_bytes(data[0][1])
        found = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = _decode(part.get_filename())
            if part.get_content_type() != "application/pdf" and not filename.lower().endswith(".pdf"):
                continue
            payload = part.get_payload(decode=True)
            if payload:
                name = _safe_name(filename or "atasament.pdf")
                if not name.lower().endswith(".pdf"):
                    name += ".pdf"
                found.append((name, payload))
        return found
