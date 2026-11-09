"""Paginile registrului: comenzile trimise, ce mai e de făcut (ePOD, documente, plată),
registrul Excel cumulat, datele pentru facturare și rapoartele."""

import csv
import io
from collections import defaultdict
from datetime import date, datetime, timezone

from flask import abort, jsonify, redirect, render_template, request, send_file, session, url_for

import journal
import registry
from mailbox_client import Mailbox
from process_pdf import analyse
from transport_order import build_register, is_transport_order, parse_order
from web_app import (COPIES, DEFAULT_FROM, LAST, MAIL, _friendly_error, _set_status, _start_job, app,
                     login_required)


def _today():
    return date.today()


def _read():
    try:
        return journal.read(), ""
    except Exception as e:
        app.logger.warning("Nu am putut citi jurnalul: %s", e)
        return {}, _friendly_error(e)


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _orders(raw):
    return list((raw.get("orders") or {}).values())


def _route(o):
    """„Dragomiresti Deal → Timisoara” (doar orașele)."""
    def city(p, i):
        return p.split(" → ")[i].rsplit(", ", 1)[-1] if p else "?"
    return f"{city(o.get('loading'), 0)} → {city(o.get('unloading'), -1)}"


def _period():
    """Perioada din ?de=…&pana=… (implicit luna curentă)."""
    today = _today()
    start = _day(request.args.get("de")) or today.replace(day=1)
    end = _day(request.args.get("pana")) or today
    return start, end


def _in_period(o, start, end):
    d = _day(o.get("unloading_date")) or _day(o.get("loading_date")) or _day(o.get("sent_at"))
    return d is not None and start <= d <= end


# --- Lista comenzilor ---------------------------------------------------------------------------

@app.route("/comenzi")
@login_required
def orders():
    raw, error = _read()
    today = _today()
    show = request.args.get("f", "todo")
    q = request.args.get("q", "").strip().lower()
    rows = []
    for o in _orders(raw):
        st = registry.status(o, today)
        if show == "todo" and not st["open"]:
            continue
        hay = " ".join(str(o.get(k) or "") for k in ("order_no", "loading", "unloading", "truck", "dsv_ref")).lower()
        if q and q not in hay:
            continue
        rows.append((o, st))
    # Întâi ce e întârziat, apoi cele mai noi.
    rows.sort(key=lambda r: (not r[1]["late"], -(_day(r[0].get("loading_date")) or date.min).toordinal()))
    return render_template("orders.html", rows=rows, show=show, q=request.args.get("q", ""), error=error,
                           summary=registry.summary(raw, today), money=registry.money, route=_route,
                           empty=not raw.get("orders"), tab="comenzi")


@app.route("/comanda/<no>", methods=["GET", "POST"])
@login_required
def order(no):
    if request.method == "POST":
        action = request.form.get("action", "")

        def change(raw):
            o = (raw.get("orders") or {}).get(no)
            if not o:
                abort(404)
            if action == "epod":
                step = request.form.get("step")
                if step in dict(registry.EPOD_STEPS):
                    steps = o.setdefault("epod_steps", {})
                    steps[step] = None if steps.get(step) else datetime.now(timezone.utc).isoformat()
            elif action in ("docs_sent", "paid"):
                value = request.form.get("value", "")
                o[action] = value if _day(value) else None
            elif action == "toll_amount":
                try:
                    o["toll_amount"] = round(float(request.form.get("value", "").replace(",", ".")), 2)
                except ValueError:
                    o["toll_amount"] = None
            elif action == "note":
                o["note"] = request.form.get("value", "")[:1000]
        try:
            journal.update(change)
        except Exception as e:
            return render_template("message.html", title="Nu am putut salva", text=_friendly_error(e),
                                   back=url_for("order", no=no))
        return redirect(url_for("order", no=no) + (f"#{action}" if action else ""))

    raw, error = _read()
    o = (raw.get("orders") or {}).get(no)
    if not o:
        if error:
            return render_template("message.html", title="Nu am putut citi registrul", text=error,
                                   back=url_for("orders"))
        abort(404)
    st = registry.status(o, _today())
    return render_template("order.html", o=o, st=st, steps=registry.EPOD_STEPS, route=_route(o),
                           today=_today().isoformat(), money=registry.money, tab="comenzi")


