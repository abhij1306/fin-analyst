"""Write tiny text PDFs for extractor tests: Helvetica text placed at exact positions."""

from pathlib import Path

# (x, y from the top, text), or (x, y, text, "F2") for Helvetica-Bold instead of Helvetica.
Page = list[tuple[float, float, str] | tuple[float, float, str, str]]


def write_pdf(path: Path, *pages: Page, width: float = 595, height: float = 842) -> Path:
    """Write a PDF with one page per `pages` entry, 10 pt text at (x, y-from-top)."""
    count = len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(count))
        + b"] /Count %d >>" % count,
        b"<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        b" /F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> >> >>",
    ]
    for i, lines in enumerate(pages):
        ops = []
        for x, y, text, *font in lines:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            name = font[0] if font else "F1"
            ops.append(f"BT /{name} 10 Tf 1 0 0 1 {x} {height - y} Tm ({escaped}) Tj ET")
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
    path.write_bytes(body)
    return path
