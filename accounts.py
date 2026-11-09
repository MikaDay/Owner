"""Conturi: administratorul (parola din SITE_PASSWORD, schimbabilă din Setări) și utilizatori
cu parola lor (ex. dispecer, contabil), plus cheia scurtăturii de pe iPhone.

Parolele se păstrează doar ca hash (werkzeug: scrypt/pbkdf2), în jurnalul din Yahoo.
"""

import hmac
import re
import secrets
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

ADMIN = "admin"
NAME_RE = re.compile(r"^[a-z0-9._-]{2,30}$")
MIN_PASSWORD = 8


def normalize(name):
    return (name or "").strip().lower()


def check_login(raw, site_password, username, password):
    """(utilizator, e_admin) dacă parola e bună, altfel None."""
    name = normalize(username)
    users = raw.get("users") or {}
    if name and name != ADMIN and name in users:
        u = users[name]
        return (name, bool(u.get("admin"))) if check_password_hash(u["hash"], password or "") else None
    if name in ("", ADMIN):
        if raw.get("admin_hash"):
            ok = check_password_hash(raw["admin_hash"], password or "")
        else:
            ok = bool(site_password) and hmac.compare_digest(password or "", site_password)
        return (ADMIN, True) if ok else None
    return None


def validate_password(password, repeat=None):
    if len(password or "") < MIN_PASSWORD:
        return f"Parola trebuie să aibă cel puțin {MIN_PASSWORD} caractere."
    if repeat is not None and password != repeat:
        return "Cele două parole nu sunt la fel."
    return ""


def set_password(raw, user, password):
    h = generate_password_hash(password)
    if user == ADMIN:
        return {**raw, "admin_hash": h}
    users = dict(raw.get("users") or {})
    users[user] = {**users[user], "hash": h}
    return {**raw, "users": users}


def add_user(raw, name, password, admin=False):
    name = normalize(name)
    users = dict(raw.get("users") or {})
    users[name] = {"hash": generate_password_hash(password), "admin": bool(admin),
                   "created": datetime.now().isoformat(timespec="seconds")}
    return {**raw, "users": users}


def validate_new_user(raw, name):
    name = normalize(name)
    if not NAME_RE.match(name) or name == ADMIN:
        return "Numele: 2–30 de litere mici, cifre, punct, minus sau _ (fără „admin”)."
    if name in (raw.get("users") or {}):
        return "Există deja un utilizator cu numele ăsta."
    return ""


def remove_user(raw, name):
    users = {k: v for k, v in (raw.get("users") or {}).items() if k != normalize(name)}
    return {**raw, "users": users}


def new_shortcut_key(raw):
    """Cheie nouă pentru scurtătura iPhone (se vede o singură dată). Întoarce (cheie, jurnal)."""
    key = secrets.token_urlsafe(18)
    return key, {**raw, "shortcut_key_hash": generate_password_hash(key)}


def check_shortcut_key(raw, key):
    h = raw.get("shortcut_key_hash")
    return bool(h and key and check_password_hash(h, key))
