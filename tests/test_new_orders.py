from datetime import datetime, timedelta, timezone

from new_orders import AFTER_LAST, BETWEEN, UNREAD, pick_new

T0 = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)


def m(n, day, unread):
    return {"uid": str(n), "message_id": f"<{n}>", "unread": unread, "date": T0 + timedelta(days=day)}


def picked(msgs, state):
    return [(x["uid"], why) for x, why in pick_new(msgs, state)]


def test_read_between_unread_are_taken():
    msgs = [m(1, 0, False), m(2, 1, True), m(3, 2, False), m(4, 3, False), m(5, 4, True), m(6, 5, False)]
    assert picked(msgs, {"last_download": None, "downloaded": {}}) == [
        ("2", UNREAD), ("3", BETWEEN), ("4", BETWEEN), ("5", UNREAD)]


def test_read_after_last_download_are_taken_downloaded_never():
    last = T0 + timedelta(days=6)
    msgs = [m(1, 0, False), m(7, 6.5, False), m(8, 7, True), m(9, 8, True)]
    state = {"last_download": last, "downloaded": {"<9>": last.isoformat()}}
    assert picked(msgs, state) == [("7", AFTER_LAST), ("8", UNREAD)]
