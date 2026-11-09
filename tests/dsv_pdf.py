"""PDF-uri DSV de test, cu aceeași structură ca cele reale (fără date reale):
pagini de detalii, 2 pagini ePOD, 3 pagini de termeni și condiții."""

import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def _page(c, lines):
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 14
    c.showPage()


def dsv_order(order_no="ARWY01981", price="2.300,00", currency="RON", toll=False, version=1,
              order_date="30.09.2026", loading=("Altex Depozit Central", "077096 Dragomiresti Deal", "30.09.2026"),
              unloading=("ALTEX TIMISOARA", "300643 TIMISOARA", "01.10.2026"), country="ROMANIA",
              truck="TM 12 YEC", goods="1 TRUNK Electrocasnice 20000 kg 13.60", extra_detail_pages=0,
              epod="https://www.dsv.com/mydsv/e-pod?token=TESTTOKEN123"):
    pages_total = 1 + extra_detail_pages + 2 + 3
    head = ["Comanda Transport", "DSV Road S.R.L This document has been created electronically.",
            f"versiune {version} - {order_date} 10:20"]
    first = head + [f"Pagina 1 / {pages_total}", "DSV Road S.R.L", "Calea Zimandului 1/A", "310210 Arad",
                    "VAT No: RO46318372", "Nume: Ilie Rumega", "Telefon: +40 700 000 000",
                    f"Comanda Transport Nr. {order_no}", epod,
                    "CIRFORVEST SRL", "VAT No: RO17204455",
                    "DSV Ref.: ROARW609003820", "Nr. Comanda: S6422000234148039926",
                    "1. Loc de incarcare Ref. incarcare:", loading[0], loading[1], "ROMANIA",
                    f"Data incarcare: {loading[2]}",
                    "Bucati Tip Ambalaj Descriere Greutate LDM", goods,
                    "1. Loc de descarcare Ref. descarcare: TM 12 YEC",
                    *(["taxa Toll_RO se va factura separat"] if toll else []),
                    unloading[0], unloading[1], country, f"Data descarcare: {unloading[2]}"]
    if not extra_detail_pages:
        first += [f"Transport: {price} {currency} toate in Instructiuni:",
                  f"Numar de inmatriculare camion: {truck}"]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _page(c, first)
    for k in range(extra_detail_pages):
        lines = head + [f"Pagina {k + 2} / {pages_total}", f"{k + 2}. Loc de descarcare Ref. descarcare:",
                        "PROCTER & GAMBLE DISTRIBUTION SRL", "307160 Dumbravita, Jud. TM", "ROMANIA",
                        f"Data descarcare: {unloading[2]}"]
        if k == extra_detail_pages - 1:
            lines += [f"Transport: {price} {currency} toate in Instructiuni:",
                      f"Numar de inmatriculare camion: {truck}"]
        _page(c, lines)
    _page(c, head + ["Platforma ePOD - Utilizare", "1. Apasati pe Link"])
    _page(c, head + ["5. Pentru a selecta afisarea informatiilor in limba romana"])
    for k in range(3):
        _page(c, head + ["TERMENI SI CONDITII GENERALE IN RELATIA CU TRANSPORTATORII:",
                         "documentele vor ajunge in maxim 5 zile lucratoare"])
    c.save()
    return buf.getvalue()


def plain_pdf(lines=("Factura 123", "Total 100 RON"), pages=1):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for _ in range(pages):
        _page(c, list(lines))
    c.save()
    return buf.getvalue()
