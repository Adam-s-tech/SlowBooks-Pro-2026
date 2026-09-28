"""Add Reseller Permit: the state's format note has a row of its own under
State and Permit number (#194, @cnbarry1). A negative top margin had drawn it
over both boxes. The layout itself is checked in a browser in
test_browser_ui.py; this needs none."""

import re
from pathlib import Path

JS = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "static"
    / "js"
    / "reseller_permits.js"
).read_text(encoding="utf-8")


def test_the_format_note_is_not_pulled_up_over_the_boxes():
    tag = re.search(r'<p id="permit-format-hint"[^>]*>', JS).group(0)
    assert "margin:-" not in tag.replace(" ", ""), tag
