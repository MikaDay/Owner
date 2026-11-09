"""Notificări, setări, trimitere automată și verificarea periodică (/cron/tick)."""

import hmac
import io
import json
import os
import threading
from datetime import datetime

from flask import abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for

import automation
import journal
from mailbox_client import Mailbox
from transport_order import build_register
from views_orders import REGISTER_EXTRA, _in_period, _orders, _register_rows
from web_app import (DEFAULT_FROM, MAIL, NEW_ORDERS_DAYS, _friendly_error, _jobs_dir, app, collect_new_orders,
                     deliver_orders, login_required)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --- Ce face automat serverul --------------------------------------------------------------------

def auto_send():
    """Trimiterea automată: aceleași comenzi pe care le-ar lua „Comenzi noi”, fără duplicate."""
    import secrets
    path = _jobs_dir() / secrets.token_hex(16)
    path.mkdir()
    collect_new_orders(path)
    job_file = path / "job.json"
    if not job_file.exists():
        return 0, 0
    items = json.loads(job_file.read_text(encoding="utf-8"))["items"]
    chosen = [i for i, it in enumerate(items) if not it.get("skip")]
    if not chosen:
        return 0, 0
    deliver_orders(path, items, chosen, {i: items[i]["name"] for i in chosen}, user="automat")
    return len(chosen), sum(1 for i in chosen if (items[i].get("details") or {}).get("toll_ro"))


def monthly_report(start, end):
    """Registrul unei luni, ca mail cu Excel în Inbox. Întoarce câte comenzi a avut luna."""
    raw = journal.read(fresh=True)
    rows = _register_rows([o for o in _orders(raw) if _in_period(o, start, end)])
    if not rows:
        return 0
    month = automation.MONTHS[start.month - 1]
    data = build_register(rows, extra_columns=REGISTER_EXTRA)
    with Mailbox(**MAIL) as mb:
        mb.deliver_to_inbox(MAIL["user"], f"Registru transporturi - {month} {start.year} - {len(rows)} comenzi",
                            f"Registrul comenzilor din {month} {start.year}: {len(rows)} comenzi.\n\nTrimis de Cirforvest.",
                            [(f"registru_{start:%Y-%m}.xlsx", data, XLSX)])
    return len(rows)


HOOKS = {"mailbox": lambda: Mailbox(**MAIL), "days": NEW_ORDERS_DAYS, "sender": DEFAULT_FROM,
         "auto_send": auto_send, "monthly_report": monthly_report}


@app.route("/cron/tick", methods=["GET", "POST"])
def cron_tick():
    """Chemat la ~30 de minute de GitHub Actions. Dacă e setat CRON_TOKEN, cererea trebuie să-l aibă.
    Fără el, oricine poate doar declanșa ce ar face oricum ceasul, cel mult o dată la 10 minute."""
    token = os.environ.get("CRON_TOKEN", "")
    given = request.headers.get("X-Cron-Token", "") or request.args.get("token", "")
    if token and not hmac.compare_digest(given, token):
        abort(403)
    if not (MAIL["user"] and MAIL["password"]):
        return "mailul nu e configurat\n", 200
    if automation.throttled():
        return "prea devreme\n", 200
    if request.args.get("sync") == "1":            # pentru teste
        return "\n".join(automation.tick(HOOKS)) + "\n"

    def run():
        try:
            app.logger.info("tick: %s", automation.tick(HOOKS))
        except Exception:
            app.logger.exception("tick a eșuat")
    threading.Thread(target=run, daemon=True).start()
    return "ok\n", 202


@app.route("/ping")
def ping():
    return "ok\n", 200, {"Cache-Control": "no-store"}


# --- Service worker (notificări + pornire rapidă) -------------------------------------------------

@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# --- Setări și abonarea la notificări --------------------------------------------------------------

def _read_raw():
    try:
        return journal.read(), ""
    except Exception as e:
        return {}, _friendly_error(e)


@app.route("/setari", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        new = {"notify_new": bool(request.form.get("notify_new")), "notify_daily": bool(request.form.get("notify_daily")),
               "auto_send": bool(request.form.get("auto_send")), "monthly_report": bool(request.form.get("monthly_report")),
               "daily_hour": max(5, min(12, request.form.get("daily_hour", 7, type=int)))}
        try:
            journal.update(lambda raw: {**raw, "settings": {**(raw.get("settings") or {}), **new}})
        except Exception as e:
            return render_template("message.html", title="Nu am putut salva", text=_friendly_error(e),
                                   back=url_for("settings"))
        return redirect(url_for("settings", salvat=1))
    raw, error = _read_raw()
    vapid_public = ""
    if not error:
        try:
            vapid_public = automation.ensure_vapid(raw)
        except Exception as e:
            error = _friendly_error(e)
    return render_template("settings.html", cfg=automation.settings(raw), error=error,
                           devices=len(raw.get("push") or []), vapid_public=vapid_public,
                           saved=request.args.get("salvat"), cron=raw.get("cron") or {}, tab="setari",
                           **settings_extra(raw))


def settings_extra(raw):
    """Secțiunile adăugate de alte module (utilizatori, scurtătură) — înlocuită acolo."""
    return {}


@app.route("/push/abonare", methods=["POST"])
@login_required
def push_subscribe():
    sub = request.get_json(silent=True) or {}
    if not (isinstance(sub.get("endpoint"), str) and sub["endpoint"].startswith("https://")
            and (sub.get("keys") or {}).get("p256dh") and sub["keys"].get("auth")):
        return jsonify({"error": "abonament invalid"}), 400
    entry = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
             "user": session.get("user", ""), "at": datetime.now().isoformat(timespec="seconds"),
             "device": request.headers.get("User-Agent", "")[:120]}
    journal.update(lambda raw: {**raw, "push": [s for s in raw.get("push") or [] if s["endpoint"] != entry["endpoint"]]
                                + [entry]})
    return jsonify({"ok": True})


@app.route("/push/dezabonare", methods=["POST"])
@login_required
def push_unsubscribe():
    endpoint = (request.get_json(silent=True) or {}).get("endpoint", "")
    journal.update(lambda raw: {**raw, "push": [s for s in raw.get("push") or [] if s["endpoint"] != endpoint]})
    return jsonify({"ok": True})


@app.route("/push/test", methods=["POST"])
@login_required
def push_test():
    raw, error = _read_raw()
    if error:
        return jsonify({"error": error}), 503
    n = automation.notify(raw, "Cirforvest", "Notificările merg. Așa vei afla de comenzile noi.", url="/", tag="test")
    return jsonify({"sent": n})
