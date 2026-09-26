"""The onboarding checklist gives the owner the New-Hire Report as a document,
not as raw JSON (2.18.0 gate, macbase1 NEW-4).

A "New-Hire Report JSON" button beside the PDF opened the API's reply as
text — braces, keys like "ssn_last_four" and the SSN digits — in a modal. The
PDF is the report; the JSON endpoint stays for API clients.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "static" / "js" / "onboarding.js").read_text(encoding="utf-8")


def test_the_checklist_offers_the_pdf_and_no_raw_json():
    assert "New-Hire Report JSON" not in JS
    assert "JSON.stringify" not in JS and "<pre" not in JS
    assert "viewReport" not in JS
    # the only new-hire report the page asks the server for is the PDF
    urls = re.findall(r"/onboarding/\$\{empId\}/new-hire-report[^`'\"\s]*", JS)
    assert urls == ["/onboarding/${empId}/new-hire-report/pdf"]
    assert (
        'onclick="OnboardingPage.downloadReport(${empId})">New-Hire Report PDF</button>'
        in JS
    )


def test_the_json_report_is_still_served_to_api_clients(client, seed_accounts):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Lena",
            "last_name": "Ortiz",
            "pay_type": "salary",
            "pay_rate": 52000,
            "work_state": "OR",
        },
    )
    assert r.status_code == 201, r.text
    report = client.get(f"/api/onboarding/{r.json()['id']}/new-hire-report")
    assert report.status_code == 200, report.text
    assert report.json()["employee_name"] == "Lena Ortiz"
