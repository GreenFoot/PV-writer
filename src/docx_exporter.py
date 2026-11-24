"""Word (.docx) export of the PV, styled like the reference PVs.

The document is built from the agenda tree and the section bodies (not by converting a full
HTML page): Title, ``Agenda :`` with internal hyperlinks to bookmarks on every section
heading, a separator, then the numbered headings and their bodies. The result opens in
Word, LibreOffice or Google Docs, where images and tables are added and the PDF exported.

The body blocks are styled by role (see ``classify_blocks``): a topic opened by a lone bold
bullet becomes a small heading with a coloured bar, "Réaction des parents :" style labels
stay with what follows, and "->" follow-up lines are set apart in a shaded box. The page is
A4 with the title in the header and the page number in the footer. Pictures (``<img>`` in
a body, relative to the meeting folder) are centred and fit the text column.
"""

import logging
import os

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Emu, Pt, RGBColor

from .agenda import AgendaNode, iter_nodes
from .pv_document import (
    AGENDA_LABEL,
    COLOR_H1,
    COLOR_H2,
    COLOR_H3,
    COLOR_TITLE,
    EMPTY_CELL,
    SUMMARY_ACTIONS_LABEL,
    SUMMARY_COLUMNS,
    SUMMARY_LABEL,
    SUMMARY_POINTS_LABEL,
    Block,
    classify_blocks,
    parse_fragment,
)

log = logging.getLogger("pvwriter.docx")

FONT_BODY = "Arial"
FONT_TITLE = "Montserrat"  # falls back to the default font when not installed
_HEADING_STYLE = {1: ("Heading 1", COLOR_H1, 20), 2: ("Heading 2", COLOR_H2, 16), 3: ("Heading 3", COLOR_H3, 14)}
_LINK_COLOR = "1155CC"
_TEXT_COLOR = "222222"
_MUTED_COLOR = "666666"
_SUBTOPIC_COLOR = "1F4E79"
_LABEL_COLOR = "45818E"
_ACTION_FILL = "EEF4FB"
_ACTION_PREFIXES = ("->", "→")

_TEXT_WIDTH_CM = 21 - 2 * 2.2  # A4 minus the side margins
_PX_TO_CM = 2.54 / 96

STYLE_SUBTOPIC = "PV Sujet"
STYLE_LABEL = "PV Intitulé"
STYLE_ACTION = "PV Suite"

# Children of w:pPr that must come after w:pBdr / w:shd (OOXML schema order)
_AFTER_PBDR = (
    "w:shd",
    "w:tabs",
    "w:suppressAutoHyphens",
    "w:kinsoku",
    "w:wordWrap",
    "w:overflowPunct",
    "w:topLinePunct",
    "w:autoSpaceDE",
    "w:autoSpaceDN",
    "w:bidi",
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
)


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#").upper())


def _set_font(style, name: str, size: float | None = None, color: str | None = None, bold=None, italic=None):
    """Set font properties on a style, including the East-Asian font slot."""
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), name)
    # Theme fonts in the default template would override the explicit names
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if fonts.get(qn(attr)) is not None:
            del fonts.attrib[qn(attr)]
    if size is not None:
        style.font.size = Pt(size)
    if color is not None:
        style.font.color.rgb = _rgb(color)
    if bold is not None:
        style.font.bold = bold
    if italic is not None:
        style.font.italic = italic


def _remove_borders(style):
    """Remove paragraph borders a template may put on the Title style."""
    ppr = style.element.find(qn("w:pPr"))
    if ppr is not None:
        for border in ppr.findall(qn("w:pBdr")):
            ppr.remove(border)


def _set_border(ppr, side: str, size: int, color: str, space: int):
    """Add (or replace) one paragraph border on a ``w:pPr`` element.

    Args:
        ppr: The paragraph or style properties element.
        side: ``top``, ``left``, ``bottom`` or ``right``.
        size: Width in eighths of a point.
        color: Hex colour.
        space: Gap between border and text, in points.
    """
    borders = ppr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        ppr.insert_element_before(borders, *_AFTER_PBDR)
    for old in borders.findall(qn(f"w:{side}")):
        borders.remove(old)
    border = OxmlElement(f"w:{side}")
    for key, value in (("w:val", "single"), ("w:sz", str(size)), ("w:space", str(space))):
        border.set(qn(key), value)
    border.set(qn("w:color"), color.lstrip("#").upper())
    # Keep the schema order top, left, bottom, right
    order = ["top", "left", "bottom", "right"]
    following = [qn(f"w:{s}") for s in order[order.index(side) + 1 :]]
    anchor = next((child for child in borders if child.tag in following), None)
    if anchor is not None:
        anchor.addprevious(border)
    else:
        borders.append(border)


