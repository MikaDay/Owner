import io
import re

import web_app
from dsv_pdf import dsv_order
from test_web import journal


def login(username, password):
    c = web_app.app.test_client()
    r = c.post("/login", data={"username": username, "password": password})
    return c, r.status_code == 302


def test_admin_changes_password_and_adds_users(client, server):
    r = client.post("/setari/parola", data={"current": "parola#1", "new": "noua-parola-9", "repeat": "noua-parola-9"})
    assert "schimbat" in r.headers["Location"] or "msg=" in r.headers["Location"]
    assert not login("", "parola#1")[1] and login("", "noua-parola-9")[1]
    client.post("/setari/utilizatori", data={"action": "add", "name": "Dispecer", "password": "dispecer-123"})
    assert "dispecer" in journal(server)["users"]
    c, ok = login("dispecer", "dispecer-123")
    assert ok and not login("dispecer", "gresit-123")[1]
    assert c.post("/setari/utilizatori", data={"action": "add", "name": "x2", "password": "12345678"}).status_code == 403
    client.post("/setari/utilizatori", data={"action": "remove", "name": "dispecer"})
    assert not login("dispecer", "dispecer-123")[1]


def test_validation_messages(client):
    r = client.post("/setari/parola", data={"current": "parola#1", "new": "scurt", "repeat": "scurt"})
    assert "msg=" in r.headers["Location"]
    html = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "cel puțin 8 caractere" in html
    r = client.post("/setari/utilizatori", data={"action": "add", "name": "admin", "password": "12345678"})
    assert "fără" in client.get(r.headers["Location"]).get_data(as_text=True)


def test_sender_name_is_recorded(client, server):
    client.post("/setari/utilizatori", data={"action": "add", "name": "ana", "password": "parola-ana-1"})
    c, _ = login("ana", "parola-ana-1")
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001"))], day=1)
    from conftest import follow_job
    tok = follow_job(c, c.get("/noi")).rsplit("/", 1)[1]
    c.post(f"/export/{tok}", data={"use_0": "1", "mode": "mail"})
    assert journal(server)["sends"][0]["user"] == "ana" and journal(server)["orders"]["ARWY0001"]["sent_by"] == "ana"


def test_shortcut_key(client, server):
    html = client.post("/setari/cheie-scurtatura").get_data(as_text=True)
    key = re.search(r'<code id="code">([^<]+)</code>', html).group(1)
    anon = web_app.app.test_client()
    data = lambda **kw: {**kw, "pdf": (io.BytesIO(dsv_order()), "a.pdf")}   # noqa: E731
    assert anon.post("/api/upload", data=data(key=key), content_type="multipart/form-data").status_code == 200
    assert anon.post("/api/upload", data=data(key="alta"), content_type="multipart/form-data").status_code == 403
    assert anon.post("/api/upload", data=data(password="parola#1"), content_type="multipart/form-data").status_code == 200


def test_android_share_target(client):
    m = web_app.app.test_client().get("/manifest.webmanifest").get_json()
    assert m["share_target"]["action"] == "/distribuie"
    r = client.post("/distribuie", data={"pdf": (io.BytesIO(dsv_order()), "a.pdf")}, content_type="multipart/form-data")
    assert "/review/" in r.headers["Location"]
    anon = web_app.app.test_client().post("/distribuie", data={"pdf": (io.BytesIO(dsv_order()), "a.pdf")},
                                          content_type="multipart/form-data")
    assert "Intră mai întâi" in anon.get_data(as_text=True)
