"""Pagina „Facturi”: ce a plătit DSV, ce e restant, ce lipsește la DSV, legătura factură ↔ comandă."""

import csv
import io
import os
from datetime import date

from flask import abort, redirect, render_template, request, send_file, session, url_for
from openpyxl import load_workbook
from pypdf import PdfReader

import finance
import journal
import registry
from mailbox_client import Mailbox
from web_app import MAIL, _friendly_error, _set_status, _start_job, app, login_required

SCAN_DAYS = int(os.environ.get("FINANCE_SCAN_DAYS", "120"))


def _pdf_text(data):
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return ""


def _sheets(data):
    """Rândurile din toate foile unui Excel (.xlsx), ca liste de valori."""
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        return []
    try:
        return [[list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets]
    finally:
        wb.close()


def read_document(name, data):
    """(fel, conținut) pentru un atașament: PDF (extras, situație, RoToll) sau Excel (raport plăți DSV)."""
    if name.lower().endswith(finance.SHEET_EXT):
        sheets = _sheets(data)
        return ("dsv_report", sheets) if finance.parse_dsv_report(sheets) else (None, None)
    text = _pdf_text(data)
    return finance.kind(text), text


def scan_mail(path=None, days=SCAN_DAYS):
    """Caută în mail extrasele BCR, situațiile DSV și desfășurătoarele RoToll încă necitite de aplicație
    și le pune în jurnal. Întoarce lista rezumatelor (câte una pe document)."""
    notes, docs = [], []
    with Mailbox(**MAIL) as mb:
        raw = journal.read(fresh=True, mb=mb)
        scanned = set(raw.get("fin_scanned") or [])
        found = [m for m in mb.list_messages(days=days, limit=1000) if m["message_id"] not in scanned]
        names = mb.attachment_names([m["uid"] for m in found]) if found else {}
        todo = [m for m in found if finance.worth_opening(names.get(m["uid"], []))]
        for n, m in enumerate(todo):
            if path:
                _set_status(path, phase="Se citesc extrasele și situațiile…", done=n, total=len(todo))
            for name, data in mb.attachments(m["uid"], (".pdf",) + finance.SHEET_EXT):
                kind, text = read_document(name, data)
                if kind:
                    docs.append((kind, text, f"{name} ({m['subject']}, {m['date']:%d.%m.%Y})" if m["date"] else name))
        if path:
            _set_status(path, done=len(todo), total=len(todo))

        def change(r):
            for kind, text, source in docs:
                notes.append(finance.apply_document(r, kind, text, source))
            r["fin_scanned"] = list(set(r.get("fin_scanned") or []) | {m["message_id"] for m in found})[-3000:]
            return r
        journal.update(change, mb=mb)
    return notes


def describe(notes):
    bcr = [n for n in notes if n["kind"] == "bcr"]
    lists = [n for n in notes if n["kind"] == "dsv_list"]
    tolls = [n for n in notes if n["kind"] == "toll"]
    reports = [n for n in notes if n["kind"] == "dsv_report"]
    parts = []
    if reports:
        pays, amount = sum(n["payments"] for n in reports), sum(n["amount"] for n in reports)
        invs = sum(len(n["invoices"]) for n in reports)
        stornos = sorted({s for n in reports for s in n.get("stornos") or []})
        parts.append(f"{len(reports)} {'raport' if len(reports) == 1 else 'rapoarte'} de plăți DSV: {pays} "
                     f"{'plată nouă' if pays == 1 else 'plăți noi'}" + (f" ({registry.money({'RON': amount})}, "
                     f"{invs} facturi)" if pays else "")
                     + (f"; stornări: {', '.join(stornos)}" if stornos else ""))
    if bcr:
        pays, amount = sum(n["payments"] for n in bcr), sum(n["amount"] for n in bcr)
        parts.append(f"{len(bcr)} {'extras' if len(bcr) == 1 else 'extrase'} BCR: {pays} plăți DSV noi"
                     + (f" ({registry.money({'RON': amount})})" if amount else ""))
    if lists:
        parts.append(f"{len(lists)} {'situație' if len(lists) == 1 else 'situații'} DSV: "
                     f"{sum(n['invoices'] for n in lists)} facturi, {sum(n['unpaid'] for n in lists)} neplătite, "
                     f"{sum(n['missing'] for n in lists)} lipsă la DSV")
    if tolls:
        parts.append(f"{len(tolls)} curse RoToll")
    return "; ".join(parts) or "nimic nou"


def _rows(raw, today):
    invoices = list((raw.get("invoices") or {}).values())
    orders = raw.get("orders") or {}
    for inv in invoices:
        inv["status"] = finance.invoice_status(inv, today)
        due = finance._day(inv.get("due"))
        inv["late_days"] = (today - due).days if due and inv["status"] == "late" else 0
        o = orders.get(inv.get("order_no") or "")
        inv["route"] = (o or {}).get("loading", "")
    return invoices


@app.route("/facturi")
@login_required
def invoices_page():
    try:
        raw = journal.read()
    except Exception as e:
        return render_template("message.html", title="Nu am putut citi facturile", text=_friendly_error(e))
    today = date.today()
    rows = _rows(raw, today)
    show = request.args.get("f", "restante")
    pick = {"restante": lambda i: i["status"] == "late", "lipsa": lambda i: i["status"] == "missing",
            "neplatite": lambda i: i["status"] in ("late", "open", "missing"), "platite": lambda i: i["status"] == "paid"}
    listed = sorted((i for i in rows if pick.get(show, pick["restante"])(i)),
                    key=lambda i: (-(i.get("late_days") or 0), i.get("date") or "", i["no"]), reverse=show == "platite")
    if show == "platite":
        listed = sorted(listed, key=lambda i: i.get("paid") or "", reverse=True)
    total = lambda st: round(sum(i.get("amount") or 0 for i in rows if i["status"] in st), 2)  # noqa: E731
    payments = sorted(raw.get("payments") or [], key=lambda p: p.get("date") or "", reverse=True)
    orders_without = [o for o in (raw.get("orders") or {}).values() if not o.get("invoice")]
    return render_template(
        "invoices.html", rows=listed, show=show, tab="facturi", money=registry.money, pretty=registry.pretty,
        counts={k: sum(1 for i in rows if f(i)) for k, f in pick.items()},
        late_sum=total(("late",)), open_sum=total(("late", "open")), missing=sum(1 for i in rows if i["status"] == "missing"),
        last_payment=payments[0] if payments else None, total_invoices=len(rows),
        orders_without=len(orders_without), dsv_text=_dsv_text(rows, today), mail_user=MAIL["user"])


def _dsv_text(rows, today):
    """Mesajul gata de trimis la DSV: facturile restante și cele care nu apar la ei."""
    late = sorted((i for i in rows if i["status"] == "late"), key=lambda i: i["no"])
    missing = sorted((i for i in rows if i["status"] == "missing"), key=lambda i: i["no"])
    if not late and not missing:
        return ""
    lines = ["Bună ziua,", "", "vă rog să verificați situația următoarelor facturi CIRFORVEST SRL:"]
    if late:
        lines += ["", "Facturi scadente și neîncasate:"]
        lines += [f"- {i['no']} din {registry.pretty(i.get('date'))}, {registry.pretty(i.get('amount'))} "
                  f"{i.get('currency') or 'RON'}, scadentă {registry.pretty(i.get('due'))}" for i in late]
        lines.append(f"Total: {registry.money({'RON': sum(i.get('amount') or 0 for i in late)})}")
    if missing:
        lines += ["", "Facturi emise care nu apar în situația dumneavoastră:"]
        lines += [f"- {i['no']}" + (f" din {registry.pretty(i.get('date'))}" if i.get("date") else "") for i in missing]
    lines += ["", "Vă mulțumesc,", "CIRFORVEST SRL"]
    return "\n".join(lines)


@app.route("/facturi/verifica", methods=["POST"])
@login_required
def invoices_scan():
    back = url_for("invoices_page")

    def work(path):
        notes = scan_mail(path)
        return [], f"Am căutat în mailurile din ultimele {SCAN_DAYS} de zile: {describe(notes)}.", \
            {"title": "Plăți verificate", "back": back}
    return redirect(url_for("progress", token=_start_job(work, "Se caută extrasele și situațiile în mail…")))


@app.route("/facturi/incarca", methods=["POST"])
@login_required
def invoices_upload():
    """Un extras, o situație DSV, un desfășurător RoToll (PDF) sau un raport de plăți DSV (Excel), încărcat de mână."""
    notes, skipped = [], []
    try:
        docs = [(f.filename, *read_document(f.filename, f.read())) for f in request.files.getlist("doc")
                if f and f.filename]

        def change(raw):
            for name, kind, text in docs:
                if kind:
                    notes.append(finance.apply_document(raw, kind, text, f"{name} (încărcat)"))
                else:
                    skipped.append(name)
            return raw
        journal.update(change)
    except Exception as e:
        return render_template("message.html", title="Nu am putut citi documentul", text=_friendly_error(e),
                               back=url_for("invoices_page"))
    text = f"Am citit: {describe(notes)}." + (f" Nu am recunoscut: {', '.join(skipped)}." if skipped else "")
    return render_template("message.html", title="Document citit", text=text, back=url_for("invoices_page"))


@app.route("/facturi/dsv.csv")
@login_required
def invoices_csv():
    raw = journal.read()
    rows = _rows(raw, date.today())
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Factură", "Data", "Sumă", "Monedă", "Scadență", "Stare", "Zile întârziere", "Comandă",
                "Plătită", "Referință plată"])
    names = {"paid": "plătită", "late": "restantă", "open": "în termen", "missing": "lipsă la DSV"}
    for i in sorted(rows, key=lambda i: i["no"]):
        w.writerow([i["no"], registry.pretty(i.get("date")), registry.pretty(i.get("amount")), i.get("currency") or "RON",
                    registry.pretty(i.get("due")), names[i["status"]], i.get("late_days") or "", i.get("order_no") or "",
                    registry.pretty(i.get("paid")) if i.get("paid") else "", i.get("paid_ref") or ""])
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8")), as_attachment=True, mimetype="text/csv",
                     download_name=f"facturi_dsv_{date.today():%Y-%m-%d}.csv")


@app.route("/comanda/<no>/factura", methods=["POST"])
@login_required
def order_invoice(no):
    invoice_no = request.form.get("invoice", "").strip().upper()
    if invoice_no and not finance.INVOICE_RE.fullmatch(invoice_no):
        return render_template("message.html", title="Număr de factură greșit",
                               text="Scrie numărul ca în factură, de exemplu CIR2132.", back=url_for("order", no=no))

    def change(raw):
        if no not in (raw.get("orders") or {}):
            abort(404)
        return finance.link_invoice(raw, no, invoice_no)
    journal.update(change)
    return redirect(url_for("order", no=no) + "#factura")


def order_invoice_context(raw, o):
    """Pentru pagina comenzii: factura legată și propunerile."""
    inv = (raw.get("invoices") or {}).get(o.get("invoice") or "")
    return {"invoice": inv, "invoice_status": finance.invoice_status(inv) if inv else None,
            "invoice_candidates": [] if inv else finance.candidates(raw, o)[:3],
            "toll_trips": [raw.get("toll_trips", {}).get(s) for s in o.get("toll_trips") or [] if raw.get("toll_trips", {}).get(s)]}
