from datetime import date

import finance
from finance_docs import BCR, DSV_LIST, TOLL


def test_kinds():
    assert finance.kind(BCR) == "bcr" and finance.kind(DSV_LIST) == "dsv_list" and finance.kind(TOLL) == "toll"
    assert finance.kind("Factura PPC") is None


def test_bcr_payments_from_dsv_only():
    pays = finance.parse_bcr(BCR)
    assert len(pays) == 1
    p = pays[0]
    assert p["amount"] == 5929.0 and p["date"] == date(2026, 9, 8)
    assert p["invoices"] == ["CIR2018", "CIR2021"] and p["ref"] == "260908S508904681"


def test_dsv_list_paid_unpaid_missing():
    inv, missing = finance.parse_dsv_list(DSV_LIST)
    assert set(inv) == {"CIR2018", "CIR2021", "CIR2105R", "CIR2132"}
    assert inv["CIR2018"]["paid"] == date(2026, 9, 8) and inv["CIR2018"]["amount"] == 2904.0
    assert inv["CIR2105R"]["paid"] is None and inv["CIR2105R"]["due"] == date(2026, 10, 17)
    assert inv["CIR2132"]["dsv_doc"] == "27080700" and inv["CIR2132"]["paid"] is None
    assert missing == ["CIR2095", "CIR2096"]


def test_toll_trip():
    t = finance.parse_toll(TOLL)
    assert t["plate"] == "TM12YEC" and t["start"] == "2026-09-30T03:29:00" and t["total"] == 250.67 and t["net"] == 207.17
    assert t["origin"].startswith("Depozit Altex") and t["origin"].endswith("România")
    assert t["destination"].startswith("Strada Arnold Hauser") and t["km"] == 551.0


def order(no, price, unloading, truck="TM12YEC", toll=""):
    return {"order_no": no, "price": price, "currency": "RON", "loading_date": unloading, "unloading_date": unloading,
            "truck": truck, "toll_ro": toll, "paid": None}


def test_apply_and_link_invoices_payments_and_tolls():
    raw = {"orders": {"A1": order("A1", 2400.0, "2026-08-24"), "A2": order("A2", 2500.0, "2026-08-24"),
                      "A3": order("A3", 2300.0, "2026-09-30", toll="DA"), "A4": order("A4", 2300.0, "2026-09-29")}}
    finance.apply_document(raw, "dsv_list", DSV_LIST, "test")
    # A1 (2.400 + TVA = 2.904) ↔ CIR2018 și A2 (3.025) ↔ CIR2021: un singur candidat → legate automat, plătite.
    assert raw["orders"]["A1"]["invoice"] == "CIR2018" and raw["orders"]["A1"]["paid"] == "2026-09-08"
    assert raw["orders"]["A2"]["invoice"] == "CIR2021" and raw["invoices"]["CIR2021"]["auto_linked"]
    # A3 și A4 au același preț (2.783 cu TVA) → nu ghicim: rămân nelegate.
    assert "invoice" not in raw["orders"]["A3"] and "invoice" not in raw["orders"]["A4"]
    assert [i["no"] for i in finance.candidates(raw, raw["orders"]["A3"])] == ["CIR2132"]
    assert raw["invoices"]["CIR2095"]["dsv_known"] is False
    finance.link_invoice(raw, "A3", "CIR2132")
    assert raw["orders"]["A3"]["invoice"] == "CIR2132" and finance.candidates(raw, raw["orders"]["A4"]) == []
    note = finance.apply_document(raw, "toll", TOLL, "test")
    assert note["plate"] == "TM12YEC"
    assert raw["orders"]["A3"]["toll_amount"] == 207.17 and raw["orders"]["A3"]["toll_cost"] == 250.67
    assert raw["orders"]["A4"]["toll_cost"] == 250.67 and raw["orders"]["A4"].get("toll_amount") is None  # fără Toll_RO
    # Extrasul BCR nu dublează plata deja știută și nu se aplică de două ori.
    assert finance.apply_document(raw, "bcr", BCR, "x")["payments"] == 1
    assert finance.apply_document(raw, "bcr", BCR, "x")["payments"] == 0


def test_bank_payment_marks_unknown_invoice_and_order():
    raw = {"orders": {"A1": order("A1", 2400.0, "2026-08-24")}}
    finance.link_invoice(raw, "A1", "CIR2018")
    assert raw["orders"]["A1"]["paid"] is None
    finance.apply_document(raw, "bcr", BCR, "extras sept")
    assert raw["invoices"]["CIR2018"]["paid"] == "2026-09-08" and raw["orders"]["A1"]["paid"] == "2026-09-08"
    assert finance.invoice_status(raw["invoices"]["CIR2018"]) == "paid"
    assert finance.invoice_status({"no": "X", "due": "2026-10-01"}, today=date(2026, 11, 1)) == "late"
    assert finance.invoice_status({"no": "X", "dsv_known": False}) == "missing"
