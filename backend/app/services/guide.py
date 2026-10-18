"""Render the resolution guide (markdown) as plain text or PDF."""
from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape


def to_text(md: str) -> str:
    out = []
    for line in md.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            t = m.group(2).replace("**", "")
            out += ["", t.upper() if len(m.group(1)) <= 2 else t, "=" * len(t) if len(m.group(1)) == 1 else "-" * len(t)]
        else:
            out.append(line.replace("**", ""))
    return "\n".join(out).strip() + "\n"


def to_pdf(md: str, title: str = "Resolution Guide") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=18, textColor=colors.HexColor("#0f6cbd"), spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, textColor=colors.HexColor("#243a5e"), spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=11, spaceBefore=6)

    def fmt(t: str) -> str:
        t = escape(t)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)

    flow, bullets = [], []

    def flush():
        nonlocal bullets
        if bullets:
            flow.append(ListFlowable([ListItem(Paragraph(fmt(b), body), leftIndent=12) for b in bullets], bulletType="bullet", start="•", leftIndent=12))
            bullets = []

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            flow.append(Spacer(1, 3))
            continue
        if line.startswith("---"):
            flush()
            flow.append(HRFlowable(width="100%", color=colors.lightgrey))
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush()
            flow.append(Paragraph(fmt(m.group(2)), {1: h1, 2: h2, 3: h3}[len(m.group(1))]))
            continue
        m = re.match(r"^\s*-\s+(.*)$", line)
        if m:
            bullets.append(m.group(1))
            continue
        flush()
        flow.append(Paragraph(fmt(line.strip()), body))
    flush()
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title=title, author="LogFix AI").build(flow)
    return buf.getvalue()
