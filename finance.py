"""Facturi și plăți: citirea documentelor din mail și legarea lor de comenzi.

Trei feluri de documente (PDF, de obicei trimise pe mail de cineva din firmă):
- **extras de cont BCR** — încasările de la DSV, cu facturile plătite în detalii
  („Platitor: DSV ROAD SRL … Detalii: /ROC/DSV Invoice CIR2018/CIR2021///RFB/”);
- **lista facturilor din situația DSV** (reconcilierea „Facturi CIRFORVEST – sortate…”): factură, dată,
  sumă, scadență, data plății; plus „Numere CIR care nu apar în situația DSV”;
- **desfășurătorul RoToll** al unei curse: camion, început/sfârșit, de unde–până unde, taxa.

Totul ajunge în jurnal: "invoices" {CIR…: …}, "payments" [...], "toll_trips" {serie: …}.
Sumele facturilor sunt cu TVA (cum le plătește DSV); prețul comenzii e fără TVA.
"""

import re
from datetime import date, datetime, timedelta

VAT = 0.21
INVOICE_RE = re.compile(r"(?<!\d)CIR\d{3,6}R?(?!\d)")   # și lipit de text: „InvoiceCIR2018”


def _num(text):
    """„5.929,00” / „5,929.00” / „-3,509.00” → 5929.0 (fără semn)."""
    t = text.strip().lstrip("-").replace(" ", "")
    if re.search(r",\d{2}$", t):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    return float(t)


def _date(text, fmt=None):
    for f in ([fmt] if fmt else ["%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"]):
        try:
            return datetime.strptime(text.strip(), f).date()
        except (ValueError, AttributeError):
            continue
    return None


# --- Ce fel de document e ----------------------------------------------------------------------

def kind(text):
    t = text or ""
    if "EXTRAS DE CONT" in t and "BANCA COMERCIALA ROMANA" in t:
        return "bcr"
    if "Desfășurător" in t and "NUMĂR DE ÎNMATRICULARE" in t:
        return "toll"
    if re.search(r"Facturi CIRFORVEST", t) or (re.search(r"^CIR\d+R?\s+\d\d\.\d\d\.\d{4}\s+-?[\d,.]+\s+RON", t, re.M)
                                               and "Scaden" in t):
        return "dsv_list"
    return None


# --- Extras de cont BCR: încasările de la DSV ----------------------------------------------------

_AMOUNT = r"\d{1,3}(?:\.\d{3})*,\d{2}"


def parse_bcr(text):
    """Încasările de la DSV dintr-un extras BCR: [{date, amount, invoices, ref}].

    Coloanele de sumă apar lipite în textul PDF-ului („5.929,000,00” = credit 5.929,00 și debit 0,00);
    pentru o plată primită, suma e cea nenulă."""
    flat = re.sub(r"\s+", " ", text or "")
    out = []
    for m in re.finditer(r"(\d{2}-\d{2}-\d{4}) \d{2}:\d{2} Referinta (\S+?),.{0,120}?Platitor: DSV", flat):
        block = flat[m.start(): m.start() + 900]
        end = re.search(r"Ordin de plata (\S+) (\d{2}\.\d{2}\.\d{4}) ?(" + _AMOUNT + r")(" + _AMOUNT + ")", block)
        if not end:
            continue
        details = block[: end.start()]
        invoices = sorted(set(INVOICE_RE.findall(details.split("Detalii:", 1)[-1].replace(" ", ""))))
        a, b = _num(end.group(3)), _num(end.group(4))
        out.append({"date": _date(end.group(2)) or _date(m.group(1)), "amount": a or b, "invoices": invoices,
                    "ref": m.group(2), "doc": end.group(1)})
    return out


# --- Lista facturilor (situația DSV) -----------------------------------------------------------

