"""Word (.docx) export of the PV, styled like the reference PVs.

The document is built from the agenda tree and the section bodies (not by converting a full
HTML page): Title, ``Agenda :`` with internal hyperlinks to bookmarks on every section
heading, a separator, then the numbered headings and their bodies. The result opens in
Word, LibreOffice or Google Docs, where images and tables are added and the PDF exported.
"""

import logging

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .agenda import AgendaNode, iter_nodes
from .pv_document import AGENDA_LABEL, COLOR_H1, COLOR_H2, COLOR_H3, COLOR_TITLE, Block, parse_fragment

log = logging.getLogger("pvwriter.docx")

FONT_BODY = "Arial"
FONT_TITLE = "Montserrat"  # falls back to the default font when not installed
_HEADING_STYLE = {1: ("Heading 1", COLOR_H1, 20), 2: ("Heading 2", COLOR_H2, 16), 3: ("Heading 3", COLOR_H3, 14)}
_LINK_COLOR = "1155CC"


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#").upper())


def _set_font(style, name: str, size: int | None = None, color: str | None = None, bold=None, italic=None):
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


def _configure_styles(doc):
    """Apply the reference look: Arial body, coloured non-bold headings."""
    styles = doc.styles
    normal = styles["Normal"]
    _set_font(normal, FONT_BODY, 11)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.15

    title = styles["Title"]
    _set_font(title, FONT_TITLE, 24, COLOR_TITLE, bold=False)
    _remove_borders(title)
    title.paragraph_format.space_after = Pt(12)

    for level, (name, color, size) in _HEADING_STYLE.items():
        style = styles[name]
        _set_font(style, FONT_BODY, size, color, bold=False, italic=False)
        style.paragraph_format.space_before = Pt(16 if level == 1 else 12)
        style.paragraph_format.space_after = Pt(4)

    for name in ("List Bullet", "List Bullet 2", "List Bullet 3", "List Number", "List Number 2", "List Number 3"):
        if name in [s.name for s in styles if s.type == WD_STYLE_TYPE.PARAGRAPH]:
            styles[name].paragraph_format.space_after = Pt(2)


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
    ppr = paragraph._p.get_or_add_pPr()  # pylint: disable=protected-access
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "999999")):
        bottom.set(qn(key), value)
    borders.append(bottom)
    ppr.append(borders)


def _list_style(block: Block) -> str:
    """Return the built-in list style for a block's nesting level."""
    level = min(block.list_level, 3)
    base = "List Number" if block.ordered else "List Bullet"
    return base if level == 1 else f"{base} {level}"


def _add_blocks(doc, blocks: list[Block]):
    """Append parsed fragment blocks to the document."""
    for block in blocks:
        style = _list_style(block) if block.list_level else None
        paragraph = doc.add_paragraph(style=style)
        for run in block.runs:
            if run.text == "\n":
                paragraph.add_run().add_break()
                continue
            r = paragraph.add_run(run.text)
            r.bold = run.bold or None
            r.italic = run.italic or None
            r.underline = run.underline or None


def _add_toc(doc, nodes: list[AgendaNode]):
    """Append the agenda as linked bullets, nested like the agenda tree."""
    for node in nodes:
        style = "List Bullet" if node.level == 1 else f"List Bullet {node.level}"
        paragraph = doc.add_paragraph(style=style)
        _add_internal_link(paragraph, node.toc_label, node.anchor)
        _add_toc(doc, node.children)


def export_docx(path: str, title: str, roots: list[AgendaNode], bodies: dict[str, str]) -> str:
    """Write the PV as a .docx file.

    Args:
        path: Destination file.
        title: Document title (``PV … du JJ/MM/AAAA``).
        roots: Agenda tree.
        bodies: HTML fragment per agenda id (missing or empty bodies leave the heading alone).

    Returns:
        The path written.
    """
    doc = Document()
    _configure_styles(doc)
    doc.core_properties.title = title
    doc.core_properties.language = "fr-BE"

    doc.add_paragraph(title, style="Title")
    doc.add_paragraph(AGENDA_LABEL, style="Heading 1")
    _add_toc(doc, roots)
    _add_separator(doc)

    for bookmark_id, node in enumerate(iter_nodes(roots), start=1):
        heading = doc.add_paragraph(node.heading, style=_HEADING_STYLE[node.level][0])
        _add_bookmark(heading, node.anchor, bookmark_id)
        _add_blocks(doc, parse_fragment(bodies.get(node.node_id, "")))

    doc.save(path)
    log.info("PV exported to %s", path)
    return path
