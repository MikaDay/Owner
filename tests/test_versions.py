import re

from conftest import follow_job
from dsv_pdf import dsv_order
from test_web import journal


def review_html(client, server, *mails):
    for n, (subject, pdf) in enumerate(mails):
        server.add_mail(subject, [(f"c{n}.pdf", pdf)], day=n + 1)
    loc = follow_job(client, client.get("/noi"))
    assert "/review/" in loc, client.get(loc).get_data(as_text=True)
    return loc.rsplit("/", 1)[1], client.get(loc).get_data(as_text=True)


def checked(html):
    return dict(re.findall(r'name="use_(\d+)" value="1" (checked)?', html))


def send(client, tok, idx):
    client.post(f"/export/{tok}", data={**{f"use_{i}": "1" for i in idx}, "mode": "mail"})


def test_same_order_twice_in_one_batch_keeps_newest_version(client, server):
    tok, html = review_html(client, server, ("FW: ARWY1", dsv_order(order_no="ARWY02628")),
                            ("FW: ARWY1 v2", dsv_order(order_no="ARWY02628", version=2, price="2.450,00")))
    assert "apare de două ori" in html
    assert sorted(checked(html).values()) == ["", "checked"]


def test_resent_unchanged_order_is_a_duplicate_and_unchecked(client, server):
    tok, _ = review_html(client, server, ("FW: ARWY01981", dsv_order()))
    send(client, tok, [0])
    # DSV trimite iar același mail (necitit) — sau omul îl caută manual.
    server.add_mail("FW: ARWY01981 (retrimis)", [("x.pdf", dsv_order())], day=5)
    loc = follow_job(client, client.get("/noi"))
    html = client.get(loc).get_data(as_text=True)
    assert "duplicat" in html and checked(html) == {"0": ""}


def test_new_version_shows_changes_and_records_history(client, server):
    tok, _ = review_html(client, server, ("FW: ARWY03166", dsv_order(order_no="ARWY03166")))
    send(client, tok, [0])
    server.add_mail("FW: ARWY03166 modificata", [("v2.pdf", dsv_order(
        order_no="ARWY03166", version=2, price="2.450,00",
        unloading=("ALTEX TIMISOARA", "300643 TIMISOARA", "02.10.2026")))], day=6)
    loc = follow_job(client, client.get("/noi"))
    html = client.get(loc).get_data(as_text=True)
    assert "MODIFICATĂ de DSV (versiunea 2" in html and "Preț: 2.300,00 → 2.450,00" in html
    assert "Data descărcării: 01.10.2026 → 02.10.2026" in html
    assert checked(html) == {"0": "checked"}
    send(client, loc.rsplit("/", 1)[1], [0])
    o = journal(server)["orders"]["ARWY03166"]
    assert o["version"] == 2 and o["price"] == 2450.0 and o["history"][0]["version"] == 2
    assert "Modificări de la DSV" in client.get("/comanda/ARWY03166").get_data(as_text=True)
