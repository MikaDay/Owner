import email
import email.policy
from datetime import datetime

import pytest

import automation
import journal
import views_auto
import web_app
import webpush
from dsv_pdf import dsv_order, plain_pdf
from new_orders import RO
from test_web import journal as journal_of


@pytest.fixture
def pushes(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "x")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "y")
    monkeypatch.setattr(automation, "TICK_FILE", tmp_path / "tick")

    def fake_send(sub, message, private, public):
        sent.append((sub["endpoint"], message))
        return 410 if "gone" in sub["endpoint"] else 201
    monkeypatch.setattr(webpush, "send", fake_send)
    monkeypatch.setattr(automation.webpush, "send", fake_send)
    return sent


def subscribe(client, endpoint="https://web.push.apple.com/abc"):
    r = client.post("/push/abonare", json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}})
    assert r.get_json() == {"ok": True}


def tick(at):
    return automation.tick(views_auto.HOOKS, now=at, sender=webpush.send)


def test_new_orders_are_announced_once(client, server, pushes):
    subscribe(client)
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001"))], day=1)
    tick(datetime(2026, 11, 9, 3, tzinfo=RO))
    assert [m["title"] for _, m in pushes] == ["1 comandă nouă"]
    tick(datetime(2026, 11, 9, 3, 30, tzinfo=RO))
    assert len(pushes) == 1                                        # aceeași comandă nu se anunță din nou
    server.add_mail("FW: ARWY0002", [("b.pdf", dsv_order(order_no="ARWY0002"))], day=2)
    tick(datetime(2026, 11, 9, 4, tzinfo=RO))
    assert pushes[-1][1]["title"] == "2 comenzi noi" and "ARWY0002" in pushes[-1][1]["body"]


def test_orders_wait_until_a_phone_subscribes(client, server, pushes):
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001"))], day=1)
    tick(datetime(2026, 11, 9, 3, tzinfo=RO))                      # niciun telefon abonat încă
    assert pushes == []
    subscribe(client)
    tick(datetime(2026, 11, 9, 3, 30, tzinfo=RO))
    assert [m["title"] for _, m in pushes] == ["1 comandă nouă"]


def test_dead_subscriptions_are_removed(client, server, pushes):
    subscribe(client, "https://web.push.apple.com/gone")
    subscribe(client)
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001"))], day=1)
    tick(datetime(2026, 11, 9, 3, tzinfo=RO))
    assert [s["endpoint"] for s in journal_of(server)["push"]] == ["https://web.push.apple.com/abc"]


def test_auto_send_at_chosen_hour_then_daily_summary(client, server, pushes):
    subscribe(client)
    client.post("/setari", data={"notify_new": "1", "notify_daily": "1", "auto_send": "1", "daily_hour": "7"})
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001", toll=True))], day=1)
    server.add_mail("FW: ARWY0002", [("b.pdf", dsv_order(order_no="ARWY0002"))], day=2)
    server.add_mail("Facturi", [("f.pdf", plain_pdf())], day=3)
    tick(datetime(2026, 11, 9, 6, tzinfo=RO))                      # înainte de 7: doar notificarea
    assert not journal_of(server).get("orders")
    tick(datetime(2026, 11, 9, 7, 5, tzinfo=RO))
    j = journal_of(server)
    assert set(j["orders"]) == {"ARWY0001", "ARWY0002"} and j["sends"][0]["user"] == "automat"
    titles = [m["title"] for _, m in pushes]
    assert "Am trimis 2 comenzi pe mail" in titles and "Azi" in titles
    subjects = [email.message_from_bytes(raw, policy=email.policy.default)["Subject"] for raw in server.folders["INBOX"].values()]
    assert any(s.startswith("Comenzi Toll_RO") for s in subjects)
    n = len(pushes)
    tick(datetime(2026, 11, 9, 8, tzinfo=RO))                      # o singură dată pe zi
    assert len(pushes) == n


def test_monthly_report_lands_in_inbox(client, server, pushes):
    subscribe(client)
    server.add_mail("FW: ARWY0001", [("a.pdf", dsv_order(order_no="ARWY0001"))], day=1)
    tok = web_app  # noqa
    from conftest import follow_job
    t = follow_job(client, client.get("/noi")).rsplit("/", 1)[1]
    client.post(f"/export/{t}", data={"use_0": "1", "mode": "mail"})   # comandă cu încărcare 30.09, descărcare 01.10
    tick(datetime(2026, 11, 1, 7, 30, tzinfo=RO))
    mails = [email.message_from_bytes(raw, policy=email.policy.default) for raw in server.folders["INBOX"].values()]
    report = [m for m in mails if m["Subject"].startswith("Registru transporturi - octombrie 2026")]
    assert report and next(report[0].iter_attachments()).get_filename() == "registru_2026-10.xlsx"
    assert any(m["title"] == "Registrul pe octombrie" for _, m in pushes)


def test_mail_password_problem_is_announced(client, server, pushes):
    subscribe(client)
    journal.read()                                                  # copia locală are abonamentele
    server.fail_login = True
    assert tick(datetime(2026, 11, 9, 9, tzinfo=RO)) == ["eroare autentificare mail"]
    assert pushes[-1][1]["title"] == "Mailul nu mai merge"


def test_cron_endpoint_token_and_throttle(client, server, pushes, monkeypatch):
    anon = web_app.app.test_client()
    monkeypatch.setenv("CRON_TOKEN", "secret")
    assert anon.post("/cron/tick?sync=1").status_code == 403
    assert anon.post("/cron/tick?sync=1", headers={"X-Cron-Token": "secret"}).status_code == 200
    assert anon.post("/cron/tick?sync=1", headers={"X-Cron-Token": "secret"}).get_data(as_text=True) == "prea devreme\n"


def test_settings_page_and_service_worker(client, server, monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    html = client.get("/setari").get_data(as_text=True)
    assert "Notificări pe acest telefon" in html and "Trimite automat" in html
    keys = journal_of(server)["vapid"]                              # generate de server, o singură dată
    assert keys["public"] in html and keys["private"] not in html
    client.get("/setari")
    assert journal_of(server)["vapid"] == keys
    r = web_app.app.test_client().get("/sw.js")
    assert r.status_code == 200 and r.headers["Service-Worker-Allowed"] == "/" and b"showNotification" in r.data
    assert web_app.app.test_client().get("/ping").data == b"ok\n"
