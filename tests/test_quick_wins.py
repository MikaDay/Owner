import email
import email.policy
import io
import re
import zipfile
from datetime import date, timedelta

from pypdf import PdfReader

import web_app
from conftest import follow_job
from dsv_pdf import dsv_order, plain_pdf
from test_web import journal


def prepare(client, server, *orders):
    for n, pdf in enumerate(orders):
        server.add_mail(f"FW: comanda {n}", [(f"c{n}.pdf", pdf)], day=n + 1)
    loc = follow_job(client, client.get("/noi"))
    assert "/review/" in loc, client.get(loc).get_data(as_text=True)
    return loc.rsplit("/", 1)[1], client.get(loc).get_data(as_text=True)


def d(days):
    return (date.today() + timedelta(days=days)).strftime("%d.%m.%Y")


def test_review_sorted_by_loading_with_urgent_labels(client, server):
    tok, html = prepare(client, server,
                        dsv_order(order_no="ARWY0003", loading=("X SRL", "300100 Timisoara", d(5))),
                        dsv_order(order_no="ARWY0001", loading=("X SRL", "300100 Timisoara", d(0))),
                        dsv_order(order_no="ARWY0002", loading=("X SRL", "300100 Timisoara", d(1))))
    names = re.findall(r'value="Comanda (ARWY\d+)"', html)
    assert names == ["ARWY0001", "ARWY0002", "ARWY0003"]
    assert "Încărcare AZI" in html and "Încărcare mâine" in html


def test_mail_pdf_follows_loading_order(client, server):
    tok, html = prepare(client, server,
                        dsv_order(order_no="ARWY0002", loading=("X SRL", "300100 Timisoara", d(3))),
                        dsv_order(order_no="ARWY0001", loading=("X SRL", "300100 Timisoara", d(2))))
    before = set(server.folders["INBOX"])
    client.post(f"/export/{tok}", data={"use_0": "1", "use_1": "1", "mode": "mail"})
    new = [u for u in sorted(server.folders["INBOX"], key=int) if u not in before]
    msg = email.message_from_bytes(server.folders["INBOX"][new[0]], policy=email.policy.default)
    pdf = next(msg.iter_attachments()).get_payload(decode=True)
    first = [PdfReader(io.BytesIO(pdf)).pages[k].extract_text() for k in (0, 5)]
    assert "ARWY0001" in first[0] and "ARWY0002" in first[1]


def test_zip_per_truck_and_driver_sheet(client, server):
    tok, _ = prepare(client, server, dsv_order(order_no="ARWY0001", truck="TM 32 DAR"),
                     dsv_order(order_no="ARWY0002", truck="TM 18 YCW"),
                     dsv_order(order_no="ARWY0003", truck="TM 32 DAR", extra_detail_pages=2))
    r = client.post(f"/export/{tok}", data={"use_0": "1", "use_1": "1", "use_2": "1", "mode": "trucks"})
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert sorted(z.namelist()) == ["TM18YCW.pdf", "TM32DAR.pdf"]
    assert len(PdfReader(io.BytesIO(z.read("TM32DAR.pdf"))).pages) == 5 + 9
    assert journal(server) is None                       # ZIP-ul nu notează descărcarea
    html = client.get(f"/review/{tok}").get_data(as_text=True)
    urls = re.findall(r'data-url="([^"]+/sofer\.pdf)"', html)
    pages = sorted(len(PdfReader(io.BytesIO(client.get(u).data)).pages) for u in urls)
    assert pages == [1, 1, 3]                            # doar detaliile, o singură dată


def test_search_by_order_number_also_finds_old_read_orders(client, server):
    server.add_mail("FW: ARWY02628", [("a.pdf", dsv_order(order_no="ARWY02628"))], day=1, seen=True)
    server.add_mail("FW: ARWY02999", [("b.pdf", dsv_order(order_no="ARWY02999"))], day=2, seen=True)
    loc = follow_job(client, client.get("/cauta?nr=ARWY02628"))
    html = client.get(loc).get_data(as_text=True)
    assert 'value="Comanda ARWY02628"' in html and "ARWY02999" not in html
    loc = follow_job(client, client.get("/cauta?nr=ARWY00000"))
    assert "Nu am găsit ARWY00000" in client.get(loc).get_data(as_text=True)


def test_history_new_count_and_calendar(client, server):
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001", toll=True))], day=1)
    server.add_mail("FW: ARWY0002", [("b.pdf", dsv_order(order_no="ARWY0002"))], day=2)
    assert client.get("/api/noi").get_json()["count"] == 2
    tok = follow_job(client, client.get("/noi")).rsplit("/", 1)[1]
    client.post(f"/export/{tok}", data={"use_0": "1", "use_1": "1", "mode": "mail"})
    assert client.get("/api/noi").get_json()["count"] == 0
    html = client.get("/istoric").get_data(as_text=True)
    assert "2 comenzi" in html and "1 Toll_RO" in html and "nr=ARWY0001" in html
    anon = web_app.app.test_client()
    assert anon.get("/calendar.ics?key=gresit").status_code == 403
    ics = anon.get(f"/calendar.ics?key={web_app.calendar_key()}").get_data(as_text=True)
    assert ics.count("BEGIN:VEVENT") == 6                # încărcare, descărcare, termen × 2
    assert "SUMMARY:Termen documente ARWY0001 · TM12YEC" in ics and "DTSTART;VALUE=DATE:20261001" in ics


def test_unknown_format_and_skipped_mails_are_explained(client, server):
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001", price="", truck=""))], day=1)
    server.add_mail("Situatie facturi", [("f.pdf", plain_pdf())], day=2)
    loc = follow_job(client, client.get("/noi"))
    html = client.get(loc).get_data(as_text=True)
    assert "Nu am putut citi din PDF: prețul, camionul" in html
    assert "fără comandă de transport recunoscută („Situatie facturi”)" in html
