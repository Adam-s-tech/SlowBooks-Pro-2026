"""A negative amount prints "-$10.00" on a document, not "$-10.00" (2.18.0).

A discount line from QuickBooks Online is a negative line, and the money
filters every document template shares put the minus after the dollar
sign: "$-10.00" on the invoice PDF, its print preview and the sales
receipt. The filters now put the sign first, as the app's pages do
(formatCurrency: "-$10.00"); every positive amount prints as it did.
"""

from decimal import Decimal

import pytest

from app.services import pdf_service
from tests.test_qbo_import_discounts import Discounts, _discount, _discounted
from tests.test_qbo_import_tax import _line, _tax


def test_the_money_filters_put_the_minus_before_the_dollar_sign():
    assert pdf_service._format_currency(-10) == "-$10.00"
    assert pdf_service._format_currency(Decimal("-1234.5")) == "-$1,234.50"
    assert pdf_service._format_currency(-10, "EUR") == "EUR -10.00"
    assert pdf_service._format_rate(Decimal("-10.0000")) == "-$10.00"
    assert pdf_service._format_rate(Decimal("-0.0450")) == "-$0.045"
    assert pdf_service._format_rate(Decimal("-0.0450"), "CAD") == "CAD -0.045"
    # an amount that rounds to nothing has no sign
    assert pdf_service._format_currency(-0.004) == "$0.00"


def test_every_positive_amount_prints_as_it_did():
    assert [
        pdf_service._format_currency(v)
        for v in (0, None, 850, Decimal("1234.5"), 10824891.75)
    ] == ["$0.00", "$0.00", "$850.00", "$1,234.50", "$10,824,891.75"]
    assert pdf_service._format_currency(850, "EUR") == "EUR 850.00"
    assert [
        pdf_service._format_rate(Decimal(v)) for v in ("12.5000", "0.0450", "1234.5")
    ] == ["$12.50", "$0.045", "$1,234.50"]


@pytest.fixture
def books(db_session, seed_accounts, monkeypatch):
    return Discounts(db_session, seed_accounts, monkeypatch)


def test_a_discount_prints_with_its_minus_first(client, books):
    lines = [_line(1, 100, "Catering", "TAX")]
    tax = _tax(8.01, (8.9, 90, 8.01))
    books.invoices(
        _discounted("180", "1080", lines + [_discount(2, 10, 10)], 98.01, tax, True)
    )
    books.sales_receipts(
        _discounted("181", "SR-18", lines + [_discount(2, 10, 10)], 98.01, tax, True)
    )
    for number in ("1080", "SR-18"):
        html = client.get(f"/api/invoices/{books.get(number).id}/print-preview").text
        assert html.count("-$10.00") == 2, number  # its price and its amount
        assert "$-" not in html, number
        assert "$98.01" in html, number