@app.route("/api/rezumat")
@login_required
def summary_api():
    raw, error = _read()
    if error:
        return jsonify({"error": error}), 503
    s = registry.summary(raw, _today())
    return jsonify({
        "orders": s["orders"], "docs_due": s["docs_due"], "docs_late": s["docs_late"], "epod_open": s["epod_open"],
        "unpaid": registry.money(s["unpaid"]) if s["unpaid"] else "", "overdue": registry.money(s["overdue"]) if s["overdue"] else "",
        "loading_soon": [{"no": o["order_no"], "route": _route(o), "date": o.get("loading_date"), "truck": o.get("truck")}
                         for o in s["loading_soon"]],
    })


# --- Comenzile mai vechi, aduse din mail în registru ---------------------------------------------

@app.route("/comenzi/import", methods=["POST"])
@login_required
def orders_import():
    """Citește comenzile DSV din ultimele N zile și le pune în registru (fără să le marcheze citite
    sau descărcate) — ca termenele și plățile să poată fi urmărite și pentru ce s-a printat înainte."""
    days = max(1, min(180, request.form.get("days", 60, type=int)))
    user = session.get("user", "")

    def work(path):
        found_details = []
        with Mailbox(**MAIL) as mb:
            _set_status(path, phase="Se caută comenzile în mail…")
            found = mb.list_messages(days=days, sender=DEFAULT_FROM, limit=500)
            for n, m in enumerate(found):
                _set_status(path, phase="Se citesc comenzile…", done=n, total=len(found))
                for name, data in mb.pdf_attachments(m["uid"]):
                    try:
                        r = analyse(data, COPIES, LAST, name)
                    except Exception:
                        continue
                    if is_transport_order(r["text"]):
                        found_details.append((m, parse_order(r["text"])))
            _set_status(path, done=len(found), total=len(found))
            before = set((journal.read(fresh=True, mb=mb).get("orders") or {}))

            def change(raw):
                for m, d in sorted(found_details, key=lambda x: (x[1].get("version") or 1)):
                    if d.get("order_no") in before:
                        continue           # ce e deja în registru (cu bifele lui) nu se atinge
                    registry.upsert(raw, [d], sent_at=m["date"] or datetime.now(timezone.utc), user=user)
                    raw["orders"][d["order_no"]]["imported"] = True
                return raw
            raw = journal.update(change, mb=mb)
        added = len(set(raw.get("orders") or {}) - before)
        return [], (f"Am adăugat {added} {'comandă' if added == 1 else 'comenzi'} din ultimele {days} de zile în "
                    "registru. Mailurile au rămas cum erau (citite sau necitite)."), \
            {"title": "Registru actualizat", "back": url_for("orders")}

    return redirect(url_for("progress", token=_start_job(work, "Se caută comenzile în mail…")))


# --- Registrul Excel cumulat și datele pentru facturare ------------------------------------------

REGISTER_EXTRA = [
    ("Termen documente", "docs_deadline", 15, "DD.MM.YYYY"),
    ("Documente trimise", "docs_sent", 15, "DD.MM.YYYY"),
    ("Scadență plată", "due", 14, "DD.MM.YYYY"),
    ("Încasat", "paid", 12, "DD.MM.YYYY"),
    ("Toll_RO de facturat", "toll_amount", 14, "#,##0.00"),
    ("ePOD", "epod_state", 8, None),
    ("Notă", "note", 30, None),
]


def _register_rows(orders_):
    rows = []
    for o in orders_:
        st = registry.status(o)
        row = dict(o)
        for k in ("order_date", "loading_date", "unloading_date", "docs_sent", "paid"):
            row[k] = _day(o.get(k))
        row.update(docs_deadline=st["deadline"], due=st["due"], epod_state=f"{st['epod_done']}/{st['epod_total']}",
                   file=f"Comanda {o.get('order_no')}.pdf")
        rows.append(row)
    return rows


@app.route("/registru.xlsx")
@login_required
def register_xlsx():
    raw, error = _read()
    if error:
        return render_template("message.html", title="Nu am putut citi registrul", text=error, back=url_for("orders"))
    start, end = _period()
    rows = _register_rows([o for o in _orders(raw) if _in_period(o, start, end)])
    data = build_register(rows, extra_columns=REGISTER_EXTRA)
    return send_file(io.BytesIO(data), as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     download_name=f"registru_{start:%Y-%m-%d}_{end:%Y-%m-%d}.xlsx")


