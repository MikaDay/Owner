from datetime import date

from dsv_pdf import dsv_order
from process_pdf import details_text
from transport_order import is_transport_order, parse_order


def parsed(**kw):
    return parse_order(details_text(dsv_order(**kw)))


def test_fields_from_dsv_order():
    d = parsed()
    assert d["order_no"] == "ARWY01981"
    assert d["issuer"] == "DSV Road S.R.L"
    assert d["loading"] == "Altex Depozit Central, Dragomiresti Deal"
    assert d["unloading"] == "ALTEX TIMISOARA, Timisoara"
    assert d["loading_date"] == date(2026, 9, 30) and d["unloading_date"] == date(2026, 10, 1)
    assert d["price"] == 2300.0 and d["currency"] == "RON"
    assert d["weight_kg"] == 20000 and d["ldm"] == 13.6 and d["goods"] == "Electrocasnice"
    assert d["truck"] == "TM12YEC" and d["toll_ro"] == ""
    assert d["contact"] == "Ilie Rumega"


def test_toll_note_is_not_a_company_name():
    d = parsed(toll=True)
    assert d["toll_ro"] == "DA"
    assert "Toll" not in d["unloading"]


def test_county_and_goods_without_description():
    d = parsed(unloading=("PROCTER & GAMBLE DISTRIBUTION SRL", "307160 Dumbravita, Jud. TM", "19.10.2026"),
               goods="1 TRUNK 13000 kg 13.60")
    assert d["unloading"].endswith("Dumbravita (TM)")
    assert d["goods"] == "" and d["weight_kg"] == 13000


def test_price_on_a_later_detail_page():
    d = parsed(extra_detail_pages=2, price="400,00")
    assert d["price"] == 400.0 and d["truck"] == "TM12YEC"


def test_is_transport_order():
    assert is_transport_order(details_text(dsv_order()))
    assert not is_transport_order("Factura 123 Total 100 RON")