def _set_shading(ppr, fill: str):
    """Give a paragraph or style a background colour."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    ppr.insert_element_before(shd, *_AFTER_PBDR[1:])


def _paragraph_style(doc, name: str, base: str = "Normal"):
    """Return a custom paragraph style, creating it on first use."""
    styles = doc.styles
    if name in [s.name for s in styles if s.type == WD_STYLE_TYPE.PARAGRAPH]:
        return styles[name]
    style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = styles[base]
    style.quick_style = True
    return style


def _configure_styles(doc):
    """Apply the reference look (Arial body, coloured non-bold headings) and the body roles."""
    styles = doc.styles
    normal = styles["Normal"]
    _set_font(normal, FONT_BODY, 11, _TEXT_COLOR)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15
    normal.paragraph_format.widow_control = True
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    title = styles["Title"]
    _set_font(title, FONT_TITLE, 24, COLOR_TITLE, bold=False)
    _remove_borders(title)
    title.paragraph_format.space_after = Pt(12)

    for level, (name, color, size) in _HEADING_STYLE.items():
        style = styles[name]
        _set_font(style, FONT_BODY, size, color, bold=False, italic=False)
        fmt = style.paragraph_format
        fmt.space_before = Pt({1: 24, 2: 16, 3: 12}[level])
        fmt.space_after = Pt(6)
        fmt.keep_with_next = True
        fmt.keep_together = True
    # A thin rule under each main section
    _set_border(styles["Heading 1"].element.get_or_add_pPr(), "bottom", 4, COLOR_H1, 2)

    for name in ("List Bullet", "List Bullet 2", "List Bullet 3", "List Number", "List Number 2", "List Number 3"):
        if name in [s.name for s in styles if s.type == WD_STYLE_TYPE.PARAGRAPH]:
            styles[name].paragraph_format.space_after = Pt(3)
            styles[name].paragraph_format.line_spacing = 1.15
    # Nested items (answers, details) are slightly quieter than their parent item
    for name in ("List Bullet 2", "List Bullet 3", "List Number 2", "List Number 3"):
        _set_font(styles[name], FONT_BODY, 10.5, _MUTED_COLOR)

    subtopic = _paragraph_style(doc, STYLE_SUBTOPIC)
    _set_font(subtopic, FONT_BODY, 12, _SUBTOPIC_COLOR, bold=True)
    fmt = subtopic.paragraph_format
    fmt.space_before = Pt(14)
    fmt.space_after = Pt(6)
    fmt.keep_with_next = True
    _set_border(subtopic.element.get_or_add_pPr(), "left", 24, COLOR_H1, 6)

    label = _paragraph_style(doc, STYLE_LABEL)
    _set_font(label, FONT_BODY, 11, _LABEL_COLOR, bold=True)
    label.paragraph_format.space_before = Pt(8)
    label.paragraph_format.space_after = Pt(3)
    label.paragraph_format.keep_with_next = True

    action = _paragraph_style(doc, STYLE_ACTION)
    fmt = action.paragraph_format
    fmt.space_before = Pt(4)
    fmt.space_after = Pt(8)
    fmt.left_indent = Cm(0.3)
    ppr = action.element.get_or_add_pPr()
    _set_border(ppr, "left", 18, COLOR_TITLE, 6)
    _set_shading(ppr, _ACTION_FILL)


def _configure_page(doc, title: str):
    """A4 portrait page, the title in the header and "Page X / Y" in the footer."""
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2)
    section.header_distance = section.footer_distance = Cm(1)
    # The first page already shows the big title
    section.different_first_page_header_footer = True

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header.add_run(title)
    run.font.size = Pt(8.5)
    run.font.color.rgb = _rgb(_MUTED_COLOR)

    for footer in (section.footer, section.first_page_footer):
        paragraph = footer.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for part in ("Page ", "PAGE", " / ", "NUMPAGES"):
            if part.isupper():
                _add_field(paragraph, part)
            else:
                paragraph.add_run(part)
        for run in paragraph.runs:
            run.font.size = Pt(8.5)
            run.font.color.rgb = _rgb(_MUTED_COLOR)


def _add_field(paragraph, instruction: str):
    """Append a Word field (PAGE, NUMPAGES) to a paragraph.

    A complex field (begin / instruction / separate / result / end runs) keeps the run
    formatting when Word updates the result, unlike ``w:fldSimple``.
    """

    def _char(kind: str):
        run = paragraph.add_run()
        char = OxmlElement("w:fldChar")
        char.set(qn("w:fldCharType"), kind)
        run._r.append(char)  # pylint: disable=protected-access

    _char("begin")
    run = paragraph.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    run._r.append(instr)  # pylint: disable=protected-access
    _char("separate")
    paragraph.add_run("1")
    _char("end")


def _add_bookmark(paragraph, name: str, bookmark_id: int):
    """Wrap a paragraph's content in a Word bookmark."""
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bookmark_id))
    p = paragraph._p  # pylint: disable=protected-access
    ppr = p.find(qn("w:pPr"))
    p.insert(1 if ppr is not None else 0, start)
    p.append(end)


