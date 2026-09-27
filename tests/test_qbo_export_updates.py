"""The export keeps QuickBooks Online up to date with what changed here
(2.18.0).

The export sent a record once and never again ("v1 doesn't update"): an
invoice, payment, customer, vendor, item or account edited here after it
went stayed as it went in QBO, and one voided here stayed live there.
Now a record the export sent goes again, as an update to the same QBO
record, when it changed here since (QBO's copy is read first for its
SyncToken and what SlowBooks doesn't keep), is voided in QBO when it was
voided here, and is left when nothing changed. A record the import brought
in from QBO stays QBO's and is never touched. What QBO refuses is a note,
in its words; the rest carries on.

QBO is played by FakeQBO: the python-quickbooks SDK's own objects built and
read by the export, with their save, get and void calls answered here.
"""

import copy
from datetime import datetime
from decimal import Decimal
import json

import pytest
from quickbooks.exceptions import QuickbooksException
from quickbooks.objects.account import Account as QBOAccount
from quickbooks.objects.customer import Customer as QBOCustomer
from quickbooks.objects.invoice import Invoice as QBOInvoice
from quickbooks.objects.item import Item as QBOItem
from quickbooks.objects.payment import Payment as QBOPayment
from quickbooks.objects.salesreceipt import SalesReceipt as QBOSalesReceipt
from quickbooks.objects.vendor import Vendor as QBOVendor

from app.models.qbo_mapping import QBOMapping
from app.services import qbo_export

SDK = (
    QBOAccount,
    QBOCustomer,
    QBOVendor,
    QBOItem,
    QBOInvoice,
    QBOSalesReceipt,
    QBOPayment,
)


class FakeQBO:
    """QBO's side: the records it holds (as the JSON the SDK sends), and
    each call the export makes. A (call, object, id) in `refuse` is refused
    the way QBO refuses a change."""

    def __init__(self, monkeypatch):
        self.records, self.calls, self.refuse = {}, [], set()
        fake = self

        def save(obj, qb=None, request_id=None, params=None):
            name, body = obj.qbo_object_name, json.loads(obj.to_json())
            if obj.Id and int(obj.Id) > 0:
                key = (name, str(obj.Id))
                if ("update", *key) in fake.refuse:
                    raise QuickbooksException(
                        "Business Validation Error",
                        6000,
                        "A closed period in QuickBooks Online can't be changed",
                    )
                held = fake.records[key]
                if str(body.get("SyncToken")) != str(held["SyncToken"]):
                    raise QuickbooksException("Stale Object Error", 5010)
                body["SyncToken"] = str(int(held["SyncToken"]) + 1)
                fake.calls.append(("update", *key, body))
            else:
                key = (name, str(900 + len(fake.records)))
                body.update(Id=key[1], SyncToken="0")
                fake.calls.append(("create", *key, body))
            fake.records[key] = body
            obj.Id = key[1]
            return type(obj).from_json(copy.deepcopy(body))

        def get(cls, id, qb=None, params=None):
            held = copy.deepcopy(fake.records[(cls.qbo_object_name, str(id))])
            if cls.qbo_object_name in ("Invoice", "SalesReceipt"):
                # QBO's copy carries the tax it worked out, and its totals
                held.setdefault("TxnTaxDetail", {"TotalTax": 4.45})
            return cls.from_json(held)

        def void(obj, qb=None):
            key = (obj.qbo_object_name, str(obj.Id))
            if ("void", *key) in fake.refuse:
                raise QuickbooksException(
                    "Business Validation Error", 6000, "It has payments linked to it"
                )
            held = fake.records[key]
            assert str(obj.SyncToken) == str(held["SyncToken"])  # QBO's current one
            held.update(Voided=True, SyncToken=str(int(held["SyncToken"]) + 1))
            fake.calls.append(("void", *key, obj.SyncToken))

        for cls in SDK:
            monkeypatch.setattr(cls, "save", save)
            monkeypatch.setattr(cls, "get", classmethod(get))
            if hasattr(cls, "void"):
                monkeypatch.setattr(cls, "void", void)
        monkeypatch.setattr(qbo_export, "get_qbo_client", lambda db: object())

    def since(self, mark):
        """The calls made after `mark` (a len(calls) taken earlier)."""
        return self.calls[mark:]