@app.route("/facturare.csv")
@login_required
def invoicing_csv():
    """Un rând pe comandă (plus un rând pentru Toll_RO, dacă e trecută suma), cu numărul comenzii
    în câmpul cerut de DSV pentru e-Factura (BT-10 „BuyerReference”). Separator „;”, ca Excel-ul în
    limba română să-l deschidă direct."""
    raw, error = _read()
    if error:
        return render_template("message.html", title="Nu am putut citi registrul", text=error, back=url_for("orders"))
    start, end = _period()
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Client", "CUI client", "Nr. comandă (BT-10 BuyerReference)", "Descriere", "Cantitate",
                "Preț unitar", "Monedă", "Data descărcării", "Ref. DSV", "Ref. client"])
    for o in sorted((o for o in _orders(raw) if _in_period(o, start, end)), key=lambda o: o.get("unloading_date") or ""):
        unloading = _day(o.get("unloading_date"))
        desc = f"Transport {_route(o)}" + (f", {unloading:%d.%m.%Y}" if unloading else "") + \
            (f", camion {o['truck']}" if o.get("truck") else "")
        price = f"{o['price']:.2f}".replace(".", ",") if o.get("price") is not None else ""
        common = [o.get("issuer") or "DSV Road S.R.L", o.get("issuer_vat") or "", o.get("order_no")]
        tail = [unloading.strftime("%d.%m.%Y") if unloading else "", o.get("dsv_ref") or "", o.get("client_ref") or ""]
        w.writerow(common + [desc, 1, price, o.get("currency") or "RON"] + tail)
        if o.get("toll_amount"):
            w.writerow(common + [f"Taxă Toll_RO, comanda {o.get('order_no')}", 1,
                                 f"{o['toll_amount']:.2f}".replace(".", ","), o.get("currency") or "RON"] + tail)
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8")), as_attachment=True, mimetype="text/csv",
                     download_name=f"facturare_{start:%Y-%m-%d}_{end:%Y-%m-%d}.csv")


# --- Rapoarte -----------------------------------------------------------------------------------

@app.route("/rapoarte")
@login_required
def reports():
    raw, error = _read()
    year = request.args.get("an", type=int) or _today().year
    orders_ = [o for o in _orders(raw) if (_day(o.get("loading_date")) or date.min).year == year]
    months = defaultdict(lambda: {"count": 0, "money": defaultdict(float), "toll": 0})
    trucks = defaultdict(lambda: {"count": 0, "money": defaultdict(float)})
    routes = defaultdict(list)
    doc_delays, pay_delays = [], []
    for o in orders_:
        d = _day(o.get("loading_date"))
        cur = o.get("currency") or "RON"
        key = d.strftime("%Y-%m")
        months[key]["count"] += 1
        months[key]["toll"] += bool(o.get("toll_ro"))
        trucks[o.get("truck") or "—"]["count"] += 1
        if o.get("price"):
            months[key]["money"][cur] += o["price"]
            trucks[o.get("truck") or "—"]["money"][cur] += o["price"]
            routes[(_route(o), cur)].append((d, o["price"]))
        unloading, sent, paid = _day(o.get("unloading_date")), _day(o.get("docs_sent")), _day(o.get("paid"))
        if unloading and sent:
            doc_delays.append((sent - unloading).days)
        if sent and paid:
            pay_delays.append((paid - sent).days)
    route_rows = []
    for (r, cur), prices in routes.items():
        prices.sort()
        values = [p for _, p in prices]
        route_rows.append({"route": r, "cur": cur, "count": len(values), "min": min(values), "max": max(values),
                           "avg": sum(values) / len(values), "last": values[-1], "last_date": prices[-1][0]})
    route_rows.sort(key=lambda x: -x["count"])
    s = registry.summary(raw, _today())
    return render_template(
        "reports.html", year=year, error=error, tab="rapoarte", money=registry.money,
        months=sorted(((k, v["count"], dict(v["money"]), v["toll"]) for k, v in months.items()), reverse=True),
        trucks=sorted(((k, v["count"], dict(v["money"])) for k, v in trucks.items()), key=lambda x: -x[1]),
        routes=route_rows, total=len(orders_), toll=sum(1 for o in orders_ if o.get("toll_ro")),
        unpaid=s["unpaid"], overdue=s["overdue"],
        doc_delay=round(sum(doc_delays) / len(doc_delays), 1) if doc_delays else None,
        pay_delay=round(sum(pay_delays) / len(pay_delays), 1) if pay_delays else None,
        month_start=_today().replace(day=1).isoformat(), today=_today().isoformat())