def _add_internal_link(paragraph, text: str, anchor: str):
    """Append a hyperlink run pointing at a bookmark of the same document."""
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), anchor)
    link.set(qn("w:history"), "1")
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), _LINK_COLOR)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(color)
    rpr.append(underline)
    run.append(rpr)
    text_el = OxmlElement("w:t")
    text_el.text = text
    text_el.set(qn("xml:space"), "preserve")
    run.append(text_el)
    link.append(run)
    paragraph._p.append(link)  # pylint: disable=protected-access


def _add_separator(doc):
    """Add an empty paragraph with a bottom border, like the rule under the agenda."""
    paragraph = doc.add_paragraph()
    _set_border(paragraph._p.get_or_add_pPr(), "bottom", 6, "999999", 1)  # pylint: disable=protected-access


def _list_style(block: Block) -> str:
    """Return the built-in list style for a block's nesting level."""
    level = min(block.list_level, 3)
    base = "List Number" if block.ordered else "List Bullet"
    return base if level == 1 else f"{base} {level}"


def _add_runs(paragraph, block: Block, strip_action: bool = False):
    """Append a block's inline runs to a paragraph."""
    runs = list(block.runs)
    if strip_action and runs:
        first = runs[0].text.lstrip()
        for prefix in _ACTION_PREFIXES:
            if first.startswith(prefix):
                first = first[len(prefix) :].lstrip()
                break
        runs[0] = type(runs[0])(first, runs[0].bold, runs[0].italic, runs[0].underline)
        arrow = paragraph.add_run("→  ")
        arrow.bold = True
        arrow.font.color.rgb = _rgb(COLOR_TITLE)
    for run in runs:
        if run.text == "\n":
            paragraph.add_run().add_break()
            continue
        if not run.text:
            continue
        r = paragraph.add_run(run.text)
        r.bold = run.bold or None
        r.italic = run.italic or None
        r.underline = run.underline or None


def _add_image(doc, block: Block, image_root: str):
    """Append a centred picture, at its display width but never wider than the text column."""
    path = os.path.join(image_root, *block.image.split("/"))
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(6)
    paragraph.paragraph_format.space_after = Pt(9)
    if not os.path.isfile(path):
        log.warning("Missing PV image %s", path)
        paragraph.add_run(f"[image manquante : {block.image}]").italic = True
        return
    run = paragraph.add_run()
    max_width = Cm(_TEXT_WIDTH_CM)
    width = Cm(block.width * _PX_TO_CM) if block.width else None
    shape = run.add_picture(path, width=min(width, max_width) if width else None)
    if shape.width > max_width:
        ratio = max_width / shape.width
        shape.width, shape.height = Emu(int(max_width)), Emu(int(shape.height * ratio))


def _add_blocks(doc, blocks: list[Block], image_root: str = ""):
    """Append parsed fragment blocks to the document, styled by their role."""
    blocks = classify_blocks(blocks)
    for index, block in enumerate(blocks):
        nxt = blocks[index + 1] if index + 1 < len(blocks) else None
        if block.kind == "image":
            _add_image(doc, block, image_root)
            continue
        if block.kind == "subtopic":
            paragraph = doc.add_paragraph(block.text.strip(), style=STYLE_SUBTOPIC)
            continue
        if block.kind == "label":
            paragraph = doc.add_paragraph(style=STYLE_LABEL)
            paragraph.add_run(block.text.strip())
            continue
        if block.kind == "action":
            paragraph = doc.add_paragraph(style=STYLE_ACTION)
            _add_runs(paragraph, block, strip_action=True)
            continue

        style = _list_style(block) if block.list_level else None
        paragraph = doc.add_paragraph(style=style)
        _add_runs(paragraph, block)
        if block.list_level:
            in_same_list = nxt is not None and nxt.list_level and nxt.list_id == block.list_id
            if not in_same_list:
                # Air between the end of a list and the next paragraph
                paragraph.paragraph_format.space_after = Pt(9)
            elif nxt.list_level > block.list_level:
                # Keep a question with its answer
                paragraph.paragraph_format.keep_with_next = True


def _shade_cell(cell, fill: str):
    """Give a table cell a background colour."""
    tcpr = cell._tc.get_or_add_tcPr()  # pylint: disable=protected-access
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def _fill_cell(cell, text: str, bold: bool = False, color: str | None = None):
    """Write compact text into a table cell."""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT  # justified text gaps badly in narrow cells
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(2)
    paragraph.paragraph_format.line_spacing = 1.0
    run = paragraph.add_run(text)
    run.font.size = Pt(9.5)
    run.bold = bold or None
    if color:
        run.font.color.rgb = _rgb(color)


