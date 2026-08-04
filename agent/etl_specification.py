"""Build deterministic ETL specification documents from validated mappings."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape as html_escape
from io import BytesIO
from textwrap import wrap
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as PdfImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .contracts import FieldMapping, TargetField
from .validation import ValidatedSpecs

EtlSpecificationFormat = Literal["md", "docx", "pdf"]

_GREEN = "0B6657"
_GREEN_SOFT = "DCEEE8"
_INK = "081917"
_MUTED = "536B65"
_LINE = "DCE5E1"
_AMBER = "C56F45"
_MAXIMUM_ROWS = 500
_FIGURE_ROWS_PER_IMAGE = 12


@dataclass(frozen=True)
class EtlSpecificationRow:
    """One field-level row in an ETL specification."""

    destination_field: str
    source_field: str
    logic: str
    comment: str


@dataclass(frozen=True)
class EtlSpecificationDocument:
    """Renderer-independent ETL specification content."""

    cdm_version: str
    target_table: str
    table_description: str
    table_notes: str
    source_models: tuple[str, ...]
    relationships: tuple[str, ...]
    rows: tuple[EtlSpecificationRow, ...]
    changes: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class EtlSpecificationArtifact:
    """One bounded downloadable ETL specification file."""

    content: bytes
    file_name: str
    media_type: str


def _source_label(mapping: FieldMapping) -> str:
    if not mapping.source_fields:
        return "—"
    return "\n".join(
        f"{reference.model}.{reference.field}"
        for reference in mapping.source_fields
    )


def _logic(mapping: FieldMapping, target: TargetField) -> str:
    transformation = mapping.transformation.strip()
    if mapping.action == "null":
        return transformation or "Set NULL."

    parts: list[str] = []
    if mapping.mapping_table_name:
        parts.append(f"Lookup using {mapping.mapping_table_name}.")
    if transformation:
        parts.append(transformation)
    elif mapping.source_fields:
        parts.append(
            "Direct mapping; conform the value to the OMOP "
            f"{target.data_type} datatype."
        )
    return " ".join(parts) or "No transformation logic recorded."


def _comment(mapping: FieldMapping) -> str:
    parts = []
    if mapping.comment.strip():
        parts.append(mapping.comment.strip())
    if mapping.review_comment and mapping.review_comment.strip():
        parts.append(f"Review: {mapping.review_comment.strip()}")
    return "\n".join(parts) or "—"


def _relationships(specs: ValidatedSpecs) -> tuple[str, ...]:
    relationships = [
        (
            f"{join.join_type.upper()} JOIN "
            f"{join.left.model}.{join.left.field} = "
            f"{join.right.model}.{join.right.field}"
        )
        for join in specs.mapping.joins
    ]
    if specs.mapping.union_all:
        relationships.append(
            "UNION ALL: " + ", ".join(specs.mapping.union_all)
        )
    return tuple(relationships)


def build_etl_specification_document(
    specs: ValidatedSpecs,
) -> EtlSpecificationDocument:
    """Create a deterministic document model in OMOP target-field order."""
    mappings = {
        mapping.target_field: mapping
        for mapping in specs.mapping.fields
    }
    rows: list[EtlSpecificationRow] = []
    for target in specs.target_schema.fields:
        mapping = mappings.get(target.name)
        if mapping is None:
            rows.append(
                EtlSpecificationRow(
                    destination_field=target.name,
                    source_field="—",
                    logic=(
                        "Set NULL because this optional OMOP field is not "
                        "configured in the mapping."
                    ),
                    comment="—",
                )
            )
            continue
        rows.append(
            EtlSpecificationRow(
                destination_field=target.name,
                source_field=_source_label(mapping),
                logic=_logic(mapping, target),
                comment=_comment(mapping),
            )
        )

    if len(rows) > _MAXIMUM_ROWS:
        raise ValueError("The ETL specification exceeds the row limit.")

    return EtlSpecificationDocument(
        cdm_version=specs.target_schema.cdm_version,
        target_table=specs.mapping.target_table,
        table_description=specs.target_schema.description.strip(),
        table_notes=specs.mapping.notes.strip(),
        source_models=tuple(specs.mapping.source_models),
        relationships=_relationships(specs),
        rows=tuple(rows),
        changes=tuple(
            (
                change.date.isoformat(),
                change.description.strip(),
                change.author.strip() or "—",
            )
            for change in specs.mapping.change_log
        ),
    )


def _markdown_cell(value: str) -> str:
    return (
        html_escape(value, quote=False)
        .replace("|", "\\|")
        .replace("\n", "<br>")
    )


def _markdown_text(value: str) -> str:
    """Prevent submitted specification text from becoming active HTML."""
    return html_escape(value, quote=False)


def _mermaid_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "&quot;")


def _markdown_figure(specs: ValidatedSpecs) -> str:
    lines = ["```mermaid", "flowchart LR"]
    source_nodes: dict[str, str] = {}
    target_nodes: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    for mapping in specs.mapping.fields:
        if mapping.action == "null":
            continue
        for reference in mapping.source_fields:
            source_key = f"{reference.model}.{reference.field}"
            source_id = source_nodes.setdefault(
                source_key,
                f"source_{len(source_nodes)}",
            )
            target_id = target_nodes.setdefault(
                mapping.target_field,
                f"target_{len(target_nodes)}",
            )
            label = (
                f"lookup: {mapping.mapping_table_name}"
                if mapping.mapping_table_name
                else mapping.action
            )
            edges.append((source_id, target_id, label))

    for label, node_id in source_nodes.items():
        lines.append(f'  {node_id}["{_mermaid_label(label)}"]')
    for label, node_id in target_nodes.items():
        lines.append(f'  {node_id}["{_mermaid_label(label)}"]')
    for source_id, target_id, label in edges:
        lines.append(
            f'  {source_id} -- "{_mermaid_label(label)}" --> {target_id}'
        )
    if not edges:
        lines.append('  empty["No configured source-field mappings"]')
    lines.extend(
        [
            f"  classDef source fill:#{_GREEN_SOFT},stroke:#{_GREEN}",
            "  classDef target fill:#E4EFF0,stroke:#356D73",
        ]
    )
    if source_nodes:
        lines.append("  class " + ",".join(source_nodes.values()) + " source")
    if target_nodes:
        lines.append("  class " + ",".join(target_nodes.values()) + " target")
    lines.append("```")
    return "\n".join(lines)


def render_markdown(
    document: EtlSpecificationDocument,
    specs: ValidatedSpecs,
) -> bytes:
    """Render a portable Markdown ETL specification."""
    lines = [
        f"# {document.target_table} ETL specification",
        "",
        f"**OMOP CDM version:** {document.cdm_version}",
        f"**Source models:** {', '.join(document.source_models)}",
        "**Generation method:** Deterministic; no AI request",
        "",
        "## Overview",
        "",
        _markdown_text(
            document.table_notes
            or document.table_description
            or "No table-level notes recorded."
        ),
        "",
        "## Mapping overview",
        "",
        _markdown_figure(specs),
        "",
    ]
    if document.relationships:
        lines.extend(["## Source relationships", ""])
        lines.extend(
            f"- {_markdown_text(item)}"
            for item in document.relationships
        )
        lines.append("")
    lines.extend(
        [
            "## Field mapping",
            "",
            "| Destination Field | Source field | Logic | Comment field |",
            "|---|---|---|---|",
        ]
    )
    lines.extend(
        "| "
        + " | ".join(
            _markdown_cell(value)
            for value in (
                row.destination_field,
                row.source_field,
                row.logic,
                row.comment,
            )
        )
        + " |"
        for row in document.rows
    )
    lines.extend(
        [
            "",
            "## Change log",
            "",
            "| Date | Description | Author |",
            "|---|---|---|",
        ]
    )
    if document.changes:
        lines.extend(
            "| "
            + " | ".join(_markdown_cell(value) for value in change)
            + " |"
            for change in document.changes
        )
    else:
        lines.append("| — | No changes recorded. | — |")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size=size)
    except OSError:
        return ImageFont.load_default(size=size)


def _wrapped_lines(value: str, width: int) -> list[str]:
    result: list[str] = []
    for line in value.splitlines() or [""]:
        result.extend(wrap(line, width=width) or [""])
    return result


def _mapping_figure_images(specs: ValidatedSpecs) -> list[bytes]:
    mappings = [
        mapping
        for mapping in specs.mapping.fields
        if mapping.action != "null" and mapping.source_fields
    ]
    chunks = [
        mappings[index:index + _FIGURE_ROWS_PER_IMAGE]
        for index in range(0, len(mappings), _FIGURE_ROWS_PER_IMAGE)
    ] or [[]]
    images: list[bytes] = []
    for chunk_index, chunk in enumerate(chunks):
        width = 1600
        header_height = 118
        row_height = 78
        height = header_height + max(1, len(chunk)) * row_height + 28
        image = Image.new("RGB", (width, height), "#FFFFFF")
        draw = ImageDraw.Draw(image)
        title_font = _font(32, bold=True)
        header_font = _font(25, bold=True)
        text_font = _font(22)
        small_font = _font(18)
        title_suffix = (
            f" · {chunk_index + 1}/{len(chunks)}"
            if len(chunks) > 1
            else ""
        )
        draw.text(
            (36, 24),
            f"{specs.mapping.target_table} mapping{title_suffix}",
            fill=f"#{_INK}",
            font=title_font,
        )
        draw.rounded_rectangle(
            (28, 78, 670, 116),
            radius=8,
            fill=f"#{_GREEN_SOFT}",
        )
        draw.rounded_rectangle(
            (930, 78, 1572, 116),
            radius=8,
            fill="#E4EFF0",
        )
        draw.text((48, 82), "Source field", fill=f"#{_GREEN}", font=header_font)
        draw.text((950, 82), "OMOP destination", fill="#356D73", font=header_font)
        if not chunk:
            draw.text(
                (48, 145),
                "No configured source-field mappings.",
                fill=f"#{_MUTED}",
                font=text_font,
            )
        for row_index, mapping in enumerate(chunk):
            top = header_height + row_index * row_height
            if row_index % 2:
                draw.rectangle((28, top, 1572, top + row_height), fill="#F8FAF9")
            source = "\n".join(
                f"{reference.model}.{reference.field}"
                for reference in mapping.source_fields
            )
            for line_index, line in enumerate(_wrapped_lines(source, 48)[:2]):
                draw.text(
                    (48, top + 12 + line_index * 25),
                    line,
                    fill=f"#{_INK}",
                    font=text_font,
                )
            draw.line(
                (690, top + 39, 900, top + 39),
                fill=f"#{_AMBER}",
                width=4,
            )
            draw.polygon(
                [(900, top + 39), (884, top + 29), (884, top + 49)],
                fill=f"#{_AMBER}",
            )
            label = "lookup" if mapping.mapping_table_name else mapping.action
            draw.text(
                (735, top + 10),
                label,
                fill=f"#{_MUTED}",
                font=small_font,
            )
            draw.text(
                (950, top + 20),
                mapping.target_field,
                fill=f"#{_INK}",
                font=text_font,
            )
            draw.line(
                (28, top + row_height, 1572, top + row_height),
                fill=f"#{_LINE}",
                width=2,
            )
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        images.append(buffer.getvalue())
    return images


def _shade_docx_cell(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _set_docx_cell_text(cell, value: str, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    for index, line in enumerate(value.splitlines() or [""]):
        if index:
            paragraph.add_run().add_break()
        run = paragraph.add_run(line)
        run.bold = bold
        run.font.name = "Arial"
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor.from_string(_INK)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP


def _add_docx_heading(document, text: str, level: int) -> None:
    heading = document.add_heading(text, level=level)
    heading.style.font.name = "Arial"
    heading.style.font.color.rgb = RGBColor.from_string(_GREEN)


def _normalize_docx_archive(content: bytes) -> bytes:
    """Remove ZIP timestamps so identical inputs produce identical bytes."""
    source_buffer = BytesIO(content)
    output_buffer = BytesIO()
    with ZipFile(source_buffer, "r") as source, ZipFile(
        output_buffer,
        "w",
        compression=ZIP_DEFLATED,
        compresslevel=9,
    ) as destination:
        for name in sorted(source.namelist()):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            destination.writestr(entry, source.read(name))
    return output_buffer.getvalue()


def render_docx(
    content: EtlSpecificationDocument,
    specs: ValidatedSpecs,
) -> bytes:
    """Render a landscape Microsoft Word ETL specification."""
    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.top_margin = Inches(0.55)
    section.bottom_margin = Inches(0.55)
    section.left_margin = Inches(0.55)
    section.right_margin = Inches(0.55)
    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(9.5)
    document.core_properties.title = f"{content.target_table} ETL specification"
    document.core_properties.author = "CardiacAI OMOP Agent"

    title = document.add_heading(f"{content.target_table} ETL specification", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title.style.font.name = "Arial"
    title.style.font.color.rgb = RGBColor.from_string(_INK)
    metadata = document.add_paragraph()
    metadata.add_run(f"OMOP CDM {content.cdm_version}  ·  ").bold = True
    metadata.add_run(f"Sources: {', '.join(content.source_models)}  ·  ")
    metadata.add_run("Deterministic output; no AI request")

    _add_docx_heading(document, "Overview", 1)
    document.add_paragraph(
        content.table_notes
        or content.table_description
        or "No table-level notes recorded."
    )
    _add_docx_heading(document, "Mapping overview", 1)
    for figure in _mapping_figure_images(specs):
        picture = document.add_picture(BytesIO(figure), width=Inches(9.7))
        picture.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if content.relationships:
        _add_docx_heading(document, "Source relationships", 1)
        for relationship in content.relationships:
            document.add_paragraph(relationship, style="List Bullet")

    _add_docx_heading(document, "Field mapping", 1)
    table = document.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    table.autofit = False
    widths = (Inches(1.55), Inches(2.15), Inches(3.85), Inches(2.55))
    headers = ("Destination Field", "Source field", "Logic", "Comment field")
    for index, (cell, label) in enumerate(zip(table.rows[0].cells, headers)):
        cell.width = widths[index]
        _shade_docx_cell(cell, _GREEN_SOFT)
        _set_docx_cell_text(cell, label, bold=True)
    header_properties = table.rows[0]._tr.get_or_add_trPr()
    header_properties.append(OxmlElement("w:tblHeader"))
    for row in content.rows:
        cells = table.add_row().cells
        for index, value in enumerate(
            (row.destination_field, row.source_field, row.logic, row.comment)
        ):
            cells[index].width = widths[index]
            _set_docx_cell_text(cells[index], value)

    _add_docx_heading(document, "Change log", 1)
    change_table = document.add_table(rows=1, cols=3)
    change_table.style = "Table Grid"
    for cell, label in zip(
        change_table.rows[0].cells,
        ("Date", "Description", "Author"),
    ):
        _shade_docx_cell(cell, _GREEN_SOFT)
        _set_docx_cell_text(cell, label, bold=True)
    changes = content.changes or (("—", "No changes recorded.", "—"),)
    for change in changes:
        cells = change_table.add_row().cells
        for cell, value in zip(cells, change):
            _set_docx_cell_text(cell, value)

    buffer = BytesIO()
    document.save(buffer)
    return _normalize_docx_archive(buffer.getvalue())


def _pdf_paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(
        html_escape(value).replace("\n", "<br/>") or "—",
        style,
    )


def render_pdf(
    content: EtlSpecificationDocument,
    specs: ValidatedSpecs,
) -> bytes:
    """Render a landscape A4 PDF ETL specification."""
    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        invariant=1,
        title=f"{content.target_table} ETL specification",
        author="CardiacAI OMOP Agent",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "EtlTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor(f"#{_INK}"),
        spaceAfter=8,
    )
    heading_style = ParagraphStyle(
        "EtlHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor(f"#{_GREEN}"),
        spaceBefore=9,
        spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "EtlBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor(f"#{_INK}"),
    )
    small_style = ParagraphStyle(
        "EtlSmall",
        parent=body_style,
        fontSize=7.5,
        leading=9.5,
    )
    header_style = ParagraphStyle(
        "EtlHeader",
        parent=small_style,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        textColor=colors.HexColor(f"#{_GREEN}"),
    )

    story = [
        Paragraph(
            f"{html_escape(content.target_table)} ETL specification",
            title_style,
        ),
        _pdf_paragraph(
            f"OMOP CDM {content.cdm_version} · Sources: "
            f"{', '.join(content.source_models)} · Deterministic output; "
            "no AI request",
            body_style,
        ),
        Paragraph("Overview", heading_style),
        _pdf_paragraph(
            content.table_notes
            or content.table_description
            or "No table-level notes recorded.",
            body_style,
        ),
        Paragraph("Mapping overview", heading_style),
    ]
    figures = _mapping_figure_images(specs)
    for figure in figures:
        image = PdfImage(BytesIO(figure))
        scale = min(
            (255 * mm) / image.imageWidth,
            (145 * mm) / image.imageHeight,
            1,
        )
        image.drawWidth = image.imageWidth * scale
        image.drawHeight = image.imageHeight * scale
        story.extend([image, Spacer(1, 3 * mm)])
    if content.relationships:
        story.append(Paragraph("Source relationships", heading_style))
        for relationship in content.relationships:
            story.append(_pdf_paragraph(f"• {relationship}", body_style))

    story.append(Paragraph("Field mapping", heading_style))
    table_data = [[
        Paragraph("Destination Field", header_style),
        Paragraph("Source field", header_style),
        Paragraph("Logic", header_style),
        Paragraph("Comment field", header_style),
    ]]
    table_data.extend(
        [
            _pdf_paragraph(row.destination_field, small_style),
            _pdf_paragraph(row.source_field, small_style),
            _pdf_paragraph(row.logic, small_style),
            _pdf_paragraph(row.comment, small_style),
        ]
        for row in content.rows
    )
    mapping_table = Table(
        table_data,
        colWidths=(38 * mm, 53 * mm, 92 * mm, 72 * mm),
        repeatRows=1,
        hAlign="LEFT",
    )
    mapping_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{_GREEN_SOFT}")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor(f"#{_LINE}")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F8FAF9")],
                ),
            ]
        )
    )
    story.append(mapping_table)
    story.append(Paragraph("Change log", heading_style))
    changes = content.changes or (("—", "No changes recorded.", "—"),)
    change_data = [[
        Paragraph("Date", header_style),
        Paragraph("Description", header_style),
        Paragraph("Author", header_style),
    ]]
    change_data.extend(
        [_pdf_paragraph(value, small_style) for value in change]
        for change in changes
    )
    change_table = Table(
        change_data,
        colWidths=(35 * mm, 170 * mm, 50 * mm),
        repeatRows=1,
        hAlign="LEFT",
    )
    change_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{_GREEN_SOFT}")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor(f"#{_LINE}")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(change_table)
    pdf.build(story)
    return buffer.getvalue()


def build_etl_specification(
    specs: ValidatedSpecs,
    output_format: EtlSpecificationFormat,
) -> EtlSpecificationArtifact:
    """Render one validated mapping as Markdown, Word, or PDF."""
    document = build_etl_specification_document(specs)
    renderers = {
        "md": (render_markdown, "text/markdown; charset=utf-8"),
        "docx": (
            render_docx,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "pdf": (render_pdf, "application/pdf"),
    }
    renderer, media_type = renderers[output_format]
    content = renderer(document, specs)
    if not content:
        raise ValueError("The ETL specification is empty.")
    return EtlSpecificationArtifact(
        content=content,
        file_name=f"{document.target_table}_etl_specification.{output_format}",
        media_type=media_type,
    )
