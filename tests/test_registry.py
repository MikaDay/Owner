import csv
import io
from datetime import date

from openpyxl import load_workbook

import registry
from conftest import follow_job
from dsv_pdf import dsv_order, plain_pdf
from test_web import journal, mail_job


def send_all(client, server):
    tok = mail_job(client, server)
    client.post(f"/export/{tok}", data={"use_0": "1", "use_1": "1", "mode": "mail"})


def test_sent_orders_enter_the_register_with_details(client, server):
    send_all(client, server)
    orders = journal(server)["orders"]
    assert set(orders) == {"ARWY01981", "ARWY09999"}
    o = orders["ARWY09999"]
    assert o["toll_ro"] == "DA" and o["epod"].startswith("https://www.dsv.com/mydsv/e-pod")
    assert o["unloading_date"] == "2026-10-01" and o["price"] == 2300.0
    assert journal(server)["sends"][0]["orders"] and journal(server)["sends"][0]["toll"] == 1


def test_status_deadlines_and_payment():
    o = {"unloading_date": "2026-10-01", "epod_steps": {}, "docs_sent": None, "paid": None}
    st = registry.status(o, today=date(2026, 10, 5))
    assert st["deadline"] == date(2026, 10, 8) and st["docs"][0] == "todo"
    assert registry.status(o, today=date(2026, 10, 7))["docs"][0] == "soon"
    assert registry.status(o, today=date(2026, 10, 12))["docs"][0] == "late"
    o["docs_sent"] = "2026-10-06"
    st = registry.status(o, today=date(2026, 11, 25))
    assert st["docs"][0] == "ok" and st["due"] == date(2026, 11, 20) and st["pay"][0] == "late"
    o["paid"] = "2026-11-21"
    assert registry.status(o, today=date(2026, 11, 25))["pay"][0] == "ok"


def test_order_page_epod_docs_paid_toll_note(client, server):
    send_all(client, server)
    html = client.get("/comanda/ARWY09999").get_data(as_text=True)
    assert "Deschide ePOD-ul DSV" in html and "Toll_RO de facturat" in html
    client.post("/comanda/ARWY09999", data={"action": "epod", "step": "colectare"})
    client.post("/comanda/ARWY09999", data={"action": "docs_sent", "value": "2026-10-05"})
    client.post("/comanda/ARWY09999", data={"action": "paid", "value": "2026-11-15"})
    client.post("/comanda/ARWY09999", data={"action": "toll_amount", "value": "245,50"})
    client.post("/comanda/ARWY09999", data={"action": "note", "value": "factura 77"})
    o = journal(server)["orders"]["ARWY09999"]
    assert o["epod_steps"]["colectare"] and o["docs_sent"] == "2026-10-05" and o["paid"] == "2026-11-15"
    assert o["toll_amount"] == 245.5 and o["note"] == "factura 77"
    client.post("/comanda/ARWY09999", data={"action": "epod", "step": "colectare"})   # a doua apăsare debifează
    assert not journal(server)["orders"]["ARWY09999"]["epod_steps"]["colectare"]


def test_orders_list_summary_and_home_api(client, server):
    send_all(client, server)
    html = client.get("/comenzi?f=all").get_data(as_text=True)
    assert "ARWY01981" in html and "ARWY09999" in html and "documente de trimis" in html
    assert "ARWY09999" in client.get("/comenzi?f=all&q=09999").get_data(as_text=True)
    assert "ARWY01981" not in client.get("/comenzi?f=all&q=09999").get_data(as_text=True)
    s = client.get("/api/rezumat").get_json()
    assert s["orders"] == 2 and s["docs_due"] == 2 and "RON" in s["unpaid"]


def test_import_older_orders_without_touching_mail(client, server):
    server.add_mail("FW: ARWY02000", [("a.pdf", dsv_order(order_no="ARWY02000"))], day=1, seen=True)
    server.add_mail("FW: ARWY02001", [("b.pdf", dsv_order(order_no="ARWY02001"))], day=2)
    server.add_mail("Facturi", [("f.pdf", plain_pdf())], day=3)
    loc = follow_job(client, client.post("/comenzi/import", data={"days": "60"}))
    assert "Am adăugat 2 comenzi" in client.get(loc).get_data(as_text=True)
    j = journal(server)
    assert set(j["orders"]) == {"ARWY02000", "ARWY02001"} and j["orders"]["ARWY02000"]["imported"]
    assert not j.get("downloaded")                                      # nu se notează ca descărcate
    assert [server.is_seen(u) for u in sorted(server.folders["INBOX"], key=int)] == [True, False, False]


def test_register_excel_and_invoicing_csv(client, server):
    send_all(client, server)
    client.post("/comanda/ARWY09999", data={"action": "toll_amount", "value": "100"})
    r = client.get("/registru.xlsx?de=2026-09-01&pana=2026-12-31")
    ws = load_workbook(io.BytesIO(r.data)).active
    header = [c.value for c in ws[1]]
    assert "Termen documente" in header and "Toll_RO de facturat" in header
    assert {ws.cell(row=i, column=1).value for i in (2, 3)} == {"ARWY01981", "ARWY09999"}
    r = client.get("/facturare.csv?de=2026-09-01&pana=2026-12-31")
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][2].startswith("Nr. comandă (BT-10")
    assert [row[2] for row in rows[1:]] == ["ARWY01981", "ARWY09999", "ARWY09999"]
    assert rows[3][3].startswith("Taxă Toll_RO") and rows[3][5] == "100,00" and rows[1][1] == "RO46318372"


def test_reports_page(client, server):
    send_all(client, server)
    html = client.get("/rapoarte?an=2026").get_data(as_text=True)
    assert "Prețuri pe traseu" in html and "Dragomiresti Deal → Timisoara" in html and "TM12YEC" in html
