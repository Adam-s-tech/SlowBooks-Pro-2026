"""QuickBooks Online's sales tax comes in with its invoices and sales
receipts (#192 review, 2.18.0).

The import took QBO's tax amount but not its rate, and marked every line
taxable. Editing an imported invoice worked its tax out again from a rate
of 0, so the tax fell to 0.00 and the total with it. Now the import brings
the rate from the tax lines in TxnTaxDetail and each line's taxable flag
from its TaxCodeRef (TAX / NON), so an edit re-totals to what QBO had.
When no single rate gives QBO's tax to the cent, the document keeps QBO's
tax amount, and an edit keeps it as it is.

Built through the python-quickbooks SDK's own objects, as the import reads
them: an invoice's lines are typed, a sales receipt's are plain dicts.
"""

from datetime import date
from decimal import Decimal

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice
from quickbooks.objects.salesreceipt import SalesReceipt as QBOSalesReceipt

from app.models.contacts import Customer
from app.models.invoices import Invoice
from app.models.qbo_mapping import QBOMapping
from app.services import qbo_import
from app.services.bank_register import gl_balances

DAY = date(2026, 8, 3)
CUSTOMER = {"value": "58", "name": "Acme Diner"}


def _line(n, amount, description, code, qty=1):
    return {
        "Id": str(n),
        "Amount": amount,
        "Description": description,
        "DetailType": "SalesItemLineDetail",
        "SalesItemLineDetail": {
            "Qty": qty,
            "UnitPrice": amount / qty,
            "TaxCodeRef": {"value": code},
        },
    }


def _tax(total, *parts, percent_based=True):
    """TxnTaxDetail with one tax line per (percent, taxable amount, tax)."""
    return {
        "TotalTax": total,
        "TaxLine": [
            {
                "Amount": tax,
                "DetailType": "TaxLineDetail",
                "TaxLineDetail": {
                    "TaxRateRef": {"value": str(3 + n)},
                    "PercentBased": percent_based,
                    "TaxPercent": percent,
                    "NetAmountTaxable": base,
                },
            }
            for n, (percent, base, tax) in enumerate(parts)
        ],
    }


def _document(qbo_id, number, lines, total, tax):
    document = {
        "Id": qbo_id,
        "DocNumber": number,
        "TxnDate": DAY.isoformat(),
        "DueDate": DAY.isoformat(),
        "TotalAmt": total,
        "Balance": total,
        "CustomerRef": CUSTOMER,
        "Line": lines,
    }
    if tax is not None:
        document["TxnTaxDetail"] = tax
    return document


# 50.00 taxable, 10.00 not, at 8.9%: 4.45 tax, 64.45 in all.
CATERING = [_line(1, 50, "Catering", "TAX"), _line(2, 10, "Delivery", "NON")]
CATERING_TAX = _tax(4.45, (8.9, 50, 4.45))


class Sales:
    def __init__(self, db, seed_accounts, monkeypatch):
        self.db, self.accounts = db, seed_accounts
        customer = Customer(name="Acme Diner", is_active=True)
        db.add(customer)
        db.flush()
        db.add(
            QBOMapping(entity_type="customer", qbo_id="58", slowbooks_id=customer.id)
        )
        db.add(
            QBOMapping(
                entity_type="account",
                qbo_id="35",
                slowbooks_id=seed_accounts["1200"].id,
            )
        )
        db.flush()
        self.sources = {}
        monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: object())
        monkeypatch.setattr(
            qbo_import,
            "_all_qbo_objects",
            lambda cls, client: self.sources.get(cls, []),
        )

    def invoices(self, *documents):
        self.sources[QBOInvoice] = [QBOInvoice.from_json(d) for d in documents]
        assert qbo_import.import_invoices(self.db)["errors"] == []
        self.db.commit()

    def sales_receipts(self, *documents):
        self.sources[QBOSalesReceipt] = [
            QBOSalesReceipt.from_json({**d, "DepositToAccountRef": {"value": "35"}})
            for d in documents
        ]
        assert qbo_import.import_sales_receipts(self.db)["errors"] == []
        self.db.commit()

    def get(self, number):
        self.db.expire_all()
        return self.db.query(Invoice).filter_by(invoice_number=number).one()

    def balance(self, number):
        account = self.accounts[number].id
        return gl_balances(self.db, [account])[account]


