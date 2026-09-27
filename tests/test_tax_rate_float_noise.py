"""A tax rate typed as a percent keeps its value (2.18.0, found while
folding in #192). The forms divide the typed percent by 100 in floating
point and show the stored fraction times 100, so California's 7.25% went
out as 0.07249999999999999 and came back into the field as
7.249999999999999. The server cuts a rate to the six places a document
stores (a percent to four, 8.875% included), so no path can work tax out
from a rate a hair under the one stored, or from one finer than it, and
the forms show the percent as typed."""

from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def test_a_noisy_rate_taxes_as_the_rate_it_means(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "tax_rate": 0.07249999999999999,
            "lines": [{"description": "Loaf", "quantity": 1, "rate": 10}],
        },
    )
    assert r.status_code == 201, r.text
    # 7.25% of $10.00 is $0.725: half up, $0.73, not $0.72
    assert r.json()["tax_amount"] in (0.73, "0.73")


def test_a_rate_is_kept_to_the_six_places_a_document_stores():
    from decimal import Decimal

    from app.schemas.common import _check_tax_rate

    # 8.875% and 7.0625% are kept as they are
    assert _check_tax_rate(Decimal("0.08875")) == Decimal("0.08875")
    assert _check_tax_rate(0.08875) == 0.08875
    assert _check_tax_rate(Decimal("0.070625")) == Decimal("0.070625")
    # float noise goes
    assert _check_tax_rate(0.08900000000000001) == 0.089
    assert _check_tax_rate(0.07062500000000001) == 0.070625
    # anything finer than six places rounds half up, a float by its digits
    assert _check_tax_rate(Decimal("0.0888755")) == Decimal("0.088876")
    assert _check_tax_rate(Decimal("0.08887549")) == Decimal("0.088875")
    assert _check_tax_rate(0.0000005) == 0.000001
    assert _check_tax_rate(0.0888755) == 0.088876


def test_a_rate_that_is_not_a_number_is_refused():
    from app.schemas.common import _check_tax_rate

    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="Tax rate must be a number"):
            _check_tax_rate(value)


def test_the_forms_show_the_percent_as_typed():
    for name, var in (
        ("invoices.js", "inv"),
        ("estimates.js", "est"),
        ("recurring.js", "rec"),
        ("purchase_orders.js", "po"),
        ("sales_receipts.js", "sr"),
        ("credit_memos.js", "inv"),  # the rate of the invoice being credited
    ):
        js = (JS / name).read_text(encoding="utf-8")
        assert f"+(({var}.tax_rate || 0) * 100).toFixed(4)" in js, name
        assert f"({var}.tax_rate * 100) || 0" not in js, name
        assert "tax_rate || 0) * 10000) / 100" not in js, name


def test_a_rate_that_is_nan_is_refused_as_a_422_not_a_500(
    client, seed_accounts, seed_customer
):
    # Python's JSON reader takes NaN; the refusal echoed it back, and JSON
    # can't carry it, so the 422 became a 500.
    r = client.post(
        "/api/invoices",
        content=(
            '{"customer_id": %d, "date": "2026-09-01", "tax_rate": NaN, '
            '"lines": [{"description": "Loaf", "quantity": 1, "rate": 10}]}'
            % seed_customer.id
        ),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422, r.text
    assert "nan" in r.text.lower()