@pytest.fixture
def qbo(monkeypatch):
    return FakeQBO(monkeypatch)


def _run(db, export):
    """One export step, committed as the export routes commit it."""
    result = export(db)
    db.commit()
    return result


@pytest.fixture
def customer(client, qbo, seed_accounts, db_session):
    """A customer written here and exported; Undeposited Funds is QBO 35."""
    r = client.post(
        "/api/customers", json={"name": "Acme Diner", "email": "a@acme.test"}
    )
    assert r.status_code == 201, r.text
    db_session.add(
        QBOMapping(
            entity_type="account", qbo_id="35", slowbooks_id=seed_accounts["1200"].id
        )
    )
    db_session.commit()
    _run(db_session, qbo_export.export_customers)
    return r.json()


def _invoice(client, customer, rate=50, number=None):
    body = {
        "customer_id": customer["id"],
        "date": "2026-08-03",
        "lines": [{"description": "Catering", "quantity": 1, "rate": rate}],
    }
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _mapping(db, kind, local_id):
    db.expire_all()
    return db.query(QBOMapping).filter_by(entity_type=kind, slowbooks_id=local_id).one()


def test_an_invoice_changed_here_since_it_went_is_updated_in_qbo(
    client, db_session, qbo, customer
):
    invoice = _invoice(client, customer)
    assert _run(db_session, qbo_export.export_invoices)["exported"] == 1
    [(_, _, qbo_id, sent)] = [c for c in qbo.calls if c[1] == "Invoice"]
    mark = len(qbo.calls)
    assert _run(db_session, qbo_export.export_invoices)["updated"] == 0
    assert qbo.since(mark) == []  # unchanged: not sent again
    went = _mapping(db_session, "invoice", invoice["id"]).last_synced_at

    r = client.put(
        f"/api/invoices/{invoice['id']}",
        json={"lines": [{"description": "Catering", "quantity": 2, "rate": 30}]},
    )
    assert r.status_code == 200, r.text
    result = _run(db_session, qbo_export.export_invoices)
    assert (result["exported"], result["updated"]) == (0, 1)
    [(call, name, same_id, body)] = qbo.since(mark)
    assert (call, name, same_id) == ("update", "Invoice", qbo_id)
    assert body["SyncToken"] == "1"  # sent with QBO's current "0", saved as 1
    assert [line["Amount"] for line in body["Line"]] == [60.0]
    assert "TxnTaxDetail" not in body  # QBO works the tax out again
    assert body["Line"][0]["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "NON"}
    mapping = _mapping(db_session, "invoice", invoice["id"])
    assert mapping.qbo_id == qbo_id
    assert isinstance(mapping.last_synced_at, datetime)
    assert mapping.last_synced_at != went
    mark = len(qbo.calls)
    _run(db_session, qbo_export.export_invoices)
    assert qbo.since(mark) == []


def test_a_payment_on_an_invoice_does_not_send_the_invoice_again(
    client, db_session, qbo, customer, seed_accounts
):
    invoice = _invoice(client, customer)
    _run(db_session, qbo_export.export_invoices)
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer["id"],
            "date": "2026-08-03",
            "amount": 20,
            "deposit_to_account_id": seed_accounts["1200"].id,
            "allocations": [{"invoice_id": invoice["id"], "amount": 20}],
        },
    )
    assert r.status_code == 201, r.text
    mark = len(qbo.calls)
    assert _run(db_session, qbo_export.export_invoices)["updated"] == 0
    assert _run(db_session, qbo_export.export_payments)["exported"] == 1
    [(call, name, _, body)] = qbo.since(mark)
    assert (call, name) == ("create", "Payment")
    qbo_invoice = _mapping(db_session, "invoice", invoice["id"]).qbo_id
    assert body["Line"][0]["LinkedTxn"] == [
        {"TxnId": qbo_invoice, "TxnType": "Invoice"}
    ]
    assert body["DepositToAccountRef"] == {"value": "35"}


