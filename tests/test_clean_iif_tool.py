"""tools/clean_iif.py (#195, @TheLocalW): a cleaned copy of an IIF export,
for anyone who wants the file fixed before importing it. It changes only
names, keeps item names as typed (as the import does), keeps every row's
field count, and never writes over its input."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from clean_iif import clean_iif  # noqa: E402

EXPORT = (
    "!CUST\tNAME\tBADDR1\tPHONE1\r\n"
    'CUST\t"JONES, BOB"\t"12 MAIN ST, APT 4"\t555-0100\r\n'
    "!VEND\tNAME\r\n"
    "VEND\tACME TOOLING\r\n"
    "VEND\tContoso\r\n"
    "!INVITEM\tNAME\tINVITEMTYPE\r\n"
    "INVITEM\tWIDGET-A\tSERV\r\n"
)


def test_names_change_and_nothing_else(tmp_path):
    src, dest = tmp_path / "in.iif", tmp_path / "out.iif"
    src.write_bytes(EXPORT.encode("utf-8"))
    clean_iif(src, dest)
    out = dest.read_bytes().decode("utf-8")
    assert "\r\n" in out  # line endings kept
    rows = [line.split("\t") for line in out.split("\r\n") if line]
    assert [len(r) for r in rows] == [
        len(line.split("\t")) for line in EXPORT.split("\r\n") if line
    ]
    assert rows[1] == ["CUST", '"Jones, Bob"', '"12 MAIN ST, APT 4"', "555-0100"]
    assert rows[3] == ["VEND", "ACME Tooling"]
    assert rows[4] == ["VEND", "Contoso"]
    assert rows[6] == ["INVITEM", "WIDGET-A", "SERV"]  # an item keeps its name


def test_it_never_writes_over_its_input(tmp_path):
    src = tmp_path / "in.iif"
    src.write_bytes(EXPORT.encode("utf-8"))
    run = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "clean_iif.py"), str(src), str(src)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    assert "refusing" in run.stdout
    assert src.read_bytes() == EXPORT.encode("utf-8")


def test_a_windows_1252_export_comes_back_in_windows_1252(tmp_path):
    src, dest = tmp_path / "in.iif", tmp_path / "out.iif"
    src.write_bytes("!VEND\tNAME\r\nVEND\tCAF\u00c9 ROUGE\r\n".encode("cp1252"))
    clean_iif(src, dest)
    assert dest.read_bytes() == "!VEND\tNAME\r\nVEND\tCaf\u00e9 Rouge\r\n".encode(
        "cp1252"
    )