def _add_summary(doc, summary: dict | None) -> bool:
    """Append the synthesis after the agenda: main points, then the action table.

    Returns:
        True when something was written.
    """
    points = (summary or {}).get("points", [])
    actions = (summary or {}).get("actions", [])
    if not points and not actions:
        return False
    doc.add_paragraph(SUMMARY_LABEL, style="Heading 1")

    if points:
        doc.add_paragraph(SUMMARY_POINTS_LABEL, style=STYLE_SUBTOPIC)
        for point in points:
            paragraph = doc.add_paragraph(style="List Bullet")
            paragraph.paragraph_format.space_after = Pt(3)
            if point.get("sujet"):
                paragraph.add_run(f"{point['sujet']} : ").bold = True
            paragraph.add_run(point["texte"])

    if actions:
        doc.add_paragraph(SUMMARY_ACTIONS_LABEL, style=STYLE_SUBTOPIC)
        table = doc.add_table(rows=1, cols=len(SUMMARY_COLUMNS))
        table.style = doc.styles["Table Grid"]
        table.autofit = False
        widths = (Cm(10.4), Cm(3.0), Cm(3.2))
        for column, width in zip(table.columns, widths):
            column.width = width  # the grid Word uses for the layout
        for cell, label, width in zip(table.rows[0].cells, SUMMARY_COLUMNS, widths):
            cell.width = width
            _fill_cell(cell, label, bold=True, color="FFFFFF")
            _shade_cell(cell, COLOR_H1.lstrip("#"))
        # Repeat the header row if the table breaks across pages
        tr_pr = table.rows[0]._tr.get_or_add_trPr()  # pylint: disable=protected-access
        header = OxmlElement("w:tblHeader")
        header.set(qn("w:val"), "true")
        tr_pr.append(header)
        for index, action in enumerate(actions):
            row = table.add_row()
            # A row never splits across two pages
            cant_split = OxmlElement("w:cantSplit")
            row._tr.get_or_add_trPr().append(cant_split)  # pylint: disable=protected-access
            values = (action["action"], action.get("qui") or EMPTY_CELL, action.get("echeance") or EMPTY_CELL)
            for cell, value, width in zip(row.cells, values, widths):
                cell.width = width
                _fill_cell(cell, value)
                if index % 2:
                    _shade_cell(cell, _ACTION_FILL)
    return True


def _add_toc(doc, nodes: list[AgendaNode]):
    """Append the agenda as linked bullets, nested like the agenda tree."""
    for node in nodes:
        style = "List Bullet" if node.level == 1 else f"List Bullet {node.level}"
        paragraph = doc.add_paragraph(style=style)
        _add_internal_link(paragraph, node.toc_label, node.anchor)
        _add_toc(doc, node.children)


def export_docx(
    path: str,
    title: str,
    roots: list[AgendaNode],
    bodies: dict[str, str],
    summary: dict | None = None,
    image_root: str = "",
) -> str:
    """Write the PV as a .docx file.

    Args:
        path: Destination file.
        title: Document title (``PV … du JJ/MM/AAAA``).
        roots: Agenda tree.
        bodies: HTML fragment per agenda id (missing or empty bodies leave the heading alone).
        summary: Stage-D synthesis, written right after the agenda; None to skip.
        image_root: Folder the ``<img src>`` paths are relative to (the meeting folder).

    Returns:
        The path written.
    """
    doc = Document()
    _configure_styles(doc)
    _configure_page(doc, title)
    doc.core_properties.title = title
    doc.core_properties.language = "fr-BE"

    doc.add_paragraph(title, style="Title")
    agenda = doc.add_paragraph(AGENDA_LABEL, style="Heading 1")
    agenda.paragraph_format.space_before = Pt(6)
    _add_toc(doc, roots)
    _add_separator(doc)
    # After the synthesis, the detailed PV starts on a new page
    has_summary = _add_summary(doc, summary)

    for bookmark_id, node in enumerate(iter_nodes(roots), start=1):
        heading = doc.add_paragraph(node.heading, style=_HEADING_STYLE[node.level][0])
        if has_summary and bookmark_id == 1:
            heading.paragraph_format.page_break_before = True
            heading.paragraph_format.space_before = Pt(0)
        _add_bookmark(heading, node.anchor, bookmark_id)
        _add_blocks(doc, parse_fragment(bodies.get(node.node_id, "")), image_root)

    doc.save(path)
    log.info("PV exported to %s", path)
    return path
