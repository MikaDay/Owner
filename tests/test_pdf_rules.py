from dsv_pdf import dsv_order, plain_pdf
from process_pdf import analyse, find_order_number, page_order


def test_page_order_normal_and_multi_detail():
    assert page_order(6) == [0, 0, 3, 4, 5]
    assert page_order(8, 2, 3, 3) == [0, 1, 2, 0, 1, 2, 5, 6, 7]
    assert page_order(2, 2, 3, 1) == [0, 0, 1]


def test_analyse_dsv_order_keeps_page_one_twice_and_terms():
    r = analyse(dsv_order())
    assert r["total"] == 6 and r["pages"] == [1, 1, 4, 5, 6]
    assert "ARWY01981" in r["text"]


def test_analyse_detail_on_three_pages():
    r = analyse(dsv_order(extra_detail_pages=2))
    assert r["total"] == 8 and r["pages"] == [1, 2, 3, 1, 2, 3, 6, 7, 8]


def test_unknown_format_falls_back_to_page_one():
    assert analyse(plain_pdf(pages=6))["pages"] == [1, 1, 4, 5, 6]


def test_order_number_patterns():
    assert find_order_number("Comanda Transport Nr. ARWY01981 ... Nr. Comanda: S642200") == "ARWY01981"
    assert find_order_number("Nr. comanda: TR-2026/1458") == "TR-2026/1458"
    assert find_order_number("VAT No: RO46318372 Nume: Telefon:") == ""
