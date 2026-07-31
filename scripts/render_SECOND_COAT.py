#!/usr/bin/env python3
"""Render the paired SECOND COAT Markdown master as matched PDF/PNG assets."""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "source" / "SECOND_COAT_Final_Master.md"
PDF_DIR = ROOT / "assets" / "final"
IMAGE_DIR = ROOT / "assets" / "final"
TMP_DIR = ROOT / "tmp" / "pdfs" / "final_render"
FINAL_PDF = PDF_DIR / "SECOND_COAT_Paired_Renders.pdf"

PAGE_W = 600
PAGE_H = 750
LEFT = 34
RIGHT = 34
CONTENT_W = PAGE_W - LEFT - RIGHT

def first_font(env_name: str, candidates: tuple[str, ...]) -> str:
    """Resolve a font override or the first common Linux/macOS font path."""
    paths = (os.environ.get(env_name), *candidates)
    for candidate in paths:
        if candidate and Path(candidate).is_file():
            return candidate
    raise FileNotFoundError(
        f"No usable font found. Set {env_name} to a TrueType font path."
    )


FONT_REGULAR = first_font(
    "SECOND_COAT_FONT_REGULAR",
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/opt/homebrew/share/fonts/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
)
FONT_BOLD = first_font(
    "SECOND_COAT_FONT_BOLD",
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/opt/homebrew/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
)
FONT_ITALIC = FONT_REGULAR
FONT_BOLD_ITALIC = FONT_BOLD


@dataclass
class Section:
    title: str
    body: str


@dataclass
class RenderDoc:
    render_name: str
    fields: dict[str, str]
    title: str
    subtitle: str
    sections: list[Section]
    footer: str


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("NimbusNarrow", FONT_REGULAR))
    pdfmetrics.registerFont(TTFont("NimbusNarrow-Bold", FONT_BOLD))
    pdfmetrics.registerFont(TTFont("NimbusNarrow-Italic", FONT_ITALIC))
    pdfmetrics.registerFont(TTFont("NimbusNarrow-BoldItalic", FONT_BOLD_ITALIC))
    pdfmetrics.registerFontFamily(
        "NimbusNarrow",
        normal="NimbusNarrow",
        bold="NimbusNarrow-Bold",
        italic="NimbusNarrow-Italic",
        boldItalic="NimbusNarrow-BoldItalic",
    )


def parse_master(path: Path) -> list[RenderDoc]:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^# RENDER \d+ - (.+)$", text, flags=re.MULTILINE))
    docs: list[RenderDoc] = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        block = re.split(r"^---$", block, maxsplit=1, flags=re.MULTILINE)[0]

        fields = {
            key.strip(): value.strip()
            for key, value in re.findall(
                r"^\*\*([A-Z /]+):\*\*\s*(.+)$", block, flags=re.MULTILINE
            )
        }

        title_match = re.search(r"^# (?!RENDER)(.+)$", block, flags=re.MULTILINE)
        if not title_match:
            raise ValueError(f"Missing artifact title in {match.group(1)}")
        title = title_match.group(1).strip()

        subtitle_match = re.search(
            r"^\*(.+)\*$", block[title_match.end() :], flags=re.MULTILINE
        )
        subtitle = subtitle_match.group(1).strip() if subtitle_match else ""

        section_matches = list(
            re.finditer(r"^## (.+)$", block, flags=re.MULTILINE)
        )
        sections: list[Section] = []
        footer = fields.get("FOOTER", "")

        for section_index, section_match in enumerate(section_matches):
            section_start = section_match.end()
            section_end = (
                section_matches[section_index + 1].start()
                if section_index + 1 < len(section_matches)
                else len(block)
            )
            body = block[section_start:section_end].strip()
            body = re.sub(
                r"^\*\*FOOTER:\*\*.*$",
                "",
                body,
                flags=re.MULTILINE,
            ).strip()
            sections.append(Section(section_match.group(1).strip(), body))

        if len(sections) != 6:
            raise ValueError(
                f"Expected six sections in {match.group(1)}, found {len(sections)}"
            )

        docs.append(
            RenderDoc(
                render_name=match.group(1).strip(),
                fields=fields,
                title=title,
                subtitle=subtitle,
                sections=sections,
                footer=footer,
            )
        )

    if len(docs) != 2:
        raise ValueError(f"Expected two renders, found {len(docs)}")
    return docs