def test_a_document_voided_here_after_it_went_is_voided_in_qbo_once(
    client, db_session, qbo, customer, seed_accounts
):
    invoice = _invoice(client, customer)
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer["id"],
            "date": "2026-08-03",
            "amount": 50,
            "deposit_to_account_id": seed_accounts["1200"].id,
            "allocations": [{"invoice_id": invoice["id"], "amount": 50}],
        },
    )
    payment = r.json()
    _run(db_session, qbo_export.export_invoices)
    _run(db_session, qbo_export.export_payments)
    mark = len(qbo.calls)
    assert client.post(f"/api/payments/{payment['id']}/void").status_code == 200
    assert client.post(f"/api/invoices/{invoice['id']}/void").status_code == 200
    assert _run(db_session, qbo_export.export_payments)["voided"] == 1
    assert _run(db_session, qbo_export.export_invoices)["voided"] == 1
    assert [(c[0], c[1], c[3]) for c in qbo.since(mark)] == [
        ("void", "Payment", "0"),
        ("void", "Invoice", "0"),
    ]
    mark = len(qbo.calls)
    _run(db_session, qbo_export.export_payments)
    _run(db_session, qbo_export.export_invoices)
    assert qbo.since(mark) == []  # once


def test_an_unapplied_payment_applied_here_since_is_updated(
    client, db_session, qbo, customer, seed_accounts
):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer["id"],
            "date": "2026-08-03",
            "amount": 50,
            "deposit_to_account_id": seed_accounts["1200"].id,
        },
    )
    payment = r.json()
    _run(db_session, qbo_export.export_payments)
    invoice = _invoice(client, customer)
    _run(db_session, qbo_export.export_invoices)
    r = client.post(
        f"/api/payments/{payment['id']}/apply",
        json={"allocations": [{"invoice_id": invoice["id"], "amount": 50}]},
    )
    assert r.status_code == 200, r.text
    mark = len(qbo.calls)
    assert _run(db_session, qbo_export.export_payments)["updated"] == 1
    [(call, name, _, body)] = qbo.since(mark)
    assert (call, name) == ("update", "Payment")
    assert body["Line"][0]["Amount"] == 50.0


@pytest.mark.parametrize(
    "kind, path, change, field, sent",
    [
        (
            "customer",
            "/api/customers",
            {"email": "billing@acme.test"},
            "PrimaryEmailAddr",
            {"Address": "billing@acme.test"},
        ),
        ("customer", "/api/customers", {"is_active": False}, "Active", False),
        ("vendor", "/api/vendors", {"phone": "630-555-0100"}, "PrimaryPhone", None),
        ("item", "/api/items", {"rate": 12.5}, "UnitPrice", 12.5),
    ],
    ids=["customer email", "customer made inactive", "vendor phone", "item price"],
)
def test_a_list_record_changed_here_since_it_went_is_updated(
    client, db_session, qbo, customer, kind, path, change, field, sent
):
    step = getattr(qbo_export, f"export_{kind}s")

    def export(db):
        return _run(db, step)

    if kind == "customer":
        local = customer
    else:
        body = {"name": "Linens" if kind == "item" else "Linen Supply"}
        if kind == "item":
            body.update(item_type="service", rate=10)
        r = client.post(path, json=body)
        assert r.status_code == 201, r.text
        local = r.json()
        export(db_session)
    mark = len(qbo.calls)
    r = client.put(f"{path}/{local['id']}", json=change)
    assert r.status_code == 200, r.text
    assert export(db_session)["updated"] == 1
    [(call, name, _, body)] = qbo.since(mark)
    assert call == "update"
    expected = sent if sent is not None else {"FreeFormNumber": change["phone"]}
    assert body[field] == expected
    mark = len(qbo.calls)
    export(db_session)
    assert qbo.since(mark) == []