@pytest.fixture
def sales(db_session, seed_accounts, monkeypatch):
    return Sales(db_session, seed_accounts, monkeypatch)


def _save_as_the_form_does(client, invoice_id, **changes):
    """PUT what the invoice form sends back: its header, its rate and its
    lines with their Tax boxes as they were drawn."""
    inv = client.get(f"/api/invoices/{invoice_id}").json()
    body = {
        "customer_id": inv["customer_id"],
        "date": inv["date"],
        "due_date": inv["due_date"],
        "terms": inv["terms"],
        "tax_rate": inv["tax_rate"],
        "notes": inv.get("notes"),
        "lines": [
            {
                "item_id": line["item_id"],
                "description": line["description"],
                "quantity": line["quantity"],
                "rate": line["rate"],
                "is_taxable": line["is_taxable"],
                "line_order": i,
            }
            for i, line in enumerate(inv["lines"])
        ],
    }
    body.update(changes)
    r = client.put(f"/api/invoices/{invoice_id}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_qbo_invoice_comes_in_with_its_tax_rate_and_taxable_lines(sales):
    sales.invoices(_document("140", "1040", CATERING, 64.45, CATERING_TAX))
    invoice = sales.get("1040")
    assert invoice.tax_rate == Decimal("0.0890")
    assert [ln.is_taxable for ln in invoice.lines] == [True, False]
    assert (invoice.tax_amount, invoice.total) == (Decimal("4.45"), Decimal("64.45"))


def test_an_edited_qbo_invoice_re_totals_to_what_qbo_had(client, sales):
    sales.invoices(_document("140", "1040", CATERING, 64.45, CATERING_TAX))
    saved = _save_as_the_form_does(client, sales.get("1040").id, notes="Call first")
    assert (Decimal(str(saved["tax_amount"])), Decimal(str(saved["total"]))) == (
        Decimal("4.45"),
        Decimal("64.45"),
    )
    # its own posting now, tax and all
    assert sales.balance("1100") == Decimal("64.45")
    assert sales.balance("2200") == Decimal("4.45")
    assert sales.balance("4000") == Decimal("60.00")


def test_several_tax_lines_on_one_amount_come_in_as_one_rate(client, sales):
    """State, county and city tax on the same 100.00: 9% together."""
    tax = _tax(9.00, (6.25, 100, 6.25), (1.75, 100, 1.75), (1.0, 100, 1.00))
    lines = [_line(1, 100, "Catering", "TAX")]
    sales.invoices(_document("141", "1041", lines, 109.00, tax))
    assert sales.get("1041").tax_rate == Decimal("0.0900")
    saved = _save_as_the_form_does(client, sales.get("1041").id)
    assert Decimal(str(saved["total"])) == Decimal("109.00")


NO_SINGLE_RATE = {
    # 8.875% of 1,000.00 is 88.75; stored to four places (8.88%) it is 88.80
    "a rate that is not stored to the cent": (
        [_line(1, 1000, "Catering", "TAX")],
        1088.75,
        _tax(88.75, (8.875, 1000, 88.75)),
    ),
    # 5% of both lines, 2% of the first only
    "tax lines on different amounts": (
        [_line(1, 100, "Catering", "TAX"), _line(2, 50, "Linens", "TAX")],
        159.50,
        _tax(9.50, (5, 150, 7.50), (2, 100, 2.00)),
    ),
    # a set amount, not a percentage
    "a tax line that is not a percentage": (
        [_line(1, 100, "Catering", "TAX")],
        103.00,
        _tax(3.00, (0, 100, 3.00), percent_based=False),
    ),
}


@pytest.mark.parametrize("case", NO_SINGLE_RATE, ids=list(NO_SINGLE_RATE))
def test_a_tax_no_single_rate_gives_is_kept_as_it_is_on_an_edit(client, sales, case):
    lines, total, tax = NO_SINGLE_RATE[case]
    sales.invoices(_document("142", "1042", lines, total, tax))
    invoice = sales.get("1042")
    kept = Decimal(str(tax["TotalTax"])).quantize(Decimal("0.01"))
    assert invoice.tax_rate == Decimal("0")
    assert invoice.tax_amount == kept
    saved = _save_as_the_form_does(client, invoice.id, notes="Call first")
    assert Decimal(str(saved["tax_amount"])) == kept
    assert Decimal(str(saved["total"])) == Decimal(str(total)).quantize(Decimal("0.01"))
    assert sales.balance("2200") == kept
    # a new line changes the subtotal; the tax stays as QBO charged it
    body_lines = client.get(f"/api/invoices/{invoice.id}").json()["lines"]
    more = [
        {k: line[k] for k in ("description", "quantity", "rate", "is_taxable")}
        for line in body_lines
    ] + [{"description": "Tip", "quantity": 1, "rate": 10, "is_taxable": False}]
    saved = _save_as_the_form_does(client, invoice.id, lines=more)
    assert Decimal(str(saved["tax_amount"])) == kept
    assert Decimal(str(saved["total"])) == Decimal(str(total)).quantize(
        Decimal("0.01")
    ) + Decimal("10")
    # a rate entered works the tax out from it
    saved = _save_as_the_form_does(client, invoice.id, tax_rate=0.05)
    taxable = sum(
        Decimal(str(ln["quantity"])) * Decimal(str(ln["rate"]))
        for ln in body_lines
        if ln["is_taxable"]
    )
    assert Decimal(str(saved["tax_amount"])) == (taxable * Decimal("0.05")).quantize(
        Decimal("0.01")
    )


def test_no_line_taxable_any_more_leaves_no_tax(client, sales):
    tax = _tax(88.75, (8.875, 1000, 88.75))
    sales.invoices(
        _document("142", "1042", [_line(1, 1000, "Catering", "TAX")], 1088.75, tax)
    )
    invoice = sales.get("1042")
    lines = client.get(f"/api/invoices/{invoice.id}").json()["lines"]
    untaxed = [
        {**{k: ln[k] for k in ("description", "quantity", "rate")}, "is_taxable": False}
        for ln in lines
    ]
    saved = _save_as_the_form_does(client, invoice.id, lines=untaxed)
    assert Decimal(str(saved["tax_amount"])) == Decimal("0")
    assert Decimal(str(saved["total"])) == Decimal("1000.00")


def test_a_qbo_sales_receipt_comes_in_with_its_tax_rate_and_taxable_lines(
    client, sales
):
    sales.sales_receipts(_document("143", "SR-12", CATERING, 64.45, CATERING_TAX))
    receipt = sales.get("SR-12")
    assert receipt.tax_rate == Decimal("0.0890")
    assert [ln.is_taxable for ln in receipt.lines] == [True, False]
    saved = _save_as_the_form_does(client, receipt.id, notes="Paid at the door")
    assert Decimal(str(saved["total"])) == Decimal("64.45")
    assert Decimal(str(saved["balance_due"])) == Decimal("0")


def test_qbo_lines_with_no_tax_code_stay_taxable(sales):
    """An invoice from a company without sales tax: no TaxCodeRef, no tax."""
    lines = [
        {
            "Id": "1",
            "Amount": 40,
            "Description": "Catering",
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {"Qty": 1, "UnitPrice": 40},
        }
    ]
    sales.invoices(_document("144", "1044", lines, 40, None))
    invoice = sales.get("1044")
    assert invoice.tax_rate == Decimal("0")
    assert [ln.is_taxable for ln in invoice.lines] == [True]
