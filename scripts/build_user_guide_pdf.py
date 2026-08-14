from __future__ import annotations

"""Build the versioned DFS Optimizer user-guide PDF from Markdown."""

import argparse
import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#13233A")
BLUE = colors.HexColor("#2563EB")
TEAL = colors.HexColor("#0F766E")
PALE_BLUE = colors.HexColor("#EFF6FF")
PALE_TEAL = colors.HexColor("#ECFDF5")
MUTED = colors.HexColor("#526173")
LIGHT = colors.HexColor("#DCE3EA")


def inline_markup(value: str) -> str:
    value = html.escape(value.strip())
    value = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(
        r"\[([^]]+)\]\(([^)]+)\)",
        r"<link href='\2' color='#2563EB'>\1</link>",
        value,
    )
    return value


def styles_for_guide():
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=28, leading=33, textColor=colors.white, alignment=TA_CENTER,
            spaceAfter=16,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=13, leading=19, textColor=colors.HexColor("#D9E8FF"),
            alignment=TA_CENTER,
        ),
        "cover_version": ParagraphStyle(
            "CoverVersion", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=11, leading=15, textColor=colors.white, alignment=TA_CENTER,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=17, leading=22, textColor=NAVY, spaceAfter=10,
        ),
        "h3": ParagraphStyle(
            "H3", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=12, leading=16, textColor=TEAL, spaceBefore=6, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.6, leading=14.2, textColor=NAVY, spaceAfter=8,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=MUTED,
        ),
        "callout": ParagraphStyle(
            "Callout", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=9.3, leading=14, textColor=TEAL,
        ),
        "list": ParagraphStyle(
            "List", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.4, leading=13.7, textColor=NAVY, leftIndent=4,
        ),
        "caption": ParagraphStyle(
            "Caption", parent=base["BodyText"], fontName="Helvetica-Oblique",
            fontSize=8, leading=11, textColor=MUTED, alignment=TA_CENTER,
        ),
    }


def page_footer(canvas, doc) -> None:
    canvas.saveState()
    width, _ = letter
    canvas.setStrokeColor(LIGHT)
    canvas.line(doc.leftMargin, 0.48 * inch, width - doc.rightMargin, 0.48 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 0.30 * inch, "DFS Optimizer User Guide")
    canvas.drawRightString(width - doc.rightMargin, 0.30 * inch, f"Page {doc.page}")
    canvas.restoreState()


def cover_story(version: str, styles):
    cover = Table(
        [[Paragraph("DFS Optimizer", styles["cover_title"])],
         [Paragraph("User Guide", styles["cover_title"])],
         [Paragraph("Build, review, export, and learn from DraftKings lineups", styles["cover_subtitle"])],
         [Paragraph(f"Version {html.escape(version)}", styles["cover_version"])]],
        colWidths=[6.55 * inch],
        rowHeights=[0.70 * inch, 0.58 * inch, 0.85 * inch, 0.52 * inch],
    )
    cover.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 24),
        ("RIGHTPADDING", (0, 0), (-1, -1), 24),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0, NAVY),
    ]))
    overview = Table(
        [[Paragraph("Inside", styles["h3"]), Paragraph("Start here", styles["h3"])],
         [Paragraph("Install and five-minute workflow<br/>Classic and Showdown<br/>Live NFL data and Vegas<br/>Build and portfolio controls", styles["small"]),
          Paragraph("NFL SIM Edge explained<br/>Saving and export<br/>Results & Learning<br/>Troubleshooting and privacy", styles["small"])]],
        colWidths=[3.17 * inch, 3.17 * inch],
    )
    overview.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.75, LIGHT),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 13),
        ("RIGHTPADDING", (0, 0), (-1, -1), 13),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    return [
        Spacer(1, 0.55 * inch), cover, Spacer(1, 0.55 * inch), overview,
        Spacer(1, 0.36 * inch),
        Paragraph(
            "Use this guide alongside a fresh DraftKings salary file and a final pre-lock review. "
            "Simulation and live-data outputs are decision aids, not guarantees.",
            styles["callout"],
        ),
        PageBreak(),
    ]