def parse_dsv_list(text):
    """Facturile din situația DSV / reconciliere: ({nr: {...}}, [nr lipsă la DSV]).

    Rândul tip: „CIR1938 25.06.2026 -3,509.00 RON 29.07.2026 80018586 18.08.2026 33016277 …”
    (fără dată de plată = neplătită). Secțiunea „Numere CIR care nu apar în situația DSV” dă
    facturile pe care DSV nu le are deloc."""
    invoices, missing = {}, []
    section = "list"
    for line in (text or "").splitlines():
        if "nu apar în situația DSV" in line or "nu apar in situatia DSV" in line:
            section = "missing"
            continue
        if line.startswith("Plăți și compensări") or line.startswith("Plati si compensari"):
            section = "payments"
            continue
        if section == "missing":
            m = re.match(r"^(CIR\d+R?)\b", line.strip())
            if m:
                missing.append(m.group(1))
            continue
        if section != "list":
            continue
        m = re.match(r"^(CIR\d+R?)\s+(\d\d\.\d\d\.\d{4})\s+(-?[\d.,]+)\s+(RON|EUR)\s+(\d\d\.\d\d\.\d{4})"
                     r"(?:\s+(\d+))?(?:\s+(\d\d\.\d\d\.\d{4}))?(?:\s+(\d+))?", line.strip())
        if m:
            invoices[m.group(1)] = {"no": m.group(1), "date": _date(m.group(2)), "amount": _num(m.group(3)),
                                    "currency": m.group(4), "due": _date(m.group(5)), "dsv_doc": m.group(6) or "",
                                    "paid": _date(m.group(7)) if m.group(7) else None, "paid_ref": m.group(8) or ""}
    return invoices, sorted(set(missing) - set(invoices))


# --- Desfășurător RoToll ------------------------------------------------------------------------

def parse_toll(text):
    """O cursă RoToll: {serial, plate, start, end, origin, destination, km, net, total} sau None."""
    t = text or ""

    def after(label, pattern=r"([^\n]+)"):
        m = re.search(re.escape(label) + r"\s*\n\s*" + pattern, t)
        return m.group(1).strip() if m else ""

    plate = re.sub(r"\s+", "", after("NUMĂR DE ÎNMATRICULARE"))
    serial = after("SERIE UNICĂ A TRANZACȚIEI")
    if not plate or not serial:
        return None
    start = datetime.strptime(after("DATĂ DE ÎNCEPUT"), "%d.%m.%Y %H:%M") if after("DATĂ DE ÎNCEPUT") else None
    end = datetime.strptime(after("DATĂ DE FINAL"), "%d.%m.%Y %H:%M") if after("DATĂ DE FINAL") else None
    place = lambda label: re.sub(r"\s+", " ", (re.search(re.escape(label) + r"\s*\n(.+?)\n[A-ZĂÂÎȘȚ ]{6,}\n", t, re.S)  # noqa: E731
                                                or [None, ""])[1]).strip(" ,")

    def money(label):
        v = after(label)
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return {"serial": serial, "plate": plate, "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None, "origin": place("ORIGINE"), "destination": place("DESTINAȚIE"),
            "km": money("DISTANȚĂ TARIFABILĂ PARCURSĂ (KM)"), "net": money("PREȚ TOTAL, FĂRĂ TVA (RON)"),
            "total": money("PREȚ TOTAL, INCLUSIV TVA (RON)")}


# Numele atașamentelor care merită deschise (restul mailurilor nu se descarcă deloc).
DOC_NAMES = re.compile(r"extras|breakdown|desf[aă][sș]ur[aă]tor|factur|situa[tț]i|statement|sold", re.I)


def worth_opening(names):
    return any(n.lower().endswith(".pdf") and DOC_NAMES.search(n) for n in names)


# --- Aplicarea în jurnal -----------------------------------------------------------------------

