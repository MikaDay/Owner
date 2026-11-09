"""Acces IMAP pentru aplicația web: listează mailurile și scoate PDF-urile.

Listarea citește doar antetele (rapid chiar și cu sute de mailuri);
mesajul complet se descarcă doar pentru mailurile alese.
"""

import email
import email.policy
import email.utils
import imaplib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from fetch_mail import _MONTHS, _decode, _safe_name

_HEADERS = "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID CONTENT-TYPE X-CIRFORVEST)]"
APP_HEADER = "X-Cirforvest"   # pe mailurile puse de aplicație în Inbox, ca să nu le ia drept comenzi

# Memoria aplicației stă în căsuța de mail (serverul gratuit își pierde fișierele la repornire):
# folderul „Cirforvest” conține un singur mesaj cu ce s-a descărcat și când.
STATE_FOLDER = "Cirforvest"
STATE_SUBJECT = "Cirforvest - jurnal descarcari (nu sterge)"
STATE_KEEP_DAYS = 180


def state_from_journal(raw):
    last = raw.get("last_download")
    return {"last_download": datetime.fromisoformat(last) if last else None,
            "downloaded": dict(raw.get("downloaded") or {})}


def record_download(raw, message_ids, when=None):
    """Jurnalul cu descărcarea de acum adăugată (mailurile mai vechi de 180 de zile se uită)."""
    when = when or datetime.now(timezone.utc)
    cutoff = (when - timedelta(days=STATE_KEEP_DAYS)).isoformat()
    downloaded = {k: v for k, v in (raw.get("downloaded") or {}).items() if v >= cutoff}
    downloaded.update({mid: when.isoformat() for mid in message_ids if mid})
    return {**raw, "last_download": when.isoformat(), "downloaded": downloaded}


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

    def list_messages(self, days=7, sender="", subject="", limit=300, unread_only=False, texts=()):
        """Mailurile (cele mai noi primele) care pot avea atașamente.
        `texts`: caută mailurile care conțin oricare dintre texte (ex. numere de comandă)."""
        crit = ["UNSEEN"] if unread_only else []
        if days:
            d = date.today() - timedelta(days=days)
            crit += ["SINCE", f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"]
        if sender:
            crit += ["FROM", f'"{sender}"']
        found = set()
        for extra in ([["TEXT", f'"{t}"'] for t in texts if re.fullmatch(r"[\w./-]{3,40}", t)] if texts else [[]]):
            typ, data = self.imap.uid("SEARCH", None, *((crit + extra) or ["ALL"]))
            if typ != "OK":
                raise RuntimeError(f"Căutarea a eșuat: {data}")
            found |= set(data[0].split())
        uids = sorted(found, key=int)[::-1][:limit]
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
            if hdr.get(APP_HEADER):
                continue
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

    def load_journal(self):
        """Tot jurnalul din folderul Cirforvest, ca dicționar (gol dacă nu există încă)."""
        try:
            if not self._select(STATE_FOLDER):
                return {}                        # încă nu s-a descărcat nimic de pe site
            typ, data = self.imap.uid("SEARCH", None, "ALL")
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            if not uids:
                return {}
            typ, data = self.imap.uid("FETCH", uids[-1], "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                return {}
            body = email.message_from_bytes(data[0][1]).get_payload(decode=True) or b"{}"
            return json.loads(body.decode("utf-8"))
        finally:
            self._select(self.folder)

    def save_journal(self, raw):
        """Scrie jurnalul ca un singur mesaj în folderul Cirforvest (mesajele vechi se șterg)."""
        msg = EmailMessage()
        msg["Subject"] = STATE_SUBJECT
        msg["From"] = "Cirforvest <noreply@cirforvest.local>"
        msg.set_content(json.dumps(raw, ensure_ascii=False, indent=1))
        self.imap.create(f'"{STATE_FOLDER}"')        # „există deja” e un răspuns normal
        typ, data = self.imap.append(f'"{STATE_FOLDER}"', "(\\Seen)", imaplib.Time2Internaldate(time.time()),
                                     msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(f"Nu pot salva jurnalul în folderul {STATE_FOLDER}: {data}")
        try:
            if self._select(STATE_FOLDER, readonly=False):
                typ, data = self.imap.uid("SEARCH", None, "ALL")
                old = data[0].split()[:-1] if typ == "OK" and data and data[0] else []
                if old:
                    self.imap.uid("STORE", b",".join(old), "+FLAGS", "(\\Deleted)")
                    self.imap.expunge()
        finally:
            self._select(self.folder)

    def load_state(self):
        """{"last_download": datetime|None, "downloaded": {message_id: iso_data}} din jurnal."""
        return state_from_journal(self.load_journal())

    def save_download(self, message_ids, when=None):
        """Adaugă comenzile descărcate acum în jurnal și ține minte momentul descărcării."""
        raw = self.load_journal()
        self.save_journal(record_download(raw, message_ids, when))

    def deliver_to_inbox(self, address, subject, text, attachments, when=None):
        """Pune un mail nou (necitit) cu atașamente direct în Inbox-ul contului.

        Serverul gratuit Render nu are voie să trimită mailuri (SMTP blocat), dar
        destinatarul e chiar contul conectat, așa că mailul se adaugă prin IMAP."""
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"Cirforvest <{address}>"
        msg["To"] = address
        msg["Date"] = email.utils.formatdate(when, localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain="cirforvest.local")
        msg[APP_HEADER] = "1"
        msg.set_content(text)
        for name, data, *mime in attachments:          # (nume, conținut[, „tip/subtip”]); implicit PDF
            maintype, subtype = (mime[0] if mime else "application/pdf").split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        typ, data = self.imap.append(f'"{self.folder}"', "()", imaplib.Time2Internaldate(when or time.time()),
                                     msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(f"Yahoo nu a acceptat mailul: {data}")

    def attachment_names(self, uids):
        """{uid: [nume fișiere]} fără să descarce mailurile (doar structura lor, BODYSTRUCTURE)."""
        uids = [str(u).encode() if not isinstance(u, bytes) else u for u in uids]
        out = {}
        for i in range(0, len(uids), 50):
            typ, data = self.imap.uid("FETCH", b",".join(uids[i:i + 50]), "(UID BODYSTRUCTURE)")
            if typ != "OK":
                continue
            current = None
            for item in data:
                chunk = b"".join(x for x in (item if isinstance(item, tuple) else (item,)) if isinstance(x, bytes))
                m = re.search(rb"UID (\d+)", chunk)
                if m:
                    current = m.group(1).decode()
                    out.setdefault(current, [])
                if current:
                    for name in re.findall(rb'"(?:name|filename)" "([^"]+)"', chunk, re.I):
                        out[current].append(_decode(name.decode("utf-8", "replace")))
        return out

    def read_message(self, uid):
        """Un mail întreg, pentru afișare: antete, textul și toate atașamentele (fără să-l marcheze citit)."""
        typ, data = self.imap.uid("FETCH", str(uid).encode(), "(BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
        text, html_text, files = "", "", []
        for part in msg.walk():
            if part.is_multipart():
                continue
            filename = part.get_filename()
            if filename or part.get_content_disposition() == "attachment":
                files.append((_safe_name(filename or "atasament"), part.get_content_type(),
                              part.get_payload(decode=True) or b""))
            elif part.get_content_type() == "text/plain" and not text:
                text = part.get_content()
            elif part.get_content_type() == "text/html" and not html_text:
                html_text = part.get_content()
        if not text and html_text:              # doar HTML: scoatem textul, fără etichete
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.S | re.I)
            text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\n\s*\n+", "\n\n", __import__("html").unescape(text))
        return {"subject": msg.get("Subject", ""), "from": msg.get("From", ""), "date": msg.get("Date", ""),
                "text": text.strip(), "files": files}

    def attachments(self, uid, exts):
        """[(nume_fisier, bytes)] pentru atașamentele cu una din extensiile `exts` (ex. (".pdf", ".xlsx"))."""
        typ, data = self.imap.uid("FETCH", str(uid).encode(), "(BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return []
        msg = email.message_from_bytes(data[0][1])
        found = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = _decode(part.get_filename())
            named = filename.lower().endswith(tuple(exts))
            is_pdf = not named and part.get_content_type() == "application/pdf"
            if not named and not (is_pdf and ".pdf" in exts):
                continue
            payload = part.get_payload(decode=True)
            if payload:
                name = _safe_name(filename or "atasament.pdf")
                if is_pdf and not name.lower().endswith(".pdf"):
                    name += ".pdf"
                found.append((name, payload))
        return found

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