def markdown_story(markdown: str, styles, source_dir: Path):
    story = []
    paragraph_lines = []
    list_items = []
    list_kind = None
    seen_h2 = False

    def flush_paragraph():
        if paragraph_lines:
            story.append(Paragraph(inline_markup(" ".join(paragraph_lines)), styles["body"]))
            paragraph_lines.clear()

    def flush_list():
        nonlocal list_kind
        if list_items:
            flow = [
                ListItem(
                    Paragraph(inline_markup(item), styles["list"]),
                    leftIndent=10,
                    spaceAfter=1.5,
                )
                for item in list_items
            ]
            list_options = {
                "bulletType": "1" if list_kind == "number" else "bullet",
                "leftIndent": 22,
                "bulletFontName": "Helvetica-Bold",
                "bulletFontSize": 8.5,
                "bulletColor": BLUE,
                "spaceAfter": 8,
            }
            if list_kind == "number":
                list_options["start"] = "1"
            story.append(ListFlowable(flow, **list_options))
            list_items.clear()
        list_kind = None

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("# "):
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            if seen_h2:
                story.append(PageBreak())
            seen_h2 = True
            story.append(Paragraph(inline_markup(line[3:]), styles["h2"]))
            story.append(HRFlowable(width="100%", thickness=1.2, color=BLUE, spaceAfter=11))
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(line[4:]), styles["h3"]))
            continue
        if line.startswith("> "):
            flush_paragraph()
            flush_list()
            box = Table([[Paragraph(inline_markup(line[2:]), styles["callout"])]], colWidths=[6.25 * inch])
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PALE_TEAL),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#99D5C9")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]))
            story.extend([box, Spacer(1, 8)])
            continue
        image_match = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", line)
        if image_match:
            flush_paragraph()
            flush_list()
            alt_text, relative_path = image_match.groups()
            image_path = (source_dir / relative_path).resolve()
            if not image_path.exists():
                raise FileNotFoundError(f"Guide image not found: {image_path}")
            figure = RLImage(str(image_path))
            scale = min(
                (6.25 * inch) / figure.imageWidth,
                (4.35 * inch) / figure.imageHeight,
                1.0,
            )
            figure.drawWidth *= scale
            figure.drawHeight *= scale
            figure.hAlign = "CENTER"
            caption = Paragraph(f"Figure: {inline_markup(alt_text)}", styles["caption"])
            story.append(KeepTogether([figure, Spacer(1, 5), caption, Spacer(1, 10)]))
            continue
        numbered = re.match(r"^\d+\.\s+(.*)$", line)
        bulleted = re.match(r"^-\s+(.*)$", line)
        if numbered or bulleted:
            flush_paragraph()
            kind = "number" if numbered else "bullet"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append((numbered or bulleted).group(1))
            continue
        if list_items:
            flush_list()
        paragraph_lines.append(line)

    flush_paragraph()
    flush_list()
    return story


def build_pdf(source: Path, output: Path, version: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = styles_for_guide()
    doc = SimpleDocTemplate(
        str(output), pagesize=letter, rightMargin=0.68 * inch, leftMargin=0.68 * inch,
        topMargin=0.62 * inch, bottomMargin=0.66 * inch,
        title="DFS Optimizer User Guide", author="DFS Optimizer",
        subject=f"DFS Optimizer user documentation, version {version}",
    )
    story = cover_story(version, styles)
    story.extend(markdown_story(source.read_text(encoding="utf-8"), styles, source.parent))
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="docs/USER_GUIDE.md")
    parser.add_argument("--output", default="dist/DFS-Optimizer-User-Guide.pdf")
    parser.add_argument("--version", default="1.9.1")
    args = parser.parse_args()
    build_pdf(Path(args.source), Path(args.output), args.version)
    print(Path(args.output).resolve())


if __name__ == "__main__":
    main()
