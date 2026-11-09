import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Setările se citesc la importul aplicației, deci le punem înainte.
os.environ.update(MAIL_USER="cirforvest17@yahoo.com", MAIL_PASSWORD="app-pass", SITE_PASSWORD="parola#1",
                  SECRET_KEY="test", HTTPS="0", FILTER_FROM="ilie.rumega@dsv.com", FILTER_SUBJECT="",
                  CRON_TOKEN="cron-secret", DATA_DIR=tempfile.mkdtemp())

import fake_imap  # noqa: E402

fake_imap.install()

import web_app  # noqa: E402


_REAL_THREAD = web_app.threading.Thread


class _RunNow:
    """Pregătirile „din fundal” rulează imediat în teste."""
    def __init__(self, target=None, daemon=None, **_):
        self.target = target

    def start(self):
        # Într-un fir adevărat, ca în producție: fără contextul cererii (url_for ar eșua).
        t = _REAL_THREAD(target=self.target)
        t.start()
        t.join()


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    fake_imap.SERVER.__init__()
    monkeypatch.setattr(web_app.threading, "Thread", _RunNow)
    monkeypatch.setattr(web_app, "RATE_FILE", tmp_path / "attempts.json")
    monkeypatch.setattr(web_app, "STATE", tmp_path / "downloaded.json")
    import journal
    monkeypatch.setattr(journal, "CACHE_FILE", tmp_path / "journal.json")
    monkeypatch.setattr(journal, "LOCK_FILE", tmp_path / "journal.lock")
    yield


@pytest.fixture
def server():
    return fake_imap.SERVER


@pytest.fixture
def client():
    c = web_app.app.test_client()
    r = c.post("/login", data={"password": "parola#1"})
    assert r.status_code == 302
    return c


def follow_job(client, response):
    """/noi și /prepare duc la pagina de progres; întoarce adresa finală (review sau rezultat)."""
    assert response.status_code == 302, response.status_code
    loc = response.headers["Location"]
    if "/progres/" in loc:
        st = client.get("/status/" + loc.rsplit("/", 1)[1]).get_json()
        return st["next"]
    return loc
