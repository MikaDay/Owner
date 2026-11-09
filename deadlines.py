"""Termenele din condițiile DSV.

- Documentele de transport originale trebuie să ajungă la DSV „in maxim 5 zile lucratoare pentru
  transportul intern si 10 zile lucratoare pentru transportul international, dupa descarcare”.
- „Termenul de plată este de 45 de zile calendaristice de la data primirii documentelor”.

Zilele lucrătoare sar peste weekend și peste sărbătorile legale din România.
"""

from datetime import date, timedelta

DOCS_DAYS_DOMESTIC = 5
DOCS_DAYS_INTERNATIONAL = 10
PAYMENT_DAYS = 45


def orthodox_easter(year):
    """Duminica Paștelui ortodox (algoritmul Meeus pentru calendarul iulian, +13 zile, valabil 1900–2099)."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


def holidays(year):
    """Sărbătorile legale nelucrătoare din România (Codul muncii, art. 139)."""
    easter = orthodox_easter(year)
    fixed = [(1, 1), (1, 2), (1, 6), (1, 7), (1, 24), (5, 1), (6, 1), (8, 15), (11, 30), (12, 1),
             (12, 25), (12, 26)]
    days = {date(year, m, d) for m, d in fixed}
    days |= {easter - timedelta(days=2), easter, easter + timedelta(days=1),          # Vinerea Mare, Paștele
             easter + timedelta(days=49), easter + timedelta(days=50)}               # Rusaliile
    return days


def is_working_day(d):
    return d.weekday() < 5 and d not in holidays(d.year)


def add_working_days(start, n):
    d = start
    while n > 0:
        d += timedelta(days=1)
        if is_working_day(d):
            n -= 1
    return d


def docs_deadline(unloading_date, international=False):
    """Ziua până la care documentele trebuie să ajungă la DSV."""
    if not unloading_date:
        return None
    return add_working_days(unloading_date, DOCS_DAYS_INTERNATIONAL if international else DOCS_DAYS_DOMESTIC)


def payment_due(docs_date):
    """Scadența plății: 45 de zile de la primirea documentelor (aici: de la trimiterea lor, deci aproximativ)."""
    return docs_date + timedelta(days=PAYMENT_DAYS) if docs_date else None
