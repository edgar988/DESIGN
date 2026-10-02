"""Spirit Services Group quote PDF template (HELM quote document standard).

Layout: masthead + logos, eyebrow, H1, red rule, CUSTOMER / JOB SITE panels,
CONTRACTOR block w/ licenses, scope w/ bold subheads + bullets, pricing table
(dark header, zebra), inclusions/exclusions, acceptance, footer
"Quote <no> <rev> · Page N" + "Initial: ______".
Logos are optional: drop ssg_logo.png / aeq_logo.png into estimating/assets/.
"""
import os

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
SSG_LOGO = os.path.join(ASSETS, "ssg_logo.png")
AEQ_LOGO = os.path.join(ASSETS, "aeq_logo.png")

RED = HexColor("#C1121F")
DARK = HexColor("#222222")
GRAY = HexColor("#666666")
LGRAY = HexColor("#999999")
LINE = HexColor("#D9D9D9")
ZEBRA = HexColor("#F4F4F4")

PAGE_W, PAGE_H = letter
M = 0.82 * inch
CW = PAGE_W - 2 * M
HEAD_H = 0.62 * inch
FOOT_H = 0.52 * inch

base = ParagraphStyle("base", fontName="Helvetica", fontSize=8.2, leading=11.4, textColor=DARK, spaceAfter=2.5)
sub = ParagraphStyle("sub", parent=base, fontSize=7.2, leading=9.6, textColor=GRAY)
lbl = ParagraphStyle("lbl", parent=base, fontSize=7.4, leading=9.8, textColor=GRAY)
val = ParagraphStyle("val", parent=base, fontSize=7.8, leading=10.4)
sechead = ParagraphStyle("sechead", fontName="Helvetica-Bold", fontSize=8.6, leading=11, textColor=RED,
                         spaceBefore=11, spaceAfter=4.5)
subhead = ParagraphStyle("subhead", fontName="Helvetica-Bold", fontSize=8.2, leading=11, textColor=DARK,
                         spaceBefore=6, spaceAfter=2.5)
eyebrow = ParagraphStyle("eyebrow", fontName="Helvetica-Bold", fontSize=7.4, leading=10, textColor=RED, spaceAfter=4)
h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=DARK, spaceAfter=3)
h1sub = ParagraphStyle("h1sub", fontName="Helvetica", fontSize=8.6, leading=11.8, textColor=GRAY)
wh = ParagraphStyle("wh", fontName="Helvetica-Bold", fontSize=7.6, leading=10, textColor=white)
whr = ParagraphStyle("whr", parent=wh, alignment=TA_RIGHT)
right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
tot = ParagraphStyle("tot", fontName="Helvetica-Bold", fontSize=8.6, leading=11, textColor=DARK, alignment=TA_RIGHT)
bullet = ParagraphStyle("bullet", parent=base, leftIndent=10, bulletIndent=2, spaceAfter=1.2)


def money(v):
    return "${:,.2f}".format(v)


def red_rule(th=1.6):
    t = Table([[""]], colWidths=[CW], rowHeights=[th])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RED)] +
                          [(p, (0, 0), (-1, -1), 0) for p in ("LEFTPADDING", "RIGHTPADDING",
                                                              "TOPPADDING", "BOTTOMPADDING")]))
    return t


def kv(rows, label_w=0.95 * inch, total_w=None):
    total_w = total_w or CW
    t = Table([[Paragraph(k, lbl), Paragraph(v, val)] for k, v in rows],
              colWidths=[label_w, total_w - label_w])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    return t


def panels(left_title, left_rows, right_title, right_rows):
    colw = (CW - 0.28 * inch) / 2.0
    lt = Table([[Paragraph(left_title, sechead)], [kv(left_rows, total_w=colw)]], colWidths=[colw])
    rt = Table([[Paragraph(right_title, sechead)], [kv(right_rows, total_w=colw)]], colWidths=[colw])
    z = [(p, (0, 0), (-1, -1), 0) for p in ("LEFTPADDING", "RIGHTPADDING", "TOPPADDING", "BOTTOMPADDING")]
    for t in (lt, rt):
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")] + z))
    o = Table([[lt, "", rt]], colWidths=[colw, 0.28 * inch, colw])
    o.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")] + z))
    return o


