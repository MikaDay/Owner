"""Server IMAP în memorie, cu foldere, steaguri, APPEND și EXPUNGE — destul cât să testăm
aplicația fără un cont de mail real."""

import email.utils
import imaplib
import re
from datetime import datetime, timedelta
from email.message import EmailMessage


class FakeServer:
    def __init__(self):
        self.folders = {"INBOX": {}}
        self.flags = {}
        self.next_uid = 100
        self.fail_login = False

    def add(self, folder, raw, seen=False):
        self.next_uid += 1
        uid = str(self.next_uid).encode()
        self.folders.setdefault(folder, {})[uid] = raw
        self.flags[(folder, uid)] = {b"\\Seen"} if seen else set()
        return uid

    def add_mail(self, subject, pdfs, day=0, seen=False, sender="ilie.rumega@dsv.com", msg_id=None):
        m = EmailMessage()
        m["Subject"] = subject
        m["From"] = sender
        m["Message-ID"] = msg_id or email.utils.make_msgid(domain="dsv.test")
        m["Date"] = email.utils.format_datetime((datetime.now() - timedelta(days=10 - day)).astimezone())
        m.set_content("fw")
        for name, data in pdfs:
            m.add_attachment(data, maintype="application", subtype="pdf", filename=name)
        return self.add("INBOX", m.as_bytes(), seen=seen)

    def is_seen(self, uid, folder="INBOX"):
        return b"\\Seen" in self.flags[(folder, uid)]


SERVER = FakeServer()


class FakeIMAP:
    def __init__(self, *a, **k):
        self.cur, self.ro = None, True

    def login(self, user, password):
        if SERVER.fail_login:
            raise imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] LOGIN Invalid credentials'")

    def logout(self):
        pass

    def select(self, folder, readonly=False):
        folder = folder.strip('"')
        if folder not in SERVER.folders:
            return "NO", [b"no such folder"]
        self.cur, self.ro = folder, readonly
        return "OK", [str(len(SERVER.folders[folder])).encode()]

    def create(self, folder):
        folder = folder.strip('"')
        if folder in SERVER.folders:
            return "NO", [b"exists"]
        SERVER.folders[folder] = {}
        return "OK", [b""]

    def append(self, folder, flags, date_time, msg):
        SERVER.add(folder.strip('"'), msg, seen="Seen" in flags)
        return "OK", [b""]

    def expunge(self):
        box = SERVER.folders[self.cur]
        for uid in [u for u in box if b"\\Deleted" in SERVER.flags[(self.cur, u)]]:
            del box[uid]
        return "OK", [b""]

    def uid(self, cmd, *args):
        box = SERVER.folders[self.cur]
        if cmd == "SEARCH":
            uids = sorted(box, key=int)
            if "UNSEEN" in args:
                uids = [u for u in uids if b"\\Seen" not in SERVER.flags[(self.cur, u)]]
            text = [args[i + 1].strip('"') for i, a in enumerate(args) if a in ("TEXT", "SUBJECT")]
            if text:
                uids = [u for u in uids if any(t.encode() in box[u] for t in text)]
            return "OK", [b" ".join(uids)]
        ids = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
        if cmd == "STORE":
            assert not self.ro, "STORE pe un folder deschis doar pentru citire"
            flag = re.search(r"\\\w+", args[2]).group(0).encode()
            for uid in ids.split(b","):
                SERVER.flags[(self.cur, uid)].add(flag)
            return "OK", [b""]
        out = []
        for uid in ids.split(b","):
            raw = box[uid]
            body = raw.split(b"\n\n")[0] if "HEADER" in args[1] else raw
            flags = b" ".join(sorted(SERVER.flags[(self.cur, uid)]))
            out += [(b"1 (UID %s FLAGS (%s) BODY[] {%d}" % (uid, flags, len(body)), body), b")"]
        return "OK", out


def install():
    imaplib.IMAP4_SSL = FakeIMAP
