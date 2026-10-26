"""Acces IMAP pentru aplicația web: listează mailurile și scoate PDF-urile.

Listarea citește doar antetele (rapid chiar și cu sute de mailuri);
mesajul complet se descarcă doar pentru mailurile alese.
"""

import email
import imaplib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from fetch_mail import _MONTHS, _decode, _safe_name

_HEADERS = "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID CONTENT-TYPE)]"

# Memoria aplicației stă în căsuța de mail (serverul gratuit își pierde fișierele la repornire):
# folderul „Cirforvest” conține un singur mesaj cu ce s-a descărcat și când.
STATE_FOLDER = "Cirforvest"
STATE_SUBJECT = "Cirforvest - jurnal descarcari (nu sterge)"
STATE_KEEP_DAYS = 180


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

    def _select(self, folder, readonly=True):
        typ, data = self.imap.select(f'"{folder}"', readonly=readonly)
        return typ == "OK"

    def load_state(self):
        """{"last_download": datetime|None, "downloaded": {message_id: iso_data}} din folderul Cirforvest."""
        state = {"last_download": None, "downloaded": {}}
        try:
            if not self._select(STATE_FOLDER):
                return state                     # încă nu s-a descărcat nimic de pe site
            typ, data = self.imap.uid("SEARCH", None, "ALL")
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            if uids:
                typ, data = self.imap.uid("FETCH", uids[-1], "(BODY.PEEK[])")
                if typ == "OK" and data and isinstance(data[0], tuple):
                    body = email.message_from_bytes(data[0][1]).get_payload(decode=True) or b"{}"
                    raw = json.loads(body.decode("utf-8"))
                    last = raw.get("last_download")
                    state = {"last_download": datetime.fromisoformat(last) if last else None,
                             "downloaded": dict(raw.get("downloaded") or {})}
        finally:
            self._select(self.folder)
        return state

    def save_download(self, message_ids, when=None):
        """Adaugă comenzile descărcate acum în jurnal și ține minte momentul descărcării."""
        when = when or datetime.now(timezone.utc)
        state = self.load_state()
        cutoff = (when - timedelta(days=STATE_KEEP_DAYS)).isoformat()
        downloaded = {k: v for k, v in state["downloaded"].items() if v >= cutoff}
        downloaded.update({mid: when.isoformat() for mid in message_ids if mid})
        msg = EmailMessage()
        msg["Subject"] = STATE_SUBJECT
        msg["From"] = "Cirforvest <noreply@cirforvest.local>"
        msg.set_content(json.dumps({"last_download": when.isoformat(), "downloaded": downloaded}, indent=1))
        self.imap.create(f'"{STATE_FOLDER}"')        # „există deja” e un răspuns normal
        typ, data = self.imap.append(f'"{STATE_FOLDER}"', "(\\Seen)", imaplib.Time2Internaldate(time.time()),
                                     msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(f"Nu pot salva jurnalul în folderul {STATE_FOLDER}: {data}")
        try:
            # Păstrează doar ultimul mesaj-jurnal.
            if self._select(STATE_FOLDER, readonly=False):
                typ, data = self.imap.uid("SEARCH", None, "ALL")
                old = data[0].split()[:-1] if typ == "OK" and data and data[0] else []
                if old:
                    self.imap.uid("STORE", b",".join(old), "+FLAGS", "(\\Deleted)")
                    self.imap.expunge()
        finally:
            self._select(self.folder)

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