def price_table(rows, total_label, total_value):
    """rows: [(desc_html, amount)]"""
    data = [[Paragraph("#", wh), Paragraph("Description", wh), Paragraph("Amount", whr)]]
    for i, (d, a) in enumerate(rows, 1):
        data.append([Paragraph(str(i), base), Paragraph(d, base), Paragraph(money(a), right)])
    data.append(["", Paragraph(total_label, tot), Paragraph(money(total_value), tot)])
    t = Table(data, colWidths=[0.3 * inch, CW - 1.55 * inch, 1.25 * inch], repeatRows=1)
    st = [("BACKGROUND", (0, 0), (-1, 0), DARK), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
          ("LINEABOVE", (0, -1), (-1, -1), 0.9, DARK)]
    for r in range(1, len(data) - 1):
        if r % 2 == 0:
            st.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    t.setStyle(TableStyle(st))
    return t


def bullets(items):
    return [Paragraph(i, bullet, bulletText="•") for i in items]


def acceptance(customer, entity, signer, closing):
    items = [Paragraph("ACCEPTANCE", sechead),
             Paragraph("Customer's signature below constitutes acceptance of the scope, pricing, and terms set "
                       "forth in this Quotation. " + closing, base), Spacer(1, 22)]
    colw = (CW - 0.4 * inch) / 2.0
    sig = Table([[Paragraph("%s<br/>Authorized Signature / Date &nbsp;&nbsp; Printed Name &amp; Title" % customer, sub), "",
                  Paragraph("%s<br/>%s / Date" % (entity, signer), sub)]],
                colWidths=[colw, 0.4 * inch, colw])
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (0, 0), (0, 0), 0.6, DARK),
                             ("LINEABOVE", (2, 0), (2, 0), 0.6, DARK), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("TOPPADDING", (0, 0), (-1, -1), 3)]))
    return KeepTogether(items + [sig])


def make_doc(path, quote_no, project_line, contractor, rev="R0", doc_kind="Construction Quotation"):
    doc = BaseDocTemplate(path, pagesize=letter, leftMargin=M, rightMargin=M,
                          topMargin=M * 0.55 + HEAD_H, bottomMargin=M * 0.55 + FOOT_H,
                          title="%s %s %s" % (quote_no, rev, project_line), author=contractor["entity"])

    def decorate(c, _doc):
        c.saveState()
        top = PAGE_H - M * 0.55
        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 8.2)
        c.drawString(M, top - 9, " ".join(contractor.get("masthead", contractor["entity"]).upper()))
        c.setFillColor(GRAY)
        c.setFont("Helvetica", 6.6)
        c.drawString(M, top - 19, "%s  ·  %s" % (doc_kind, project_line))
        lh = 0.40 * inch
        x = M + CW
        for p in (AEQ_LOGO, SSG_LOGO):
            if os.path.exists(p):
                from PIL import Image as PILImage
                iw, ih = PILImage.open(p).size
                w = lh * iw / ih
                x -= w
                c.drawImage(p, x, top - lh + 2, width=w, height=lh, mask="auto")
                x -= 0.10 * inch
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(M, top - 27, M + CW, top - 27)
        fy = M * 0.55 + FOOT_H
        c.line(M, fy, M + CW, fy)
        c.setFillColor(LGRAY)
        c.setFont("Helvetica", 6.4)
        c.drawString(M, fy - 11, "%s  ·  %s" % (contractor["entity"], contractor["address"]))
        c.drawString(M, fy - 20, "%s  ·  %s" % (contractor["phone"], contractor["email"]))
        c.drawRightString(M + CW, fy - 11, "Quote %s %s  ·  Page %d" % (quote_no, rev, c.getPageNumber()))
        c.drawRightString(M + CW, fy - 20, "Initial: ______")
        c.restoreState()

    frame = Frame(M, doc.bottomMargin, CW, PAGE_H - doc.topMargin - doc.bottomMargin,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
    return doc