def _iso(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def apply_document(raw, doc_kind, text, source=""):
    """Pune în jurnal ce s-a citit dintr-un document. Întoarce un rezumat (pentru mesaje)."""
    invoices = raw.setdefault("invoices", {})
    note = {"kind": doc_kind, "source": source}
    if doc_kind == "bcr":
        payments = raw.setdefault("payments", [])
        known = {(p.get("ref"), p.get("doc")) for p in payments}
        new = [p for p in parse_bcr(text) if (p["ref"], p["doc"]) not in known]
        for p in new:
            payments.append({**{k: _iso(v) for k, v in p.items()}, "source": source})
            for no in p["invoices"]:
                inv = invoices.setdefault(no, {"no": no, "currency": "RON", "dsv_known": True})
                if not inv.get("paid"):
                    inv.update(paid=_iso(p["date"]), paid_ref=p["ref"], paid_by="extras BCR")
        note.update(payments=len(new), amount=round(sum(p["amount"] for p in new), 2),
                    invoices=sorted({no for p in new for no in p["invoices"]}))
    elif doc_kind == "dsv_list":
        found, missing = parse_dsv_list(text)
        for no, inv in found.items():
            cur = invoices.setdefault(no, {"no": no})
            for k, v in inv.items():
                if v not in (None, "") and not (k == "paid" and cur.get("paid")):
                    cur[k] = _iso(v)
            cur["dsv_known"] = True
        for no in missing:
            invoices.setdefault(no, {"no": no, "currency": "RON"})["dsv_known"] = False
        note.update(invoices=len(found), unpaid=sum(1 for i in found.values() if not i["paid"]), missing=len(missing))
    elif doc_kind == "toll":
        trip = parse_toll(text)
        if trip:
            raw.setdefault("toll_trips", {})[trip["serial"]] = trip
            note.update(trip=trip["serial"], plate=trip["plate"], total=trip["total"])
    link_all(raw)
    return note


# --- Legături: factură ↔ comandă, curse RoToll ↔ comandă -----------------------------------------

def candidates(raw, order):
    """Facturile care s-ar potrivi cu o comandă: suma = prețul + 21% TVA (±1 RON), emisă între
    descărcare−5 zile și descărcare+60 de zile, nelegată de altă comandă."""
    if not order.get("price"):
        return []
    gross = round(order["price"] * (1 + VAT), 2)
    ref = _day(order.get("unloading_date")) or _day(order.get("loading_date"))
    taken = {i.get("order_no") for i in (raw.get("invoices") or {}).values() if i.get("order_no")}
    rejected = set(order.get("invoice_rejected") or [])
    out = []
    for inv in (raw.get("invoices") or {}).values():
        if inv["no"] in rejected:
            continue
        if inv.get("order_no") and inv.get("order_no") != order.get("order_no"):
            continue
        if inv.get("amount") is None or abs(inv["amount"] - gross) > 1:
            continue
        d = _day(inv.get("date"))
        if ref and d and not (ref - timedelta(days=5) <= d <= ref + timedelta(days=60)):
            continue
        out.append(inv)
    out.sort(key=lambda i: abs(((_day(i.get("date")) or date.max) - (ref or date.min)).days) if ref else 0)
    return [i for i in out if i.get("order_no") == order.get("order_no") or i.get("order_no") not in taken]


def link_invoice(raw, order_no, invoice_no, auto=False):
    invoices, orders = raw.setdefault("invoices", {}), raw.get("orders") or {}
    for inv in invoices.values():                     # o factură / o comandă
        if inv.get("order_no") == order_no and inv["no"] != invoice_no:
            inv.pop("order_no", None)
            inv.pop("auto_linked", None)
    o = orders.get(order_no)
    if o is None:
        return raw
    if not invoice_no and o.get("invoice"):
        # „Nu e factura asta”: nu o mai propunem (nici automat) pentru comanda asta.
        o["invoice_rejected"] = sorted(set(o.get("invoice_rejected") or []) | {o["invoice"]})
        old = invoices.get(o["invoice"])
        if old:
            old.pop("order_no", None)
            old.pop("auto_linked", None)
    if invoice_no:
        o["invoice_rejected"] = [x for x in o.get("invoice_rejected") or [] if x != invoice_no]
        inv = invoices.setdefault(invoice_no, {"no": invoice_no, "currency": o.get("currency") or "RON"})
        inv["order_no"] = order_no
        inv["auto_linked"] = bool(auto)
        o["invoice"] = invoice_no
    else:
        o.pop("invoice", None)
    sync_order(raw, order_no)
    return raw


def sync_order(raw, order_no):
    """Plata facturii se vede la comandă: „Încasat” (dacă nu a fost bifat de mână) și scadența facturii."""
    o = (raw.get("orders") or {}).get(order_no)
    inv = (raw.get("invoices") or {}).get((o or {}).get("invoice") or "")
    if not o or not inv:
        return
    o["invoice_due"] = inv.get("due")
    if inv.get("paid") and (not o.get("paid") or o.get("paid_auto")):
        o["paid"], o["paid_auto"] = inv["paid"][:10], True
    elif not inv.get("paid") and o.get("paid_auto"):
        o["paid"], o["paid_auto"] = None, False


def link_all(raw):
    """Legături automate, doar unde nu e nicio îndoială:
    - factura are deja comanda (din legături anterioare) → plata trece la comandă;
    - o comandă fără factură are un singur candidat, iar candidatul se potrivește doar cu ea → legare automată;
    - cursele RoToll ale camionului din zilele comenzii → taxa la comandă."""
    orders = raw.get("orders") or {}
    for o in orders.values():
        if o.get("invoice"):
            sync_order(raw, o["order_no"])
    proposals = {}
    for no, o in orders.items():
        if o.get("invoice"):
            continue
        best = clear_choice(o, candidates(raw, o))
        if best:
            proposals[no] = best
    claimed = {}
    for no, inv_no in proposals.items():
        claimed.setdefault(inv_no, []).append(no)
    for inv_no, nos in claimed.items():
        if len(nos) == 1:                          # factura nu e „cerută” și de altă comandă
            link_invoice(raw, nos[0], inv_no, auto=True)
    link_tolls(raw)
    return raw


def clear_choice(order, cands):
    """Factura de legat automat: singurul candidat, sau cel mai apropiat dacă e emis în cel mult 10 zile
    de la descărcare și următorul e cu cel puțin 7 zile mai departe. Altfel None (decide omul)."""
    if not cands:
        return None
    ref = _day(order.get("unloading_date")) or _day(order.get("loading_date"))
    if len(cands) == 1 or not ref:
        return cands[0]["no"] if len(cands) == 1 else None
    dist = [abs(((_day(c.get("date")) or date.max) - ref).days) if _day(c.get("date")) else 10 ** 6 for c in cands]
    if dist[0] <= 10 and dist[1] - dist[0] >= 7:
        return cands[0]["no"]
    return None


def link_tolls(raw):
    trips = list((raw.get("toll_trips") or {}).values())
    for o in (raw.get("orders") or {}).values():
        plate = (o.get("truck") or "").replace(" ", "")
        start, end = _day(o.get("loading_date")), _day(o.get("unloading_date")) or _day(o.get("loading_date"))
        if not plate or not start:
            continue
        mine = [t for t in trips if t["plate"] == plate and t.get("start")
                and start - timedelta(days=1) <= _day(t["start"]) <= end + timedelta(days=1)]
        if not mine:
            continue
        o["toll_trips"] = sorted(t["serial"] for t in mine)
        o["toll_cost"] = round(sum(t.get("total") or 0 for t in mine), 2)
        net = round(sum(t.get("net") or 0 for t in mine), 2)
        # Pentru comenzile cu Toll_RO: suma de refacturat (fără TVA), dacă nu a fost scrisă de mână.
        if o.get("toll_ro") and (o.get("toll_amount") is None or o.get("toll_auto")):
            o["toll_amount"], o["toll_auto"] = net, True
    return raw


def invoice_status(inv, today=None):
    today = today or date.today()
    due = _day(inv.get("due"))
    if inv.get("paid"):
        return "paid"
    if inv.get("dsv_known") is False:
        return "missing"
    if due and due < today:
        return "late"
    return "open"
