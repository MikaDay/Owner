from datetime import date

from deadlines import add_working_days, docs_deadline, holidays, orthodox_easter, payment_due


def test_orthodox_easter_known_years():
    assert orthodox_easter(2024) == date(2024, 5, 5)
    assert orthodox_easter(2025) == date(2025, 4, 20)
    assert orthodox_easter(2026) == date(2026, 4, 12)


def test_holidays_include_moving_and_fixed_days():
    h = holidays(2026)
    assert date(2026, 4, 10) in h and date(2026, 4, 13) in h      # Vinerea Mare, a doua zi de Paști
    assert date(2026, 5, 31) in h and date(2026, 6, 1) in h       # Rusalii (și 1 iunie)
    assert date(2026, 12, 1) in h and date(2026, 1, 24) in h


def test_docs_deadline_skips_weekend_and_holidays():
    # Descărcare joi 01.10.2026 → 5 zile lucrătoare: vi 2, lu 5, ma 6, mi 7, jo 8.
    assert docs_deadline(date(2026, 10, 1)) == date(2026, 10, 8)
    # Joi 26.11.2026: vi 27, lu 30 (Sf. Andrei) și ma 1 dec sunt libere → mi 2, jo 3, vi 4.
    assert add_working_days(date(2026, 11, 26), 4) == date(2026, 12, 4)
    assert docs_deadline(date(2026, 10, 1), international=True) == date(2026, 10, 15)


def test_payment_due_is_45_days():
    assert payment_due(date(2026, 10, 8)) == date(2026, 11, 22)
    assert payment_due(None) is None
