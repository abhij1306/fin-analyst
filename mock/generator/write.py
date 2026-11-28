"""Writers: sheets to .xlsx (openpyxl) or .xls (xlwt, a tool run with `uv run --with xlwt`, not a
dependency of fin), PDF pages to a text PDF (Helvetica, no dependency), and a folder to a .zip.
Fixed dates and names keep a rebuild from changing more than it must."""

import re
import zipfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from mock.generator.reports import Page
from mock.generator.sources import Sheet

FIXED = datetime(2025, 11, 1)


def xlsx(path: Path, sheets: list[Sheet]) -> None:
    book = Workbook()
    book.remove(book.active)  # type: ignore[arg-type]
    book.properties.creator = "mock generator"
    book.properties.created = FIXED
    book.properties.modified = FIXED
    for s in sheets:
        ws = book.create_sheet(s.title)
        for row in s.rows:
            ws.append(row)
        for r in s.bold:
            ws.cell(r, 1).font = Font(bold=True)
        for r, level in s.indent.items():
            ws.cell(r, 1).alignment = Alignment(indent=level)
        for cells in s.merges:
            ws.merge_cells(cells)
        ws.column_dimensions["A"].width = 48
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    _restamp(path)


def _restamp(path: Path) -> None:
    """openpyxl stamps the zip members and core.xml's modified date with the time of the save:
    rewrite them with `FIXED`."""
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            if name == "docProps/core.xml":
                stamp = f"{FIXED:%Y-%m-%dT%H:%M:%SZ}".encode()
                content = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>" + stamp, content)
            info = zipfile.ZipInfo(name, FIXED.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


def xls(path: Path, sheets: list[Sheet]) -> None:
    import xlwt

    book = xlwt.Workbook(encoding="utf-8")
    bold = xlwt.easyxf("font: bold on")
    for s in sheets:
        ws = book.add_sheet(s.title)
        for r, row in enumerate(s.rows, start=1):
            for c, value in enumerate(row):
                if value is not None:
                    ws.write(
                        r - 1,
                        c,
                        value,
                        bold if c == 0 and r in s.bold else xlwt.Style.default_style,
                    )
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(str(path))


def pdf(path: Path, pages: list[Page], width: int = 760, height: int = 842) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(len(pages)))
        + b"] /Count %d >>" % len(pages),
        b"<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>",
    ]
    for i, lines in enumerate(pages):
        ops = []
        for x, y, text in lines:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 10 Tf 1 0 0 1 {x} {height - y} Tm ({escaped}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R"
            b" /Resources 3 0 R >>" % (width, height, 5 + 2 * i)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    body = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def zip_folder(folder: Path, path: Path) -> None:
    """Every file under `folder`, by its path inside it, with a fixed date."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(p for p in folder.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(file.relative_to(folder).as_posix(), FIXED.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.read_bytes())
