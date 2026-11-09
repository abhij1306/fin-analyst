from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from layouts import MODERN
from preflight import find_fonts

_regular, _bold = find_fonts()
pdfmetrics.registerFont(TTFont("DejaVu", str(_regular)))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_bold)))

BANNER = "SYNTHETIC TEST DATA - fictitious company, generated for software testing"
FY_LABEL = {"FY23": "2022-23", "FY24": "2023-24", "FY25": "2024-25"}
YEAR_END = {"FY23": "31 March 2023", "FY24": "31 March 2024", "FY25": "31 March 2025"}
PREV = {"FY24": "FY23", "FY25": "FY24"}

S = {
    "title": ParagraphStyle("t", fontName="DejaVu-Bold", fontSize=16, alignment=TA_CENTER, leading=22),
    "h": ParagraphStyle("h", fontName="DejaVu-Bold", fontSize=11, leading=15, spaceAfter=4),
    "b": ParagraphStyle("b", fontName="DejaVu", fontSize=9, leading=13, spaceAfter=6),
    "small": ParagraphStyle("s", fontName="DejaVu", fontSize=8, leading=10, textColor=colors.HexColor("#C00000")),
    "c": ParagraphStyle("c", fontName="DejaVu", fontSize=10, alignment=TA_CENTER, leading=14),
}


def fmt(units: int) -> str:
    v = units / 100
    s = f"{abs(v):,.2f}"
    return f"({s})" if v < 0 else s


def _statement_table(model, st, year, style, overrides):
    prev = PREV[year]
    head = "As at" if st == "bs" else "Year ended"
    rows = [["Particulars", "Note", f"{head}\n{YEAR_END[year]}", f"{head}\n{YEAR_END[prev]}"]]
    total_rows = []
    for label, acct, kind, note in MODERN[st]:
        if acct is None:
            rows.append([label, "", "", ""])
            continue
        cur = overrides.get((st, acct, year), model[year][acct])
        rows.append([label, note, fmt(cur), fmt(model[prev][acct])])
        if kind == "t":
            total_rows.append(len(rows) - 1)
    t = Table(rows, colWidths=[100 * mm, 14 * mm, 30 * mm, 30 * mm], repeatRows=1)
    cmds = [
        ("FONT", (0, 0), (-1, -1), "DejaVu", 8.5),
        ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 8.5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for i in total_rows:
        cmds.append(("FONT", (0, i), (-1, i), "DejaVu-Bold", 8.5))
    if style == "rules":
        cmds.append(("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black))
        for i in total_rows:
            cmds.append(("LINEABOVE", (2, i), (-1, i), 0.5, colors.black))
    t.setStyle(TableStyle(cmds))
    return t


def tarang_report(path, company_name, model, year, notes, style="none", overrides=None):
    """Annual report PDF. Returns {"pages": {statement: page_no}}."""
    overrides = overrides or {}
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"{company_name} Annual Report {FY_LABEL[year]}")
    story = []
    # Page 1: cover
    story += [Spacer(1, 60 * mm), Paragraph(company_name, S["title"]), Spacer(1, 6 * mm),
              Paragraph(f"Annual Report {FY_LABEL[year]}", S["c"]), Spacer(1, 20 * mm),
              Paragraph(BANNER, S["small"]), PageBreak()]
    # Page 2: auditor's report
    story += [Paragraph("INDEPENDENT AUDITOR'S REPORT", S["h"]),
              Paragraph(f"To the Members of {company_name}", S["b"]),
              Paragraph("Opinion", S["h"]),
              Paragraph(
                  f"We have audited the standalone financial statements of {company_name} (\"the Company\"), "
                  f"which comprise the Balance Sheet as at {YEAR_END[year]}, the Statement of Profit and Loss "
                  "and the Statement of Cash Flows for the year then ended, and notes to the financial statements. "
                  "In our opinion and to the best of our information and according to the explanations given to us, "
                  "the aforesaid financial statements give a true and fair view in conformity with the accounting "
                  "principles generally accepted in India.", S["b"])]
    eom = notes.get("emphasis_of_matter")
    if eom:
        story += [Paragraph("Emphasis of Matter", S["h"]), Paragraph(eom, S["b"])]
    story += [Paragraph("Basis for Opinion", S["h"]),
              Paragraph("We conducted our audit in accordance with the Standards on Auditing specified under "
                        "section 143(10) of the Companies Act, 2013. We believe that the audit evidence we have "
                        "obtained is sufficient and appropriate to provide a basis for our opinion.", S["b"]),
              Spacer(1, 8 * mm),
              Paragraph("For ABC &amp; Associates (fictitious), Chartered Accountants", S["b"]),
              Paragraph("Place: Bhopal &nbsp;&nbsp; Date: 15 May " + ("2025" if year == "FY25" else "2024"), S["b"]),
              PageBreak()]
    pages = {}
    page = 3
    for st, title in [("bs", f"Balance Sheet as at {YEAR_END[year]}"),
                      ("pl", f"Statement of Profit and Loss for the year ended {YEAR_END[year]}"),
                      ("cf", f"Cash Flow Statement for the year ended {YEAR_END[year]}")]:
        story += [Paragraph(company_name, S["h"]), Paragraph(title, S["h"]),
                  Paragraph("(₹ in Lakhs)", S["b"]),
                  _statement_table(model, st, year, style, overrides), PageBreak()]
        pages[st] = page
        page += 1
    # Notes page
    prev = PREV[year]
    story += [Paragraph("Notes to the financial statements (extract)", S["h"]),
              Paragraph("(₹ in Lakhs)", S["b"]),
              Paragraph("19. Related party transactions", S["h"])]
    rpt = [["Related party", "Relationship", "Nature", FY_LABEL[year], FY_LABEL[prev]]]
    for item in notes["related_party"]:
        rpt.append([item["party"], item["relationship"], item["nature"],
                    f"{item['amounts'][year]:,.2f}", f"{item['amounts'][prev]:,.2f}"])
    t = Table(rpt, colWidths=[36 * mm, 48 * mm, 46 * mm, 22 * mm, 22 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "DejaVu", 8), ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 8),
                           ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black)]))
    story += [t, Spacer(1, 6 * mm), Paragraph("20. Contingent liabilities not provided for", S["h"])]
    cl = [["Particulars", FY_LABEL[year], FY_LABEL[prev]]]
    for item in notes["contingent_liabilities"]:
        cl.append([item["item"], f"{item['amounts'][year]:,.2f}", f"{item['amounts'][prev]:,.2f}"])
    t = Table(cl, colWidths=[120 * mm, 27 * mm, 27 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "DejaVu", 8), ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 8),
                           ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black)]))
    story.append(t)
    pages["notes"] = page
    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7)
        canvas.setFillColor(colors.HexColor("#C00000"))
        canvas.drawString(18 * mm, 9 * mm, BANNER)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return {"pages": pages}


def encrypt(src, dst, password="test1234"):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(src)
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.encrypt(password)
    with open(dst, "wb") as f:
        writer.write(f)


def scanned(src, dst, scale=1.5):
    """Image-only copy (no text layer), simulating a scanned document."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(src)
    images = [pdf[i].render(scale=scale).to_pil().convert("L") for i in range(len(pdf))]
    images[0].save(dst, save_all=True, append_images=images[1:], resolution=108)
