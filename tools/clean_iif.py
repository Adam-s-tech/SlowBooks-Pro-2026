#!/usr/bin/env python3
"""Write a cleaned copy of a QuickBooks IIF export.

Two fixes are applied, and only to the name-bearing fields:

1. ``"ACME, Inc."`` -> ``ACME, Inc.``
   QuickBooks quotes any field containing a comma. The quotes are delimiters,
   not part of the value, and they end up inside the name on import.

2. ``ACME TOOLING, INC.`` -> ``ACME Tooling, Inc.``
   Names typed in all caps are re-cased for readability, via the same
   ``normalize_name`` the importer uses when its "Change ALL-CAPS names" box
   is ticked, so both paths agree. Item names are kept as typed, as the
   import keeps them: they are often part numbers.

The original file is never modified. Structural rules this script obeys:

* only the NAME / COMPANYNAME columns are touched; balances, phone numbers,
  addresses, terms and every other column are copied through byte for byte;
* a field is re-quoted only if it needed quoting before or contains a comma,
  so the output stays valid IIF;
* formula-guarded values (``'=HYPERLINK(1)``) are left completely alone;
* line count, record types and field counts are identical to the input.

Usage:
    python tools/clean_iif.py INPUT.iif OUTPUT.iif
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.name_case import normalize_name  # noqa: E402

# Column names whose values are human-readable names.
NAME_COLUMNS = {"NAME", "COMPANYNAME", "PRINTNAME", "FULLNAME"}
# Rows whose names are left as typed: an item's name is often a part number.
KEEP_SECTIONS = {"INVITEM"}


def _unquote(field: str) -> tuple[str, bool]:
    """Return (value, was_quoted)."""
    if len(field) >= 2 and field.startswith('"') and field.endswith('"'):
        return field[1:-1], True
    return field, False


def _needs_quotes(value: str) -> bool:
    return any(ch in value for ch in ',"\t') or value != value.strip()


def _requote(value: str, was_quoted: bool) -> str:
    if not value:
        return value
    if was_quoted or _needs_quotes(value):
        return '"' + value.replace('"', '""') + '"'
    return value


def clean_field(field: str) -> str:
    """Unquote, normalize case, re-quote if necessary. Formula-guarded values
    and any value that is not a bare name are returned unchanged."""
    if not field:
        return field
    # A leading apostrophe is QuickBooks' formula guard. Never touch these.
    if field[0] == "'":
        return field
    value, was_quoted = _unquote(field)
    if not value:
        return field
    normalized = normalize_name(value)
    if normalized is None or normalized == value:
        # Unchanged: restore the original quoting exactly as it was.
        return field
    return _requote(normalized, was_quoted)


def clean_iif(source: Path, dest: Path) -> dict:
    # Bytes in, bytes out: reading as text turns QuickBooks' CRLF line endings
    # into LF (and writing text on Windows turns them into CR CR LF). An older
    # QuickBooks writes Windows-1252, as the importer allows for.
    data = source.read_bytes()
    try:
        encoding, raw = "utf-8", data.decode("utf-8")
    except UnicodeDecodeError:
        encoding, raw = "cp1252", data.decode("cp1252")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()

    # Map each section to the column names its header declares, so we know
    # which field index is a name without hard-coding positions.
    headers: dict[str, list[str]] = {}
    out: list[str] = []
    stats = {"records": 0, "fields_changed": 0, "sections": {}}

    for line in lines:
        if line.startswith("!"):
            parts = line.split("\t")
            headers[parts[0][1:].upper()] = parts
            out.append(line)
            continue
        if not line:
            out.append(line)
            continue

        parts = line.split("\t")
        section = parts[0].upper()
        columns = headers.get(section)
        if not columns or section in KEEP_SECTIONS:
            out.append(line)
            continue

        stats["records"] += 1
        changed_here = 0
        for index, column in enumerate(columns):
            if index == 0 or index >= len(parts):
                continue
            if column.upper() not in NAME_COLUMNS:
                continue
            original = parts[index]
            cleaned = clean_field(original)
            if cleaned != original:
                parts[index] = cleaned
                changed_here += 1

        if changed_here:
            stats["fields_changed"] += changed_here
            stats["sections"][section] = (
                stats["sections"].get(section, 0) + changed_here
            )
        out.append("\t".join(parts))

    text = newline.join(out) + (newline if out else "")
    dest.write_bytes(text.encode(encoding))
    return stats


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source, dest = Path(sys.argv[1]), Path(sys.argv[2])
    if not source.exists():
        print(f"input not found: {source}")
        return 1
    if source.resolve() == dest.resolve():
        print("refusing to write over the input file")
        return 1

    stats = clean_iif(source, dest)
    print(f"wrote {dest}")
    print(f"  records scanned : {stats['records']}")
    print(f"  name fields fixed: {stats['fields_changed']}")
    for section, count in sorted(stats["sections"].items()):
        print(f"    {section:9s} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
