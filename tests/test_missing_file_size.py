"""A file the upgrade found missing shows no size (2.18.0 round 6, skytech
and macbase1: "(59 bytes)" beside a file that isn't there, the size the row
kept from before)."""

from datetime import datetime, timezone
from pathlib import Path

from app.models.attachments import Attachment
from app.models.stored_files import StoredFile
from app.schemas.attachments import AttachmentResponse
from app.schemas.hr import EmployeeDocumentResponse

ROOT = Path(__file__).resolve().parents[1]


def _row(missing: bool) -> Attachment:
    row = Attachment(
        id=1,
        entity_type="invoice",
        entity_id=1,
        filename="pod.txt",
        file_path="stored_files/9",
        mime_type="text/plain",
        file_size=59,
        uploaded_at=datetime.now(timezone.utc),
    )
    row.stored_file = StoredFile(
        id=9,
        kind="attachment",
        original_name="pod.txt",
        size=0 if missing else 59,
        missing=missing,
        from_shared_folder=True,
    )
    return row


def test_a_missing_file_has_no_size():
    assert AttachmentResponse.model_validate(_row(True)).file_size is None
    assert EmployeeDocumentResponse.model_validate(_row(True)).file_size is None


def test_a_file_that_is_here_keeps_its_size():
    assert AttachmentResponse.model_validate(_row(False)).file_size == 59
    assert EmployeeDocumentResponse.model_validate(_row(False)).file_size == 59


def test_the_lists_leave_out_a_size_there_isnt():
    for page in ("bills", "expenses", "invoices"):
        js = (ROOT / "app" / "static" / "js" / f"{page}.js").read_text(encoding="utf-8")
        assert "${a.file_size == null ? '' : `<span" in js, page