def test_what_the_import_brought_in_is_never_touched(
    client, db_session, qbo, monkeypatch, seed_accounts
):
    """QBO's invoices, payment and sales receipt, imported; one voided here,
    one edited here: the export leaves them all as they are in QBO."""
    from tests.test_qbo_managed_voids import Books

    books = Books(db_session, seed_accounts, monkeypatch)
    books.documents()
    books.ledger()
    assert (
        client.post(f"/api/invoices/{books.invoice('1038').id}/void").status_code == 200
    )
    r = client.put(
        f"/api/invoices/{books.invoice('1037').id}",
        json={"lines": [{"description": "Catering", "quantity": 1, "rate": 55}]},
    )
    assert r.status_code == 200, r.text
    for export in (
        qbo_export.export_customers,
        qbo_export.export_invoices,
        qbo_export.export_payments,
    ):
        result = _run(db_session, export)
        assert (result["exported"], result["updated"], result["voided"]) == (0, 0, 0)
    assert qbo.calls == []


def test_a_refusal_is_named_in_a_note_and_the_rest_carries_on(
    client, db_session, qbo, customer
):
    first, second = _invoice(client, customer), _invoice(client, customer, rate=70)
    _run(db_session, qbo_export.export_invoices)
    for invoice in (first, second):
        r = client.put(
            f"/api/invoices/{invoice['id']}",
            json={"lines": [{"description": "Catering", "quantity": 1, "rate": 90}]},
        )
        assert r.status_code == 200, r.text
    refused = _mapping(db_session, "invoice", first["id"])
    qbo.refuse.add(("update", "Invoice", refused.qbo_id))
    result = _run(db_session, qbo_export.export_invoices)
    assert result["updated"] == 1  # the second
    assert [n["message"] for n in result["notes"]] == [
        f"Invoice {first['invoice_number']} changed here since it went to "
        "QuickBooks Online, but QuickBooks Online refused the update: A closed "
        "period in QuickBooks Online can't be changed (QuickBooks Online error "
        "6000). It is as it went there, and the next export tries again."
    ]
    # it is tried again next time, and goes once QBO takes it
    qbo.refuse.clear()
    assert _run(db_session, qbo_export.export_invoices)["updated"] == 1


def test_a_refused_void_is_named_in_a_note(client, db_session, qbo, customer):
    invoice = _invoice(client, customer)
    _run(db_session, qbo_export.export_invoices)
    assert client.post(f"/api/invoices/{invoice['id']}/void").status_code == 200
    qbo.refuse.add(
        ("void", "Invoice", _mapping(db_session, "invoice", invoice["id"]).qbo_id)
    )
    result = _run(db_session, qbo_export.export_invoices)
    assert result["voided"] == 0
    assert result["notes"][0]["message"].startswith(
        f"Invoice {invoice['invoice_number']} is voided here, but QuickBooks Online "
        "refused to void it: It has payments linked to it (QuickBooks Online error 6000)."
    )


def test_export_all_counts_what_it_brought_up_to_date(
    client, db_session, qbo, customer
):
    invoice = _invoice(client, customer)
    _run(db_session, qbo_export.export_all)
    r = client.put(
        f"/api/invoices/{invoice['id']}",
        json={"lines": [{"description": "Catering", "quantity": 1, "rate": 75}]},
    )
    assert r.status_code == 200, r.text
    result = _run(db_session, qbo_export.export_all)
    assert (result["invoices"], result["updated"], result["voided"]) == (0, 1, 0)
    from app.schemas.qbo import QBOExportResult

    assert QBOExportResult(**result).updated == 1
    assert Decimal(str(invoice["total"])) == Decimal("50")
