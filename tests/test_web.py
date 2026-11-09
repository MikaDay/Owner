import io
import json
import email
import email.policy

from conftest import follow_job
from dsv_pdf import dsv_order, plain_pdf

import web_app


def test_login_required_and_wrong_password():
    c = web_app.app.test_client()
    assert c.get("/").status_code == 302
    r = c.post("/login", data={"password": "gresit"})
    assert "Utilizator sau parolă greșită" in r.get_data(as_text=True)


def test_login_blocked_after_five_wrong_passwords():
    c = web_app.app.test_client()
    for _ in range(5):
        c.post("/login", data={"password": "gresit"})
    r = c.post("/login", data={"password": "parola#1"})          # chiar și parola bună e blocată
    assert "Prea multe parole greșite" in r.get_data(as_text=True)
    r = c.post("/api/upload", data={"password": "parola#1"})
    assert r.status_code == 429


def test_login_returns_to_requested_page_but_not_to_other_sites():
    c = web_app.app.test_client()
    r = c.get("/scurtatura")
    assert "next=" in r.headers["Location"]
    r = c.post("/login", data={"password": "parola#1", "next": "/scurtatura"})
    assert r.headers["Location"].endswith("/scurtatura")
    c2 = web_app.app.test_client()
    r = c2.post("/login", data={"password": "parola#1", "next": "//evil.example/x"})
    assert r.headers["Location"].endswith("/")


def upload(client, *pdfs):
    data = {"pdf": [(io.BytesIO(p), f"doc{i}.pdf") for i, p in enumerate(pdfs)]}
    r = client.post("/upload", data=data, content_type="multipart/form-data")
    return r.headers["Location"].rsplit("/", 1)[1]


def test_upload_review_groups_toll_and_names():
    c = web_app.app.test_client()
    c.post("/login", data={"password": "parola#1"})
    tok = upload(c, dsv_order(), dsv_order(order_no="ARWY09999", toll=True))
    html = c.get(f"/review/{tok}").get_data(as_text=True)
    assert "Cu Toll_RO" in html and "Fără Toll_RO" in html
    assert 'value="Comanda ARWY09999"' in html and 'value="Comanda ARWY01981"' in html


def test_export_requires_login(client):
    tok = upload(client, dsv_order())
    anon = web_app.app.test_client()
    r = anon.post(f"/export/{tok}", data={"use_0": "1", "mode": "zip"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def mail_job(client, server):
    server.add_mail("FW: ARWY01981 CIRFORVEST", [("CarrierContract_ARWY01981.pdf", dsv_order())], day=1)
    server.add_mail("ARW-09999 urgent", [("CarrierContract_ARWY09999.pdf", dsv_order(order_no="ARWY09999", toll=True))], day=2)
    server.add_mail("Facturi", [("Facturi.pdf", plain_pdf())], day=3)
    loc = follow_job(client, client.get("/noi"))
    assert "/review/" in loc, loc
    return loc.rsplit("/", 1)[1]


def journal(server):
    box = server.folders.get("Cirforvest", {})
    if not box:
        return None
    raw = list(box.values())[-1]
    return json.loads(email.message_from_bytes(raw).get_payload(decode=True))


def test_excel_and_zip_do_not_mark_orders_as_downloaded(client, server):
    tok = mail_job(client, server)
    for mode in ("xlsx", "zip"):
        r = client.post(f"/export/{tok}", data={"use_0": "1", "use_1": "1", "mode": mode})
        assert r.status_code == 200
    assert journal(server) is None
    assert not any(server.is_seen(u) for u in server.folders["INBOX"])


def test_send_by_mail_two_mails_toll_first_and_journal(client, server):
    tok = mail_job(client, server)
    before = set(server.folders["INBOX"])
    r = client.post(f"/export/{tok}", data={"use_0": "1", "use_1": "1", "mode": "mail"})
    assert "Trimis pe mail" in r.get_data(as_text=True)
    new = [email.message_from_bytes(server.folders["INBOX"][u], policy=email.policy.default) for u in sorted(server.folders["INBOX"], key=int)
           if u not in before]
    assert [m["Subject"].split(" - ")[0] for m in new] == ["Comenzi Toll_RO", "Comenzi fără Toll_RO"]
    j = journal(server)
    assert j and len(j["downloaded"]) == 2
    # Factura a rămas necitită; comenzile sunt citite.
    seen = {u: server.is_seen(u) for u in before}
    assert sum(seen.values()) == 2
    # A doua apăsare nu mai găsește nimic.
    loc = follow_job(client, client.get("/noi"))
    assert "/rezultat/" in loc
    assert "Nu sunt comenzi noi" in client.get(loc).get_data(as_text=True)


def test_mail_errors_are_explained(client, server):
    server.fail_login = True
    loc = follow_job(client, client.get("/noi"))
    assert "parolă de aplicație" in client.get(loc).get_data(as_text=True)


def test_mail_view_shows_text_and_any_attachment(client, server):
    from email.message import EmailMessage
    m = EmailMessage()
    m["Subject"], m["From"], m["Message-ID"] = "RE: Plata inexistenta", "x@dsv.com", "<p1@dsv>"
    m.set_content("Factura 123 nu apare in sistem.")
    m.add_attachment(b"PK\x03\x04xlsx", maintype="application",
                     subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="situatie.xlsx")
    uid = server.add("INBOX", m.as_bytes())
    html = client.get(f"/mail/{uid.decode()}").get_data(as_text=True)
    assert "Factura 123 nu apare" in html and "situatie.xlsx" in html
    r = client.get(f"/mail/{uid.decode()}?fisier=0")
    assert r.data.startswith(b"PK") and "situatie.xlsx" in r.headers["Content-Disposition"]
    assert not server.is_seen(uid)
