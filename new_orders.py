"""Care comenzi din mail trebuie descărcate („Comenzi noi”).

Pe lângă mailurile necitite, ia și comenzile deschise în Yahoo dar niciodată
descărcate de pe site, când există un motiv să credem că au fost sărite:

- au venit după ultima descărcare de pe site (ex. ultima dată duminică → tot ce
  a venit de duminică încoace), sau
- stau între comenzi necitite (două deschise una după alta, iar înainte și după
  ele totul e necitit).

Comenzile deja descărcate de pe site (ținute minte în jurnalul din mail) nu
sunt luate niciodată.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

RO = ZoneInfo("Europe/Bucharest")

UNREAD = "necitit"
AFTER_LAST = "dupa_descarcare"
BETWEEN = "intre_necitite"


def _ts(m):
    d = m.get("date")
    if d is None:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def pick_new(messages, state):
    """messages: ca din Mailbox.list_messages; state: ca din Mailbox.load_state.

    Întoarce [(mesaj, motiv)] în ordine cronologică; motiv ∈ {UNREAD, AFTER_LAST, BETWEEN}."""
    downloaded = set(state.get("downloaded") or {})
    last = state.get("last_download")
    last_ts = last.timestamp() if last else None

    msgs = sorted(messages, key=_ts)
    pending = [m["message_id"] not in downloaded or not m["message_id"] for m in msgs]
    unread_idx = [i for i, m in enumerate(msgs) if m["unread"] and pending[i]]
    first_unread, last_unread = (unread_idx[0], unread_idx[-1]) if unread_idx else (None, None)

    picked = []
    for i, m in enumerate(msgs):
        if not pending[i]:
            continue
        if m["unread"]:
            picked.append((m, UNREAD))
        elif last_ts is not None and _ts(m) > last_ts:
            picked.append((m, AFTER_LAST))
        elif first_unread is not None and first_unread < i < last_unread:
            picked.append((m, BETWEEN))
    return picked


def describe(reason, last=None):
    """Textul afișat lângă o comandă inclusă deși era deschisă în mail."""
    if reason == AFTER_LAST and last:
        return f"Deschisă în mail, dar a venit după ultima descărcare ({when(last)}) — verifică dacă ai printat-o."
    if reason == BETWEEN:
        return "Deschisă în mail, dar stă între comenzi necitite — poate n-a fost descărcată."
    return ""


DAYS = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]


def when(dt):
    """„duminică 25.10 la 18:30” (ora României)."""
    local = dt.astimezone(RO) if dt.tzinfo else dt
    return f"{DAYS[local.weekday()]} {local:%d.%m} la {local:%H:%M}"
