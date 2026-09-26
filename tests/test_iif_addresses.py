"""IIF address lines never say "None" (2.18.0 gate, macbase1 NEW-7; the same
in 2.17.3).

The city line was built as f"{state} {zip}", so Crème Brûlée Co. of Astoria,
OR, with no ZIP, went out as ADDR4 "Astoria, OR None" — and QuickBooks, or our
own importer, then read "None" as the ZIP code. A customer with no state got
"None 97103". Vendors had the same line.
"""

import pytest

from app.models.contacts import Customer, Vendor
from app.services.iif_import import _parse_city_state_zip


def _rows(client, kind: str) -> list[dict]:
    r = client.get(f"/api/iif/export/{kind}")
    assert r.status_code == 200, r.text
    header, rows = None, []
    for line in r.content.decode("cp1252").split("\r\n"):
        fields = line.split("\t")
        if fields[0] in ("!CUST", "!VEND"):
            header = fields
        elif fields[0] in ("CUST", "VEND") and header:
            rows.append(dict(zip(header, fields)))
    return rows


CITY_LINES = [
    # (city, state, zip) -> ADDR4
    (("Astoria", "OR", None), "Astoria, OR"),
    (("Astoria", None, "97103"), "Astoria, 97103"),
    ((None, "OR", "97103"), "OR 97103"),
    (("  Astoria ", " OR", ""), "Astoria, OR"),
    ((" ", None, None), ""),
    ((None, None, None), ""),
    (("Astoria", "OR", "97103"), "Astoria, OR 97103"),
]


@pytest.mark.parametrize("parts, addr4", CITY_LINES)
def test_a_customers_city_line_leaves_out_what_is_blank(
    client, db_session, seed_accounts, parts, addr4
):
    city, state, zip_code = parts
    db_session.add(
        Customer(
            name="Crème Brûlée Co.",
            bill_address1="12 Marine Dr",
            bill_city=city,
            bill_state=state,
            bill_zip=zip_code,
            is_active=True,
        )
    )
    db_session.commit()
    (row,) = _rows(client, "customers")
    assert row["ADDR4"] == addr4
    assert row["ADDR2"] == "12 Marine Dr" and row["ADDR3"] == ""
    assert "None" not in "\t".join(row.values())


@pytest.mark.parametrize("parts, addr4", CITY_LINES)
def test_a_vendors_city_line_leaves_out_what_is_blank(
    client, db_session, seed_accounts, parts, addr4
):
    city, state, zip_code = parts
    db_session.add(
        Vendor(
            name="Coastal Flour",
            address1="4 Mill Rd",
            city=city,
            state=state,
            zip=zip_code,
            is_active=True,
        )
    )
    db_session.commit()
    (row,) = _rows(client, "vendors")
    assert row["ADDR4"] == addr4
    assert "None" not in "\t".join(row.values())


def test_quickbooks_reads_the_line_back_without_a_none_zip(
    client, db_session, seed_accounts
):
    db_session.add(
        Customer(name="Crème Brûlée Co.", bill_city="Astoria", bill_state="OR")
    )
    db_session.commit()
    (row,) = _rows(client, "customers")
    assert _parse_city_state_zip(row["ADDR4"]) == ("Astoria", "OR", "")
