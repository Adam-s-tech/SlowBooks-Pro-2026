"""Tax-form PDFs are named for the form and the person, not an internal id
(2.18.0 gate, macbase1 NEW-6).

The Reports folder filled with "w2_1_2026.pdf", "1099nec_2026_vendor3.pdf"
and "new_hire_1.pdf": nothing in those names says whose form it is. They are
"W-2_2026_Lena-Ortiz.pdf", "941_2026_Q3.pdf", "1099-NEC_2026_Blue-Heron-
Installs.pdf" now, sent through the RFC 6266 header helper so an accented
name arrives intact (and as plain ASCII for a reader without RFC 6266).
"""

import re
from urllib.parse import unquote

import pytest


@pytest.fixture(autouse=True)
def _html_not_pdf(monkeypatch):
    """The names are the point here, not the rendering."""
    from app.services import form_1099, new_hire_report
    from app.services.tax_forms import form_940, form_941, w2_w3

    for mod in (w2_w3, form_940, form_941, form_1099, new_hire_report):
        monkeypatch.setattr(mod, "render_pdf", lambda html, **kw: html.encode())


def _names(r) -> tuple[str, str]:
    """(the plain-ASCII filename, the exact UTF-8 filename*) of a response."""
    assert r.status_code == 200, r.text
    header = r.headers["content-disposition"]
    plain = re.search(r'filename="([^"]*)"', header).group(1)
    exact = unquote(re.search(r"filename\*=UTF-8''([^;]*)", header).group(1))
    return plain, exact


def _employee(client, first, last):
    r = client.post(
        "/api/employees",
        json={
            "first_name": first,
            "last_name": last,
            "pay_type": "salary",
            "pay_rate": 52000,
            "work_state": "TX",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _vendor(client, name):
    r = client.post(
        "/api/vendors",
        json={"name": name, "is_1099_vendor": True, "vendor_1099_type": "NEC"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_the_payroll_forms_are_named_for_the_form_and_the_person(client, seed_accounts):
    emp = _employee(client, "Lena", "Ortiz")
    cases = {
        f"/api/payroll/forms/w2/{emp}/pdf?year=2026": "W-2_2026_Lena-Ortiz.pdf",
        "/api/payroll/forms/w3/2026/pdf": "W-3_2026.pdf",
        "/api/payroll/forms/940/2026/pdf": "940_2026.pdf",
        "/api/payroll/forms/941/2026/3/pdf": "941_2026_Q3.pdf",
    }
    for url, name in cases.items():
        assert _names(client.get(url)) == (name, name), url


def test_the_tax_forms_endpoints_are_named_the_same_way(client, seed_accounts):
    emp = _employee(client, "Lena", "Ortiz")
    vendor = _vendor(client, "Blue Heron Installs")
    cases = {
        f"/api/tax-forms/w2/{emp}/pdf?year=2026": "W-2_2026_Lena-Ortiz.pdf",
        "/api/tax-forms/940/pdf?year=2026": "940_2026.pdf",
        "/api/tax-forms/941/pdf?year=2026&quarter=3": "941_2026_Q3.pdf",
        f"/api/tax-forms/1099/{vendor}/pdf?year=2026": (
            "1099-NEC_2026_Blue-Heron-Installs.pdf"
        ),
        "/api/tax-forms/1096/pdf?year=2026": "1096_2026.pdf",
    }
    for url, name in cases.items():
        assert _names(client.get(url)) == (name, name), url


def test_the_new_hire_report_is_named_for_the_employee(client, seed_accounts):
    emp = _employee(client, "Lena", "Ortiz")
    r = client.get(f"/api/onboarding/{emp}/new-hire-report/pdf")
    assert _names(r) == ("New-Hire-Report_Lena-Ortiz.pdf",) * 2


def test_an_accented_name_arrives_whole(client, seed_accounts):
    emp = _employee(client, "José", "Núñez")
    plain, exact = _names(client.get(f"/api/payroll/forms/w2/{emp}/pdf?year=2026"))
    assert exact == "W-2_2026_José-Núñez.pdf"
    assert plain == "W-2_2026_Jose-Nunez.pdf"
    plain, exact = _names(client.get(f"/api/onboarding/{emp}/new-hire-report/pdf"))
    assert exact == "New-Hire-Report_José-Núñez.pdf"


def test_file_name_joins_the_parts():
    from app.services.request_utils import file_name

    assert file_name("W-2", 2026, "Lena  Ortiz") == "W-2_2026_Lena-Ortiz"
    assert file_name("941", 2026, "Q3") == "941_2026_Q3"
    assert file_name("W-2", 2026, None) == "W-2_2026"
    assert file_name("W-2", 2026, "   ") == "W-2_2026"
