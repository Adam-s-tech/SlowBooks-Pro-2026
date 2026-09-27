from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    filename: str
    # "stored_files/<id>": the file is kept in the company's own database.
    # (Before 2.18.0, a path under the uploads folder every company shared.)
    file_path: str
    mime_type: Optional[str]
    file_size: Optional[int]
    uploaded_at: datetime
    # The upgrade to 2.18.0 copied it in from that shared folder, which
    # never said whose a file was: it may be another company's.
    from_shared_folder: bool = False
    # The shared-folder file it pointed at was not there to copy.
    missing: bool = False

    model_config = {"from_attributes": True}
