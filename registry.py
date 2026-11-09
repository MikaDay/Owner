"""Registrul comenzilor: tot ce s-a descărcat de pe site, cu ce mai e de făcut pentru fiecare.

Stă în jurnalul din Yahoo, la cheia "orders": {nr_comandă: {...}}. Pe lângă datele din PDF
(traseu, date, preț, camion, Toll_RO, link ePOD), fiecare comandă ține:
- pașii ePOD bifați (colectare, livrare, CMR încărcat),
- data trimiterii documentelor originale și data încasării,
- suma Toll_RO de facturat separat și o notiță,
- istoricul versiunilor (când DSV trimite o comandă modificată).
"""

from datetime import date, datetime, timezone

from deadlines import docs_deadline, payment_due

EPOD_STEPS = [("colectare", "Colectare confirmată"), ("livrare", "Livrare confirmată"), ("cmr", "CMR încărcat")]
# Câmpurile comparate când vine o versiune nouă a aceleiași comenzi.
COMPARED = [("price", "Preț"), ("currency", "Monedă"), ("loading_date", "Data încărcării"),
            ("unloading_date", "Data descărcării"), ("loading", "Încărcare"), ("unloading", "Descărcare"),
            ("truck", "Camion"), ("toll_ro", "Toll_RO"), ("goods", "Marfă"), ("weight_kg", "Greutate (kg)")]
KEPT = ["order_no", "version", "order_date", "issuer", "issuer_vat", "client_ref", "dsv_ref", "loading",
        "loading_date", "unloading", "unloading_date", "international", "goods", "weight_kg", "ldm", "price",
        "currency", "truck", "toll_ro", "contact", "epod"]
MAX_SENDS = 200


def _iso(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _day(v):
    if not v:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def entry_from_details(details):
    return {k: _iso(details.get(k)) for k in KEPT}


def compare(raw, details):
    """Cum se raportează o comandă tocmai citită la registru:
    ("nou", [], None) · ("duplicat", [], vechi) · ("modificat", [(câmp, vechi, nou)], vechi)."""
    no = details.get("order_no")
    old = (raw.get("orders") or {}).get(no) if no else None
    if not old:
        return "nou", [], None
    new = entry_from_details(details)
    changes = [(label, old.get(k), new.get(k)) for k, label in COMPARED if (old.get(k) or None) != (new.get(k) or None)]
    if not changes and (new.get("version") or 1) <= (old.get("version") or 1):
        return "duplicat", [], old
    return "modificat", changes, old


def upsert(raw, details_list, sent_at=None, user=""):
    """Adaugă sau actualizează comenzile în registru; păstrează ce a bifat omul (ePOD, documente, plată)."""
    orders = raw.setdefault("orders", {})
    sent_at = _iso(sent_at or datetime.now(timezone.utc))
    for details in details_list:
        no = details.get("order_no")
        if not no:
            continue
        new = entry_from_details(details)
        old = orders.get(no)
        if old:
            kind, changes, _ = compare(raw, details)
            if kind == "modificat":
                old.setdefault("history", []).append({"at": sent_at, "version": new.get("version"),
                                                      "changes": [[label, _iso(a), _iso(b)] for label, a, b in changes]})
            old.update({k: v for k, v in new.items() if v not in (None, "")})
            old["sent_at"] = sent_at
        else:
            orders[no] = {**new, "sent_at": sent_at, "sent_by": user, "epod_steps": {}, "docs_sent": None,
                          "paid": None, "toll_amount": None, "note": "", "history": []}
    return raw


def add_send(raw, order_nos, toll_count, user=""):
    sends = raw.setdefault("sends", [])
    sends.insert(0, {"at": _iso(datetime.now(timezone.utc)), "orders": list(order_nos), "toll": toll_count,
                     "user": user})
    del sends[MAX_SENDS:]
    return raw


def status(order, today=None):
    """Ce mai e de făcut pentru o comandă, gata de afișat."""
    today = today or date.today()
    steps = order.get("epod_steps") or {}
    epod_done = sum(1 for k, _ in EPOD_STEPS if steps.get(k))
    unloading = _day(order.get("unloading_date"))
    deadline = docs_deadline(unloading, bool(order.get("international")))
    sent = _day(order.get("docs_sent"))
    paid = _day(order.get("paid"))
    due = payment_due(sent)

    if sent:
        docs = ("ok", f"Documente trimise {sent:%d.%m}")
    elif not deadline:
        docs = ("info", "Termen documente: lipsește data descărcării")
    elif unloading and unloading > today:
        docs = ("info", f"Documente: după descărcare ({unloading:%d.%m}), termen {deadline:%d.%m}")
    else:
        left = (deadline - today).days
        if left < 0:
            docs = ("late", f"Documente întârziate {-left} {'zi' if left == -1 else 'zile'} (termen {deadline:%d.%m})")
        elif left <= 2:
            docs = ("soon", f"Documente: trimite până {deadline:%d.%m} ({'azi' if left == 0 else f'{left} zile'})")
        else:
            docs = ("todo", f"Documente: trimite până {deadline:%d.%m}")

    if paid:
        pay = ("ok", f"Încasat {paid:%d.%m}")
    elif not due:
        pay = ("info", "Plată: după trimiterea documentelor")
    elif due < today:
        pay = ("late", f"Plată restantă din {due:%d.%m}")
    else:
        pay = ("todo", f"Scadent {due:%d.%m}")

    return {"epod_done": epod_done, "epod_total": len(EPOD_STEPS), "deadline": deadline, "docs": docs,
            "due": due, "pay": pay, "open": epod_done < len(EPOD_STEPS) or not sent or not paid,
            "late": docs[0] == "late" or pay[0] == "late"}


def summary(raw, today=None):
    """Rezumatul pentru ecranul principal."""
    today = today or date.today()
    orders = list((raw.get("orders") or {}).values())
    docs_due = docs_late = epod_open = 0
    unpaid = {}
    overdue = {}
    loading_soon = []
    for o in orders:
        st = status(o, today)
        if st["docs"][0] in ("todo", "soon", "late"):
            docs_due += 1
            docs_late += st["docs"][0] == "late"
        unloading = _day(o.get("unloading_date"))
        if st["epod_done"] < st["epod_total"] and (not unloading or unloading <= today):
            epod_open += 1
        if not o.get("paid") and o.get("price"):
            cur = o.get("currency") or "RON"
            unpaid[cur] = unpaid.get(cur, 0) + o["price"]
            if st["pay"][0] == "late":
                overdue[cur] = overdue.get(cur, 0) + o["price"]
        loading = _day(o.get("loading_date"))
        if loading and 0 <= (loading - today).days <= 1:
            loading_soon.append(o)
    return {"orders": len(orders), "docs_due": docs_due, "docs_late": docs_late, "epod_open": epod_open,
            "unpaid": unpaid, "overdue": overdue,
            "loading_soon": sorted(loading_soon, key=lambda o: (o.get("loading_date") or "", o.get("truck") or ""))}


def money(amounts):
    """{'RON': 2300.0, 'EUR': 400} → „2.300,00 RON + 400,00 EUR”."""
    return " + ".join(f"{v:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".") + f" {c}"
                      for c, v in sorted(amounts.items())) or "0"
