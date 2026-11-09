"""Jurnalul aplicației (folderul „Cirforvest” din Yahoo), cu o copie locală de scurtă durată.

Citirea din Yahoo durează 1–2 secunde, așa că paginile folosesc o copie salvată în /tmp (comună
pentru toate procesele serverului), reîmprospătată la cel mult CACHE_SECONDS. Scrierile se fac
sub un lacăt de fișier: citește jurnalul proaspăt din Yahoo, aplică schimbarea, scrie la loc —
două salvări în același timp nu se mai pot suprascrie una pe alta.
"""

import fcntl
import json
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

CACHE_SECONDS = 120
CACHE_FILE = Path(tempfile.gettempdir()) / "cirforvest-journal.json"
LOCK_FILE = Path(tempfile.gettempdir()) / "cirforvest-journal.lock"

_mailbox_factory = None


def configure(mailbox_factory):
    """mailbox_factory() → un Mailbox deschis (folosit cu `with`)."""
    global _mailbox_factory
    _mailbox_factory = mailbox_factory


def clear_cache():
    CACHE_FILE.unlink(missing_ok=True)


def _write_cache(raw):
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": time.time(), "data": raw}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


@contextmanager
def _locked():
    with open(LOCK_FILE, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read(fresh=False, mb=None):
    """Jurnalul ca dicționar. `mb`: un Mailbox deja deschis, ca să nu deschidem altă conexiune."""
    if not fresh:
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - cached["at"] < CACHE_SECONDS:
                return cached["data"]
        except (OSError, ValueError, KeyError):
            pass
    if mb is not None:
        raw = mb.load_journal()
    else:
        with _mailbox_factory() as box:
            raw = box.load_journal()
    _write_cache(raw)
    return raw


def update(change, mb=None):
    """change(raw) → raw nou (sau modifică raw pe loc și întoarce None). Întoarce jurnalul salvat."""
    with _locked():
        if mb is not None:
            return _update(change, mb)
        with _mailbox_factory() as box:
            return _update(change, box)


def _update(change, mb):
    raw = mb.load_journal()
    new = change(raw)
    raw = raw if new is None else new
    mb.save_journal(raw)
    _write_cache(raw)
    return raw
