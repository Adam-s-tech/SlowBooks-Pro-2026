"""A pay stub's deductions add up to what was withheld (2.18.0 gate, macbase1
NEW-2). The state engines itemize every line under a human label — the
state's own income tax ("OR income tax") and the employer's share ("WA PFML
(employer)") included — and the stub printed all of them beside the generic
state_income_tax: an Oregon stub counted its income tax twice ($548.53 where
$404.06 was withheld) and a Washington stub listed the employer's share as
the employee's deduction. Each line's YTD is that line summed over the year
to this pay date; Total Deductions YTD had read a figure nothing filled in."""

from decimal import Decimal

import pytest

from app.models.payroll import PayStub
from app.services.paystub_pdf import _deduction_lines


def _employee(client, first, state, rate):
    r = client.post(
        "/api/employees",
        json={
            "first_name": first,
            "last_name": "Test",
            "pay_type": "salary",
            "pay_rate": rate,
            "work_state": state,
            "residence_state": state,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _run(client, emp, start, end, pay_date):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": start,
            "period_end": end,
            "pay_date": pay_date,
            "stubs": [{"employee_id": emp}],
        },
    )
    assert r.status_code == 201, r.text
    run = r.json()
    assert client.post(f"/api/payroll/{run['id']}/process").status_code == 200
    return run


@pytest.mark.parametrize("state", ["OR", "WA", "CA", "NY", "IL"])
def test_the_stubs_deductions_are_what_was_withheld(
    client, db_session, seed_accounts, state
):
    emp = _employee(client, f"Lena{state}", state, 56333.42)
    run = _run(client, emp, "2026-09-13", "2026-09-26", "2026-10-01")
    stub = db_session.get(PayStub, run["stubs"][0]["id"])
    lines = _deduction_lines(stub)
    total = sum((d["amount"] for d in lines), Decimal("0"))
    reimb = Decimal(str(stub.reimbursements or 0))
    assert (
        total == Decimal(str(stub.gross_pay)) - Decimal(str(stub.net_pay)) + reimb
    ), lines
    labels = [d["label"].lower() for d in lines]
    assert not any("employer" in lab for lab in labels), labels
    assert (
        sum(
            "income tax" in lab and "local" not in lab and "federal" not in lab
            for lab in labels
        )
        <= 1
    ), labels


def test_each_lines_ytd_is_that_line_to_this_pay_date(
    client, db_session, seed_accounts, monkeypatch
):
    from app.services import paystub_pdf

    emp = _employee(client, "Jonah", "OR", 40560)
    first = _run(client, emp, "2026-08-30", "2026-09-12", "2026-09-17")
    second = _run(client, emp, "2026-09-13", "2026-09-26", "2026-10-01")
    s1 = db_session.get(PayStub, first["stubs"][0]["id"])
    s2 = db_session.get(PayStub, second["stubs"][0]["id"])
    captured = {}
    monkeypatch.setattr(
        paystub_pdf,
        "render_pdf",
        lambda html, **kw: captured.setdefault("html", html).encode(),
    )
    # the FIRST stub, printed after the second run exists: its YTD stops at its own date
    r = client.get(f"/api/payroll/{first['id']}/paystub/{s1.id}")
    assert r.status_code == 200, r.text
    lines = _deduction_lines(s2, [s1, s2])
    for d in lines:
        one = next((x for x in _deduction_lines(s1) if x["key"] == d["key"]), None)
        assert d["ytd"] == d["amount"] + (one["amount"] if one else 0), d
    total_ytd = sum((d["ytd"] for d in lines), Decimal("0"))
    first_total = sum((d["amount"] for d in _deduction_lines(s1)), Decimal("0"))
    assert f"{first_total:,.2f}" in captured["html"]  # the first stub's YTD = its own
    assert total_ytd > first_total


def test_the_template_reads_the_lines_ytd_not_a_guess():
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "app/templates/paystub_pdf.html"
    ).read_text()
    assert "d.ytd" in html and "total_deductions_ytd" in html
    assert "ytd.get('deductions'" not in html and "'federal' in k" not in html


def test_a_state_line_keeps_the_states_capitals(client, db_session, seed_accounts):
    # "OR income tax" printed as "Or Income Tax".
    emp = _employee(client, "Mara", "OR", 56333.42)
    run = _run(client, emp, "2026-09-13", "2026-09-26", "2026-10-01")
    stub = db_session.get(PayStub, run["stubs"][0]["id"])
    labels = [d["label"] for d in _deduction_lines(stub)]
    assert "OR Income Tax" in labels, labels
    assert not any(lab.startswith("Or ") for lab in labels), labels


def test_the_stub_pdf_is_named_for_the_person_and_pay_date(
    client, db_session, seed_accounts, monkeypatch
):
    # NEW-6: it downloaded as "paystub_1_1.pdf".
    from app.services import paystub_pdf

    monkeypatch.setattr(paystub_pdf, "render_pdf", lambda html, **kw: b"%PDF-1.7")
    emp = _employee(client, "Lena", "OR", 56333.42)
    run = _run(client, emp, "2026-09-13", "2026-09-26", "2026-10-01")
    r = client.get(f"/api/payroll/{run['id']}/paystub/{run['stubs'][0]['id']}")
    assert r.status_code == 200, r.text
    assert "Pay-Stub_2026-10-01_Lena-Test.pdf" in r.headers["content-disposition"]
