# ============================================================================
# File Uploads — the company logo
# Feature 15: Infrastructure D (UploadFile pattern)
#
# The logo is kept in the company's own database (app/services/file_store.py),
# not in the uploads folder every company on a desktop install shared: there
# it was one file, company_logo.<ext>, so one company's upload became every
# company's logo (2.18.0 gate, macbase1 NEW-14 and skytech).
# ============================================================================

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._roles import require_admin
from app.services import file_store

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Map of allowed content-type -> filename extension. Deriving the extension
# from the verified content-type instead of from the user-supplied filename
# means a renamed file is never stored or served under a misleading type.
#
# SVG note: SVG can contain inline <script>. The logo is only ever shown
# through <img> (which runs no script) or embedded in a PDF, and the route
# that serves it sends a sandboxing Content-Security-Policy, so opening its
# URL directly runs nothing either.
_LOGO_EXT_BY_TYPE = file_store.LOGO_TYPES

_LOGO_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — generous for a logo, blocks abuse


@router.post("/logo")
async def upload_logo(
    request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    # the logo is a company setting, and settings are the administrator's
    require_admin(request)
    content_type = (file.content_type or "").lower()
    if content_type not in _LOGO_EXT_BY_TYPE:
        raise HTTPException(
            status_code=400,
            detail="Logo must be a PNG, JPEG, GIF, WebP, or SVG image "
            f"(got '{file.content_type or 'unknown'}').",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > _LOGO_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Logo is too large ({len(content) // 1024} KB). "
            f"Maximum {_LOGO_MAX_BYTES // (1024 * 1024)} MB.",
        )

    row = file_store.replace_logo(db, content, content_type)
    db.commit()
    return {"path": file_store.logo_url(row), "message": "Logo uploaded successfully"}


@router.get("/logo")
def logo_info(db: Session = Depends(get_db)):
    """The current logo's address and where it came from: Settings says so
    when the upgrade copied it in from the folder every company shared."""
    return file_store.logo_info(file_store.current_logo(db))


@router.delete("/logo")
def remove_logo(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    file_store.remove_logo(db)
    db.commit()
    return {"status": "deleted"}


@router.get("/logo/{file_id}")
def logo_image(file_id: int, db: Session = Depends(get_db)):
    """The logo image, from this company's own database."""
    return file_store.logo_response(db, file_store.logo_row(db, file_id))
