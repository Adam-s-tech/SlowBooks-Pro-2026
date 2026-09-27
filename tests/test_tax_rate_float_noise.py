"""A tax rate typed as a percent keeps its value (2.18.0, found while
folding in #192). The forms divide the typed percent by 100 in floating
point and show the stored fraction times 100, so California's 7.25% went
out as 0.07249999999999999 and came back into the field as
7.249999999999999. The server drops the float noise (eight places keep
every real rate, 8.875% included), so no path can work tax out from a rate
a hair under the one stored, and the forms show the percent as typed."""

from pathlib import Path

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


def test_a_rate_with_more_places_is_kept():
    from decimal import Decimal

    from app.schemas.common import _check_tax_rate

    assert _check_tax_rate(Decimal("0.08875")) == Decimal("0.08875")
    assert _check_tax_rate(0.08900000000000001) == 0.089


def test_the_forms_show_the_percent_as_typed():
    for name, var in (
        ("invoices.js", "inv"),
        ("estimates.js", "est"),
        ("recurring.js", "rec"),
        ("purchase_orders.js", "po"),
        ("sales_receipts.js", "sr"),
    ):
        js = (JS / name).read_text(encoding="utf-8")
        assert f"+(({var}.tax_rate || 0) * 100).toFixed(4)" in js, name
        assert f"({var}.tax_rate * 100) || 0" not in js, name