def to_rl_markup(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<i>\1</i>", escaped)
    return escaped


def body_units(body: str) -> list[tuple[str, str]]:
    units: list[tuple[str, str]] = []
    paragraph_lines: list[str] = []

    def flush() -> None:
        if paragraph_lines:
            units.append(("paragraph", " ".join(paragraph_lines).strip()))
            paragraph_lines.clear()

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if re.match(r"^\d+\.\s+", line):
            flush()
            units.append(("number", line))
        elif line.startswith("- "):
            flush()
            units.append(("bullet", line[2:].strip()))
        else:
            paragraph_lines.append(line)
    flush()
    return units


def styles(font_size: float) -> dict[str, ParagraphStyle]:
    leading = font_size * 1.16
    return {
        "body": ParagraphStyle(
            "body",
            fontName="NimbusNarrow",
            fontSize=font_size,
            leading=leading,
            textColor=colors.HexColor("#181818"),
            alignment=TA_LEFT,
            spaceAfter=font_size * 0.50,
        ),
        "list": ParagraphStyle(
            "list",
            parent=None,
            fontName="NimbusNarrow",
            fontSize=font_size - 0.15,
            leading=leading - 0.15,
            leftIndent=8,
            firstLineIndent=-8,
            textColor=colors.HexColor("#181818"),
            alignment=TA_LEFT,
            spaceAfter=font_size * 0.10,
        ),
        "summary": ParagraphStyle(
            "summary",
            fontName="NimbusNarrow",
            fontSize=font_size + 0.15,
            leading=leading + 0.15,
            textColor=colors.HexColor("#181818"),
            alignment=TA_LEFT,
            spaceAfter=font_size * 0.50,
        ),
        "meta": ParagraphStyle(
            "meta",
            fontName="NimbusNarrow-Bold",
            fontSize=7.3,
            leading=8.2,
            textColor=colors.HexColor("#262626"),
        ),
        "meta_label": ParagraphStyle(
            "meta_label",
            fontName="NimbusNarrow-Bold",
            fontSize=5.8,
            leading=6.3,
            textColor=colors.HexColor("#666666"),
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName="NimbusNarrow-Bold",
            fontSize=6.4,
            leading=7.2,
            textColor=colors.HexColor("#3A3A3A"),
            alignment=TA_CENTER,
        ),
    }


def paragraph_for(kind: str, text: str, style_map: dict[str, ParagraphStyle], summary: bool = False) -> Paragraph:
    if kind == "number":
        match = re.match(r"^(\d+\.)\s+(.+)$", text)
        assert match
        markup = f"<b>{match.group(1)}</b> {to_rl_markup(match.group(2))}"
        style = style_map["list"]
    elif kind == "bullet":
        markup = f'<font color="#9B2424">•</font> {to_rl_markup(text)}'
        style = style_map["list"]
    else:
        markup = to_rl_markup(text)
        style = style_map["summary" if summary else "body"]
    return Paragraph(markup, style)


def measure_section(
    section: Section,
    style_map: dict[str, ParagraphStyle],
    width: float,
    summary: bool,
) -> tuple[float, list[tuple[Paragraph, float]]]:
    measured: list[tuple[Paragraph, float]] = []
    total = 0.0
    for kind, text in body_units(section.body):
        para = paragraph_for(kind, text, style_map, summary=summary)
        _, height = para.wrap(width, 1000)
        measured.append((para, height))
        total += height + para.style.spaceAfter
    if measured:
        total -= measured[-1][0].style.spaceAfter
    return total, measured


def draw_paragraphs(
    c: canvas.Canvas,
    measured: list[tuple[Paragraph, float]],
    x: float,
    top: float,
    width: float,
) -> None:
    y = top
    for para, height in measured:
        y -= height
        para.drawOn(c, x, y)
        y -= para.style.spaceAfter


def draw_title(c: canvas.Canvas, doc: RenderDoc, accent: colors.Color) -> None:
    title_top = 617
    if " FOLLOWING " in doc.title:
        first, second = doc.title.split(" FOLLOWING ", 1)
        lines = [f"{first} FOLLOWING", second]
    else:
        lines = [doc.title]

    c.setFillColor(colors.HexColor("#161616"))
    title_size = 19.5
    while (
        max(pdfmetrics.stringWidth(line, "NimbusNarrow-Bold", title_size) for line in lines)
        > CONTENT_W
        and title_size > 16.0
    ):
        title_size -= 0.25
    c.setFont("NimbusNarrow-Bold", title_size)
    line_y = title_top
    for line in lines:
        c.drawString(LEFT, line_y, line)
        line_y -= 20

    c.setFillColor(accent)
    c.setFont("NimbusNarrow-Italic", 9.0)
    c.drawString(LEFT, line_y - 1, doc.subtitle)
    c.setStrokeColor(accent)
    c.setLineWidth(1.4)
    c.line(LEFT, line_y - 7, PAGE_W - RIGHT, line_y - 7)


def draw_header(
    c: canvas.Canvas,
    doc: RenderDoc,
    accent: colors.Color,
    dark: colors.Color,
    badge_top: str,
    badge_bottom: str,
) -> None:
    c.setFillColor(dark)
    c.rect(LEFT, 722, CONTENT_W, 16, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("NimbusNarrow-Bold", 7.4)
    c.drawString(LEFT + 5, 727, doc.fields["CLASSIFICATION"])
    c.drawRightString(PAGE_W - RIGHT - 5, 727, badge_top)

    c.setFillColor(colors.HexColor("#171717"))
    agency_size = 16.5
    badge_w = 88
    badge_x = PAGE_W - RIGHT - badge_w
    agency_width = badge_x - (LEFT + 4) - 9
    while (
        pdfmetrics.stringWidth(doc.fields["AGENCY"], "NimbusNarrow-Bold", agency_size)
        > agency_width
        and agency_size > 12.0
    ):
        agency_size -= 0.25
    c.setFont("NimbusNarrow-Bold", agency_size)
    c.drawString(LEFT + 4, 696, doc.fields["AGENCY"])
    c.setFillColor(colors.HexColor("#555555"))
    c.setFont("NimbusNarrow-Bold", 8.2)
    c.drawString(LEFT + 4, 683, doc.fields["UNIT"])
    c.setFont("NimbusNarrow", 6.6)
    c.drawString(LEFT + 4, 673, f'REVIEW: {doc.fields["REVIEW"]}')

    c.setFillColor(dark)
    c.rect(badge_x, 668, badge_w, 51, stroke=0, fill=1)
    c.setFillColor(colors.white)
    badge_size = 16.0
    while (
        pdfmetrics.stringWidth(badge_bottom, "NimbusNarrow-Bold", badge_size)
        > badge_w - 10
        and badge_size > 11.0
    ):
        badge_size -= 0.25
    c.setFont("NimbusNarrow-Bold", badge_size)
    c.drawCentredString(badge_x + badge_w / 2, 696, badge_bottom)
    motto_size = 6.4
    while (
        pdfmetrics.stringWidth(doc.fields["MOTTO"], "NimbusNarrow-Bold", motto_size)
        > badge_w - 8
        and motto_size > 4.4
    ):
        motto_size -= 0.2
    c.setFont("NimbusNarrow-Bold", motto_size)
    c.drawCentredString(badge_x + badge_w / 2, 682, doc.fields["MOTTO"])

    meta_y = 637
    meta_h = 28
    widths = [218, 116, CONTENT_W - 334]
    x = LEFT
    meta = [
        ("PRODUCT", doc.fields["PRODUCT"]),
        ("DATE", doc.fields["DATE"]),
        ("DISTRIBUTION", doc.fields["DISTRIBUTION"]),
    ]
    for index, ((label, value), width) in enumerate(zip(meta, widths)):
        c.setFillColor(colors.HexColor("#E9E9E9"))
        c.setStrokeColor(colors.HexColor("#B8B8B8"))
        c.setLineWidth(0.35)
        c.rect(x, meta_y, width, meta_h, stroke=1, fill=1)
        c.setFillColor(colors.HexColor("#666666"))
        c.setFont("NimbusNarrow-Bold", 5.8)
        c.drawString(x + 5, meta_y + 18, label)
        c.setFillColor(colors.HexColor("#242424"))
        c.setFont("NimbusNarrow-Bold", 7.2)
        c.drawString(x + 5, meta_y + 7, value)
        x += width


def draw_watermark(c: canvas.Canvas, text: str, color: colors.Color) -> None:
    c.saveState()
    watermark_color = (
        colors.HexColor("#F4E9E9")
        if "KAPALA-HALO" in text
        else colors.HexColor("#EAF1F1")
    )
    c.setFillColor(watermark_color)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(36)
    c.setFont("NimbusNarrow-Bold", 28)
    c.drawCentredString(0, 0, text)
    c.restoreState()


def choose_layout(
    docs: list[RenderDoc],
) -> tuple[float, dict[str, ParagraphStyle], list[float], list[list[list[tuple[Paragraph, float]]]]]:
    available = 514
    heading_space = 13.5
    section_gap = 3.0
    content_width = CONTENT_W - 12

    for font_size in [9.15, 9.0, 8.85, 8.7, 8.55, 8.4, 8.25, 8.1]:
        style_map = styles(font_size)
        all_measured: list[list[list[tuple[Paragraph, float]]]] = []
        max_heights: list[float] = []

        for doc in docs:
            doc_measured = []
            for index, section in enumerate(doc.sections):
                _, measured = measure_section(
                    section,
                    style_map,
                    content_width,
                    summary=index == 0,
                )
                doc_measured.append(measured)
            all_measured.append(doc_measured)

        for section_index in range(6):
            heights = []
            for doc_index in range(2):
                measured = all_measured[doc_index][section_index]
                height = sum(
                    para_height + para.style.spaceAfter
                    for para, para_height in measured
                )
                if measured:
                    height -= measured[-1][0].style.spaceAfter
                heights.append(height)
            max_heights.append(max(heights))

        total = sum(max_heights) + (heading_space * 6) + (section_gap * 5)
        if total <= available:
            return font_size, style_map, max_heights, all_measured

    raise RuntimeError("Content does not fit the one-page layout")


def draw_doc(
    c: canvas.Canvas,
    doc: RenderDoc,
    doc_index: int,
    style_map: dict[str, ParagraphStyle],
    max_heights: list[float],
    measured_docs: list[list[list[tuple[Paragraph, float]]]],
) -> None:
    classified = "CLASSIFIED" in doc.render_name
    accent = colors.HexColor("#9C2025" if classified else "#356B70")
    dark = colors.HexColor("#252525" if classified else "#2F5458")
    badge_top = "M//LES" if classified else "CIV//DEFAULT"
    badge_bottom = "44-7 / KH" if classified else "44-7A / CIVIC"

    c.setFillColor(colors.HexColor("#FCFCFB"))
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    draw_watermark(
        c,
        "KAPALA-HALO // AUTHORIZED" if classified else "DEFAULT OVERLAY // CIVIC",
        accent,
    )
    draw_header(c, doc, accent, dark, badge_top, badge_bottom)
    draw_title(c, doc, accent)

    top = 557
    heading_space = 13.5
    section_gap = 3.0
    content_x = LEFT + 6
    content_width = CONTENT_W - 12

    for section_index, section in enumerate(doc.sections):
        content_height = max_heights[section_index]
        block_height = heading_space + content_height
        block_bottom = top - block_height

        if section_index == 0:
            c.setFillColor(colors.HexColor("#F0F0EF"))
            c.rect(LEFT, block_bottom - 2, CONTENT_W, block_height + 2, stroke=0, fill=1)
            c.setFillColor(accent)
            c.rect(LEFT, block_bottom - 2, 2.5, block_height + 2, stroke=0, fill=1)

        c.setFillColor(colors.HexColor("#343434"))
        c.setFont("NimbusNarrow-Bold", 7.8)
        c.drawString(LEFT + (5 if section_index == 0 else 0), top - 8.5, section.title)
        c.setStrokeColor(colors.HexColor("#777777"))
        c.setLineWidth(0.35)
        c.line(LEFT, top - 11.5, PAGE_W - RIGHT, top - 11.5)

        measured = measured_docs[doc_index][section_index]
        draw_paragraphs(
            c,
            measured,
            content_x,
            top - heading_space - 1,
            content_width,
        )
        top = block_bottom - section_gap

    footer_y = 17
    c.setStrokeColor(dark)
    c.setLineWidth(0.8)
    c.line(LEFT, footer_y + 12, PAGE_W - RIGHT, footer_y + 12)
    footer = Paragraph(to_rl_markup(doc.footer), style_map["footer"])
    _, footer_h = footer.wrap(CONTENT_W - 40, 30)
    footer.drawOn(c, LEFT + 20, footer_y + 2)
    c.setFillColor(dark)
    c.setFont("NimbusNarrow-Bold", 6.3)
    c.drawRightString(PAGE_W - RIGHT, 7, f"1 OF 1 // {badge_top}")

    c.showPage()


def count_visible_words(docs: list[RenderDoc]) -> list[int]:
    counts = []
    for doc in docs:
        pieces = [
            value for key, value in doc.fields.items() if key != "FOOTER"
        ]
        pieces.extend([doc.title, doc.subtitle])
        pieces.extend(section.title for section in doc.sections)
        pieces.extend(section.body for section in doc.sections)
        pieces.append(doc.footer)
        classified = "CLASSIFIED" in doc.render_name
        pieces.append("PRODUCT DATE DISTRIBUTION REVIEW")
        pieces.append(
            "M LES 44-7 KH 1 OF 1 M LES"
            if classified
            else "CIV DEFAULT 44-7A CIVIC 1 OF 1 CIV DEFAULT"
        )
        text = " ".join(pieces)
        text = re.sub(r"[*_#>`\[\]()]", " ", text)
        words = re.findall(r"\b[\w]+(?:[-’'][\w]+)*\b", text, flags=re.UNICODE)
        counts.append(len(words))
    return counts


def render() -> None:
    register_fonts()
    docs = parse_master(MASTER)
    counts = count_visible_words(docs)
    allow_over_limit = os.environ.get("ALLOW_OVER_1000") == "1"
    if sum(counts) >= 1000 and not allow_over_limit:
        raise ValueError(f"Aggregate word count is {sum(counts)}, not under 1,000")

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    font_size, style_map, max_heights, measured_docs = choose_layout(docs)
    c = canvas.Canvas(str(FINAL_PDF), pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle("SECOND COAT")
    c.setAuthor("Spencer N.")
    c.setSubject("Jamverse Jam - Zoothesia paired visual artifact")

    for index, doc in enumerate(docs):
        draw_doc(c, doc, index, style_map, max_heights, measured_docs)
    c.save()

    reader = PdfReader(str(FINAL_PDF))
    if len(reader.pages) != 2:
        raise RuntimeError(f"Expected 2 PDF pages, found {len(reader.pages)}")

    prefix = TMP_DIR / "second-coat"
    for old in TMP_DIR.glob("second-coat-*.png"):
        old.unlink()
    subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-r",
            "144",
            str(FINAL_PDF),
            str(prefix),
        ],
        check=True,
    )

    outputs = [
        IMAGE_DIR / "SECOND_COAT_Civic_Render.png",
        IMAGE_DIR / "SECOND_COAT_Classified_Render.png",
    ]
    for index, output in enumerate(outputs, start=1):
        source = TMP_DIR / f"second-coat-{index}.png"
        with Image.open(source) as image:
            if image.size != (1200, 1500):
                raise RuntimeError(f"Unexpected image size {image.size} for {source}")
            image.verify()
        shutil.copyfile(source, output)
        with Image.open(output) as image:
            image.verify()

    print(
        f"font_size={font_size:.2f} "
        f"civic_words={counts[0]} classified_words={counts[1]} "
        f"aggregate_words={sum(counts)}"
    )
    print(FINAL_PDF)
    for output in outputs:
        print(output)


if __name__ == "__main__":
    render()
