import csv
import io
from datetime import datetime

import pytest

import automation
import views_auto
import views_finance
import webpush
from conftest import follow_job
from dsv_pdf import dsv_order, plain_pdf
from finance_docs import BCR, DSV_LIST, TOLL
from new_orders import RO
from test_web import journal

DOCS = {b"BCR": BCR, b"DSV": DSV_LIST, b"TOLL": TOLL}


@pytest.fixture(autouse=True)
def fake_pdf_text(monkeypatch):
    """PDF-urile de test conțin doar un marcaj; textul vine din documentele model (fără fonturi)."""
    real = views_finance._pdf_text
    monkeypatch.setattr(views_finance, "_pdf_text",
                        lambda data: next((t for k, t in DOCS.items() if data.startswith(b"%PDF-" + k)), None) or real(data))


def pdf(key):
    return b"%PDF-" + key + b"\n"


def orders_sent(client, server):
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001", price="2.400,00",
                                                          loading=("X SRL", "300100 Timisoara", "24.08.2026"),
                                                          unloading=("Y SRL", "300100 Timisoara", "24.08.2026")))], day=1)
    server.add_mail("FW: ARWY0003", [("c.pdf", dsv_order(order_no="ARWY0003", toll=True,
                                                          loading=("X SRL", "300100 Timisoara", "30.09.2026"),
                                                          unloading=("Y SRL", "300100 Timisoara", "30.09.2026")))], day=2)
    tok = follow_job(client, client.get("/noi")).rsplit("/", 1)[1]
    client.post(f"/export/{tok}", data={"use_0": "1", "use_1": "1", "mode": "mail"})


def test_scan_reads_only_documents_and_links_everything(client, server):
    orders_sent(client, server)
    server.add_mail("Extras de cont", [("Extras de cont_20261015.pdf", pdf(b"BCR"))], day=3, sender="cirforvest17@yahoo.com")
    server.add_mail("situatie", [("Facturi_sortate (4).pdf", pdf(b"DSV"))], day=4, sender="cirforvest17@yahoo.com")
    server.add_mail("", [("a36e-breakdown.pdf", pdf(b"TOLL"))], day=5, sender="cirforvest17@yahoo.com")
    server.add_mail("Poza", [("poza.pdf", plain_pdf())], day=6, sender="cirforvest17@yahoo.com")   # nu se deschide
    loc = follow_job(client, client.post("/facturi/verifica"))
    text = client.get(loc).get_data(as_text=True)
    assert "1 extras BCR: 1 plăți DSV noi (5.929,00 RON)" in text and "4 facturi, 2 neplătite, 2 lipsă la DSV" in text
    assert "1 curse RoToll" in text
    j = journal(server)
    assert j["orders"]["ARWY0001"]["invoice"] == "CIR2018" and j["orders"]["ARWY0001"]["paid"] == "2026-09-08"
    assert j["orders"]["ARWY0003"]["toll_amount"] == 207.17                    # Toll_RO, din RoToll
    # Un singur candidat cu suma potrivită (2.300 + TVA = 2.783) → legată automat, de verificat.
    assert j["orders"]["ARWY0003"]["invoice"] == "CIR2132" and j["invoices"]["CIR2132"]["auto_linked"]
    # A doua verificare nu mai citește aceleași mailuri.
    loc = follow_job(client, client.post("/facturi/verifica"))
    assert "nimic nou" in client.get(loc).get_data(as_text=True)


def test_invoices_page_lists_csv_and_dsv_message(client, server):
    orders_sent(client, server)
    server.add_mail("situatie", [("Facturi_sortate.pdf", pdf(b"DSV"))], day=4)
    follow_job(client, client.post("/facturi/verifica"))
    html = client.get("/facturi?f=restante").get_data(as_text=True)
    assert "CIR2105R" in html and "Restantă" in html and "Mesaj pentru DSV" in html
    assert "CIR2095" in client.get("/facturi?f=lipsa").get_data(as_text=True)
    assert "Plătită 08.09.2026" in client.get("/facturi?f=platite").get_data(as_text=True)
    rows = list(csv.reader(io.StringIO(client.get("/facturi/dsv.csv").data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][0] == "Factură" and {r[0]: r[5] for r in rows[1:]}["CIR2095"] == "lipsă la DSV"


def test_link_invoice_from_order_page_and_upload(client, server):
    orders_sent(client, server)
    r = client.post("/facturi/incarca", data={"doc": (io.BytesIO(pdf(b"DSV")), "situatie.pdf")},
                    content_type="multipart/form-data")
    assert "4 facturi" in r.get_data(as_text=True)
    html = client.get("/comanda/ARWY0003").get_data(as_text=True)
    assert "Legată automat după sumă și dată" in html and "Nu e factura asta" in html
    client.post("/comanda/ARWY0003/factura", data={"invoice": ""})            # „Nu e factura asta”
    html = client.get("/comanda/ARWY0003").get_data(as_text=True)
    assert "Este CIR2132" not in html                                          # refuzată: nu mai e propusă
    client.post("/facturi/incarca", data={"doc": (io.BytesIO(pdf(b"DSV")), "situatie.pdf")}, content_type="multipart/form-data")
    assert "invoice" not in journal(server)["orders"]["ARWY0003"]             # nici legată automat din nou
    client.post("/comanda/ARWY0003/factura", data={"invoice": "CIR2132"})     # dar de mână se poate
    j = journal(server)
    assert j["orders"]["ARWY0003"]["invoice"] == "CIR2132" and not j["invoices"]["CIR2132"]["auto_linked"]
    assert "Factura CIR2132 restantă" in client.get("/comenzi?f=all").get_data(as_text=True)
    client.post("/comanda/ARWY0003/factura", data={"invoice": ""})
    assert "invoice" not in journal(server)["orders"]["ARWY0003"]
    r = client.post("/comanda/ARWY0003/factura", data={"invoice": "abc"})
    assert "Număr de factură greșit" in r.get_data(as_text=True)


def test_daily_tick_announces_dsv_payment(client, server, monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(automation, "TICK_FILE", tmp_path / "tick")
    monkeypatch.setattr(automation, "vapid", lambda raw=None: ("x", "y"))
    client.post("/push/abonare", json={"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "k", "auth": "a"}})
    server.add_mail("Extras de cont", [("Extras de cont.pdf", pdf(b"BCR"))], day=3)
    automation.tick(views_auto.HOOKS, now=datetime(2026, 11, 9, 7, 30, tzinfo=RO),
                    sender=lambda sub, msg, *a: sent.append(msg) or 201)
    assert any(m["title"] == "DSV a plătit 5.929,00 RON" and "CIR2018" in m["body"] for m in sent)
