"""Raportul de plăți DSV (Excel „RAPORT PLATI … EXPORT_….xlsx”): doar rândurile CIRFORVEST."""

import io
from datetime import datetime

from openpyxl import Workbook

import finance
import views_finance
from conftest import follow_job
from test_web import journal

D = datetime


def report_xlsx():
    """Ca raportul DSV: o foaie cu totalurile pe furnizor și una cu rândurile pe factură (ambele cu alți
    transportatori, inventați); aceeași factură poate apărea în două foi."""
    wb = Workbook()
    ws = wb.active
    ws.title = "RAPORT PLATI"
    ws.append(["Furnizor", "Nume", "Factura", "Doc", "Data", "Suma", "Moneda", None, "Data plata", "Nr plata"])
    ws.append([6400000001, "ALFA TRANS SRL", None, 33024780, D(2026, 10, 20), 1783.58, "RON", None,
               D(2026, 10, 20), 33024780])
    ws.append([6412069611, "CIRFORVEST SRL", None, 33024781, D(2026, 10, 20), 6655.0, "RON", None,
               D(2026, 10, 20), 33024781])                                   # total pe furnizor: ignorat
    ws2 = wb.create_sheet("PLATI 20.10")
    ws2.append([6400000001, "ALFA TRANS SRL", "AT0142", 27079159, D(2026, 9, 29), -1783.58, "RON", None,
                D(2026, 10, 20), 33024780, "*RO1", D(2026, 10, 17), "BRAS1 2010"])
    ws2.append([6412069611, "CIRFORVEST SRL", "CIR2111", 27068756, D(2026, 9, 17), -3146.0, "RON", None,
                D(2026, 10, 20), 33024781, "*RO2", D(2026, 10, 17), "BRAS1 2010"])
    ws2.append([6412069611, "CIRFORVEST SRL", "CIR2105R", 27083102, "17/09/2026", "-3,509.00", "RON", None,
                "20/10/2026", "33024781", "*RO2", "17/10/2026", "BRAS1 2010"])
    ws3 = wb.create_sheet("RAPORT PLATI 05.11.2026")
    ws3.append([6412069611, "CIRFORVEST SRL", "CIR2090", 37047841, D(2026, 9, 8), -3630.0, "RON", None,
                D(2026, 11, 5), 33027273])
    ws3.append([6412069611, "CIRFORVEST SRL", "ST CIR2090", 37047840, D(2026, 9, 8), 3570.0, "RON", None,
                D(2026, 11, 5), 33027273])
    ws3.append([6412069611, "CIRFORVEST SRL", "CIR2137", 27095522, D(2026, 10, 1), -1028.5, "RON", None,
                D(2026, 11, 5), 33027273])
    ws3.append([6412069611, "CIRFORVEST SRL", "CIR2111", 27068756, D(2026, 9, 17), -3146.0, "RON", None,
                D(2026, 10, 20), 33024781])                                  # repetată în altă foaie
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_keeps_only_cirforvest_invoice_lines():
    pays = finance.parse_dsv_report(views_finance._sheets(report_xlsx()))
    assert [(p["ref"], p["amount"], p["invoices"]) for p in pays] == [
        ("33024781", 6655.0, ["CIR2105R", "CIR2111"]),
        ("33027273", 1088.5, ["CIR2090", "CIR2137"]),          # 3.630 − 3.570 (stornare) + 1.028,50
    ]
    assert pays[0]["date"].isoformat() == "2026-10-20"
    assert finance.worth_opening(["RAPORT PLATI 20.10.2026 EXPORT_20261020_112333.xlsx"])
    assert not finance.worth_opening(["poza.xlsx", "raport.docx"])


def test_apply_marks_invoices_paid_and_merges_with_bank_statement():
    raw = {"invoices": {"CIR2111": {"no": "CIR2111", "amount": 3146.0, "date": "2026-09-17", "due": "2026-10-17"}},
           "payments": [{"date": "2026-10-21", "amount": 6655.0, "invoices": [], "ref": "BCR1", "doc": "OP1"}]}
    note = finance.apply_document(raw, "dsv_report", views_finance._sheets(report_xlsx()), "raport.xlsx")
    assert note["payments"] == 2 and note["stornos"] == ["CIR2090"]
    inv = raw["invoices"]
    assert inv["CIR2111"]["paid"] == "2026-10-20" and inv["CIR2111"]["paid_ref"] == "33024781"
    assert inv["CIR2105R"]["amount"] == 3509.0 and inv["CIR2105R"]["date"] == "2026-09-17"
    assert inv["CIR2090"]["storno"] == 3570.0 and finance.invoice_status(inv["CIR2090"]) == "paid"
    # Aceeași plată din extrasul BCR (6.655, cu o zi mai târziu) nu se numără de două ori.
    assert len(raw["payments"]) == 2 and raw["payments"][0]["dsv_ref"] == "33024781"
    assert raw["payments"][0]["invoices"] == ["CIR2105R", "CIR2111"]
    # Același raport încă o dată: nimic nou.
    assert finance.apply_document(raw, "dsv_report", views_finance._sheets(report_xlsx()), "")["payments"] == 0


def test_upload_and_mail_scan(client, server):
    r = client.post("/facturi/incarca", data={"doc": (io.BytesIO(report_xlsx()), "RAPORT PLATI 20.10.2026.xlsx")},
                    content_type="multipart/form-data")
    text = r.get_data(as_text=True)
    assert "1 raport de plăți DSV: 2 plăți noi (7.743,50 RON, 4 facturi); stornări: CIR2090" in text
    assert journal(server)["invoices"]["CIR2137"]["paid"] == "2026-11-05"
    page = client.get("/facturi?f=platite").get_data(as_text=True)
    assert "Stornare DSV 3.570,00 RON" in page and "ref. 33027273" in page

    server.add_mail("Raport plati", [("RAPORT PLATI 29.10.2026 EXPORT.xlsx", report_xlsx())], day=1,
                    sender="cirforvest17@yahoo.com")
    loc = follow_job(client, client.post("/facturi/verifica"))
    assert "1 raport de plăți DSV: 0 plăți noi" in client.get(loc).get_data(as_text=True)


def test_excel_without_cirforvest_is_not_recognised(client, server):
    wb = Workbook()
    wb.active.append(["ALFA TRANS SRL", "AT1", -10.0, "RON", D(2026, 10, 20), 123456789])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/facturi/incarca", data={"doc": (io.BytesIO(buf.getvalue()), "altceva.xlsx")},
                    content_type="multipart/form-data")
    assert "Nu am recunoscut: altceva.xlsx" in r.get_data(as_text=True)
