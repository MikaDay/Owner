"""Ce face serverul singur, chemat periodic (la ~30 de minute) de un ceas extern — GitHub Actions.

La fiecare apel („tick”), cel mult o dată la 10 minute:
- verifică dacă au venit comenzi noi și trimite o notificare pe telefoane (doar pentru cele nemaianunțate);
- dacă parola de aplicație Yahoo nu mai merge, anunță (o dată pe zi);
- la ora aleasă: rezumatul zilei (documente de trimis, ePOD, plăți restante) și, dacă e pornită,
  trimiterea automată a comenzilor noi pe mail;
- pe 1 ale lunii: registrul lunii trecute, ca mail în Inbox.

Setările stau în jurnal, la "settings"; ce s-a făcut deja, la "cron" și "notify".
"""

import json
import os
import secrets
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import journal
import registry
import webpush
from new_orders import RO, pick_new

TICK_FILE = Path(tempfile.gettempdir()) / "cirforvest-tick"
MIN_TICK_SECONDS = 10 * 60
DEFAULTS = {"notify_new": True, "notify_daily": True, "daily_hour": 7, "auto_send": False,
            "monthly_report": True}
MONTHS = ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie",
          "octombrie", "noiembrie", "decembrie"]


def settings(raw):
    return {**DEFAULTS, **(raw.get("settings") or {})}


def vapid():
    return os.environ.get("VAPID_PRIVATE_KEY", ""), os.environ.get("VAPID_PUBLIC_KEY", "")


def push_all(raw, message, sender=webpush.send):
    """Trimite notificarea pe toate telefoanele abonate; abonamentele moarte se șterg.
    Întoarce (câte au primit, endpoint-uri de șters)."""
    private, public = vapid()
    if not (private and public):
        return 0, []
    ok, gone = 0, []
    for sub in raw.get("push") or []:
        try:
            code = sender(sub, message, private, public)
        except Exception:
            continue
        if code in (404, 410):
            gone.append(sub["endpoint"])
        elif 200 <= code < 300:
            ok += 1
    return ok, gone


def drop_subscriptions(endpoints):
    if endpoints:
        journal.update(lambda raw: {**raw, "push": [s for s in raw.get("push") or [] if s["endpoint"] not in endpoints]})


def notify(raw, title, body, url="/", tag="cirforvest", sender=webpush.send):
    ok, gone = push_all(raw, {"title": title, "body": body, "url": url, "tag": tag}, sender)
    drop_subscriptions(gone)
    return ok


def throttled(now=None):
    """True dacă ultimul tick a fost acum mai puțin de 10 minute (atunci nu facem nimic)."""
    now = now or time.time()
    try:
        if now - float(TICK_FILE.read_text()) < MIN_TICK_SECONDS:
            return True
    except (OSError, ValueError):
        pass
    TICK_FILE.write_text(str(now))
    return False


def tick(app_hooks, now=None, sender=webpush.send):
    """app_hooks: funcțiile aplicației web de care e nevoie (mailbox, pregătire, trimitere).
    Întoarce un rezumat scurt (pentru jurnalul serverului)."""
    now = now or datetime.now(RO)
    done = []
    try:
        with app_hooks["mailbox"]() as mb:
            raw = journal.read(fresh=True, mb=mb)
            cfg = settings(raw)
            from mailbox_client import state_from_journal
            found = mb.list_messages(days=app_hooks["days"], sender=app_hooks["sender"])
            picked = pick_new(found, state_from_journal(raw))
    except Exception as e:
        text = str(e)
        if "AUTHENTICATIONFAILED" in text.upper() or "INVALID CREDENTIALS" in text.upper():
            raw = journal.read() if journal.CACHE_FILE.exists() else {}
            cron = raw.get("cron") or {}
            if cron.get("auth_alert") != now.date().isoformat():
                notify(raw, "Mailul nu mai merge", "Yahoo a refuzat parola de aplicație. Fă una nouă și pune-o în Render.",
                       tag="auth", sender=sender)
                try:
                    journal.update(lambda r: {**r, "cron": {**(r.get("cron") or {}), "auth_alert": now.date().isoformat()}})
                except Exception:
                    pass
            return ["eroare autentificare mail"]
        raise

    cron = dict(raw.get("cron") or {})
    notified = set((raw.get("notify") or {}).get("seen") or [])
    today = now.date().isoformat()

    # 1) Comenzi noi, anunțate o singură dată.
    fresh = [m for m, _ in picked if m["message_id"] and m["message_id"] not in notified]
    if fresh and cfg["notify_new"]:
        n = len(picked)
        body = "; ".join(m["subject"] for m in fresh[:3]) + (" …" if len(fresh) > 3 else "")
        notify(raw, f"{n} {'comandă nouă' if n == 1 else 'comenzi noi'}", body, url="/noi", tag="noi", sender=sender)
        done.append(f"notificare: {len(fresh)} noi")

    # 2) La ora aleasă: trimiterea automată, apoi rezumatul zilei.
    if now.hour >= int(cfg["daily_hour"]) and cron.get("daily") != today:
        cron["daily"] = today
        if cfg["auto_send"] and picked:
            sent_n, toll_n = app_hooks["auto_send"]()
            if sent_n:
                notify(raw, f"Am trimis {sent_n} {'comandă' if sent_n == 1 else 'comenzi'} pe mail",
                       f"{toll_n} cu Toll_RO · le găsești în Inbox, gata de printat." if toll_n else
                       "Le găsești în Inbox, gata de printat.", url="/istoric", tag="trimis", sender=sender)
                done.append(f"trimise automat: {sent_n}")
                raw = journal.read(fresh=True)
        if cfg["notify_daily"]:
            s = registry.summary(raw, now.date())
            parts = []
            if s["docs_due"]:
                parts.append(f"{s['docs_due']} documente de trimis" + (f" ({s['docs_late']} întârziate)" if s["docs_late"] else ""))
            if s["epod_open"]:
                parts.append(f"{s['epod_open']} ePOD de completat")
            if s["overdue"]:
                parts.append(f"restant {registry.money(s['overdue'])}")
            if s["loading_soon"]:
                parts.append(f"{len(s['loading_soon'])} încărcări azi/mâine")
            if parts:
                notify(raw, "Azi", " · ".join(parts), url="/comenzi", tag="azi", sender=sender)
                done.append("rezumat zilnic")

    # 3) Pe 1 ale lunii (sau prima verificare de după): registrul lunii trecute, în Inbox.
    last_month_end = now.date().replace(day=1) - timedelta(days=1)
    month_key = last_month_end.strftime("%Y-%m")
    if cfg["monthly_report"] and cron.get("monthly") != month_key and now.hour >= int(cfg["daily_hour"]):
        cron["monthly"] = month_key
        n = app_hooks["monthly_report"](last_month_end.replace(day=1), last_month_end)
        if n:
            notify(raw, f"Registrul pe {MONTHS[last_month_end.month - 1]}", f"{n} comenzi · Excel-ul e în Inbox.",
                   url="/rapoarte", tag="lunar", sender=sender)
            done.append(f"raport lunar {month_key}")

    seen = list(notified | {m["message_id"] for m in fresh})[-500:]
    journal.update(lambda r: {**r, "cron": {**(r.get("cron") or {}), **cron},
                              "notify": {**(r.get("notify") or {}), "seen": seen}})
    return done or ["nimic nou"]
