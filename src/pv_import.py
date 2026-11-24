"""Read a finished PV back into section bodies and a synthesis.

Two sources feed the same converter:

- a Word document exported by ``docx_exporter`` and then corrected by hand;
- the editable "Procès-verbal" tab (a ``QTextDocument`` built from the preview HTML).

Both are first flattened into ``Para`` records. ``records_to_pv`` then splits them at the
headings, matched to the agenda, and rebuilds the stage-B HTML fragments (``p, br, ul, ol,
li, strong, em, u, img``) plus the ``{"points", "actions"}`` synthesis. The title and the
agenda come from the meeting information, so edits to them are ignored. Pictures are stored
in the ``images`` sub-folder of the meeting, named after their content so a re-import does
not duplicate them.
"""

import hashlib
import html
import os
import re
from dataclasses import dataclass, field

from .agenda import AgendaNode, iter_nodes, normalize_title
from .pv_document import (
    AGENDA_LABEL,
    EMPTY_CELL,
    IMAGE_DIR,
    SUMMARY_COLUMNS,
    SUMMARY_LABEL,
    Run,
    image_html,
)

# Paragraph styles written by docx_exporter (kept as literals to avoid importing python-docx here)
_DOCX_SUBTOPIC = "PV Sujet"
_DOCX_LABEL = "PV Intitulé"
_DOCX_ACTION = "PV Suite"
_ARROW = re.compile(r"^\s*(?:->|→)\s*")


@dataclass
class Para:
    """One paragraph (or table) of a PV, independent of its source.

    Attributes:
        kind: ``title``, ``heading``, ``subtopic``, ``label``, ``action``, ``item``, ``text``,
            ``table`` or ``image``.
        runs: Inline content.
        level: Heading level (1..3) or list nesting depth (1..n).
        ordered: True for a numbered list item.
        list_key: Identifies the list an item belongs to, so two adjacent lists stay apart.
        rows: Cell texts of a table.
        image: Picture path relative to the meeting folder.
        width: Picture display width in px (96 dpi), 0 for the natural size.
    """

    kind: str
    runs: list[Run] = field(default_factory=list)
    level: int = 0
    ordered: bool = False
    list_key: object = None
    rows: list[list[str]] = field(default_factory=list)
    image: str = ""
    width: int = 0

    @property
    def text(self) -> str:
        """Plain text of the paragraph."""
        return "".join(r.text for r in self.runs)


def store_image(folder: str, data: bytes, ext: str) -> str:
    """Save picture bytes in the meeting's ``images`` folder, named after their content.

    Args:
        folder: Meeting folder.
        data: Image file content.
        ext: Extension with or without the dot (``png``, ``.jpg``…).

    Returns:
        The path relative to the meeting folder, with "/" separators (as used in ``src``).
    """
    ext = "." + ext.lower().lstrip(".") if ext else ".png"
    name = f"img_{hashlib.sha1(data).hexdigest()[:12]}{'.jpg' if ext == '.jpeg' else ext}"
    os.makedirs(os.path.join(folder, IMAGE_DIR), exist_ok=True)
    path = os.path.join(folder, IMAGE_DIR, name)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return f"{IMAGE_DIR}/{name}"


# ── Records → bodies and synthesis ───────────────────────────


def _runs_html(runs: list[Run]) -> str:
    """Render inline runs as HTML, merging neighbours with the same formatting."""
    merged: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        last = merged[-1] if merged else None
        same = last and (last.bold, last.italic, last.underline) == (run.bold, run.italic, run.underline)
        if same and "\n" not in (last.text, run.text):
            last.text += run.text
        else:
            merged.append(Run(run.text, run.bold, run.italic, run.underline))
    # Trim the paragraph edges
    while merged and merged[0].text != "\n" and not merged[0].text.strip():
        merged.pop(0)
    while merged and (merged[-1].text == "\n" or not merged[-1].text.strip()):
        merged.pop()
    if merged:
        merged[0].text = merged[0].text.lstrip()
        merged[-1].text = merged[-1].text.rstrip()

    out = []
    for run in merged:
        if run.text == "\n":
            out.append("<br>")
            continue
        text = html.escape(run.text, quote=False)
        if run.underline:
            text = f"<u>{text}</u>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.bold:
            text = f"<strong>{text}</strong>"
        out.append(text)
    return "".join(out)


def _strip_arrow(runs: list[Run]) -> list[Run]:
    """Drop a leading "->" / "→" (and the bold of the arrow run) from a follow-up line."""
    runs = [Run(r.text, r.bold, r.italic, r.underline) for r in runs]
    while runs and runs[0].text != "\n" and not runs[0].text.strip():
        runs.pop(0)
    if runs and _ARROW.match(runs[0].text):
        runs[0].text = _ARROW.sub("", runs[0].text)
        if not runs[0].text:
            runs.pop(0)
    return runs


def _list_html(items: list[Para]) -> str:
    """Render consecutive list items as nested ``ul``/``ol`` lists."""
    out: list[str] = []
    stack: list[str] = []
    for item in items:
        level = max(1, item.level)
        while len(stack) > level:
            out.append(f"</li></{stack.pop()}>")
        if len(stack) == level:
            out.append("</li>")
        while len(stack) < level:
            tag = "ol" if item.ordered else "ul"
            out.append(f"<{tag}>")
            stack.append(tag)
        out.append(f"<li>{_runs_html(item.runs)}")
    while stack:
        out.append(f"</li></{stack.pop()}>")
    return "".join(out)


def _body_html(paras: list[Para]) -> str:
    """Rebuild a stage-B fragment from the paragraphs of one section."""
    out: list[str] = []
    pending: list[Para] = []

    def flush():
        if pending:
            out.append(_list_html(pending))
            pending.clear()

    for para in paras:
        if para.kind == "item":
            if not para.text.strip():
                continue
            # A new top-level list starts a new <ul>, so a lone bold bullet stays a topic
            if pending and para.level <= 1 and para.list_key != pending[0].list_key:
                flush()
            pending.append(para)
            continue
        flush()
        if para.kind == "image":
            out.append(image_html(para.image, para.width))
            continue
        if para.kind == "table":
            for row in para.rows:
                cells = [c.strip() for c in row if c.strip()]
                if cells:
                    out.append(f"<p>{html.escape(' – '.join(cells), quote=False)}</p>")
            continue
        if not para.text.strip():
            continue
        if para.kind == "subtopic":
            out.append(f"<ul><li><strong>{html.escape(para.text.strip(), quote=False)}</strong></li></ul>")
        elif para.kind == "label":
            out.append(f"<p><strong>{html.escape(para.text.strip(), quote=False)}</strong></p>")
        elif para.kind == "action":
            out.append(f"<p>-&gt; {_runs_html(_strip_arrow(para.runs))}</p>")
        else:
            out.append(f"<p>{_runs_html(para.runs)}</p>")
    flush()
    return "\n".join(out)


def _summary_point(item: Para) -> dict:
    """Split a synthesis bullet into its bold subject and its text."""
    runs = [r for r in item.runs if r.text != "\n"]
    lead = 0
    while lead < len(runs) and (runs[lead].bold or not runs[lead].text.strip()):
        lead += 1
    subject = "".join(r.text for r in runs[:lead]).strip()
    text = "".join(r.text for r in runs[lead:]).strip()
    if subject.endswith(":"):
        subject = subject[:-1].strip()
    elif ":" in subject:
        # The bold may run past the colon: "Sujet : début du texte"
        subject, _, rest = subject.partition(":")
        subject, text = subject.strip(), f"{rest.strip()} {text}".strip()
    if not text:
        subject, text = "", subject
    return {"sujet": subject, "texte": text}


def _summary_actions(rows: list[list[str]]) -> list[dict]:
    """Read the action table (header row skipped)."""
    actions = []
    for row in rows:
        cells = [c.strip() for c in row] + ["", "", ""]
        if tuple(cells[:3]) == SUMMARY_COLUMNS or not cells[0]:
            continue
        actions.append(
            {
                "action": cells[0],
                "qui": "" if cells[1] == EMPTY_CELL else cells[1],
                "echeance": "" if cells[2] == EMPTY_CELL else cells[2],
            }
        )
    return actions


def _same_heading(text: str, node: AgendaNode) -> bool:
    wanted = normalize_title(text)
    return bool(wanted) and wanted in (normalize_title(node.heading), normalize_title(node.title))


def records_to_pv(paras: list[Para], roots: list[AgendaNode]) -> tuple[dict[str, str], dict, list[str]]:
    """Split a flattened PV into section bodies and the synthesis.

    Headings are matched to the agenda by their text, or else by their position among the
    remaining items of the same level. An extra heading that matches nothing becomes a topic
    of the current section.

    Args:
        paras: Records from ``docx_records`` or ``qt_records``.
        roots: Agenda tree of the meeting.

    Returns:
        ``(bodies, summary, warnings)``: HTML fragment per agenda id (every id present),
        ``{"points", "actions"}`` (empty when the PV has no synthesis), and French
        messages for headings that could not be placed.
    """
    nodes = list(iter_nodes(roots))
    sections: dict[str, list[Para]] = {node.node_id: [] for node in nodes}
    points: list[dict] = []
    actions: list[dict] = []
    warnings: list[str] = []
    current: str | None = None  # node id, "summary", or None (title, agenda)
    position = 0  # index of the next unmatched agenda node

    for para in paras:
        if para.kind == "title":
            continue
        if para.kind == "heading":
            text = para.text.strip()
            if para.level == 1 and normalize_title(text) == normalize_title(AGENDA_LABEL):
                current = None
                continue
            if para.level == 1 and normalize_title(text) == normalize_title(SUMMARY_LABEL):
                current = "summary"
                continue
            remaining = nodes[position:]
            match = next((n for n in remaining if _same_heading(text, n)), None)
            if match is None and not any(_same_heading(text, n) for n in nodes):
                # A renamed heading: take the next item of the same level
                match = next((n for n in remaining if n.level == para.level), None)
            if match is None:
                warnings.append(f"Titre « {text} » sans point d'agenda correspondant : gardé comme sujet.")
                if current not in (None, "summary"):
                    sections[current].append(Para("subtopic", [Run(text)]))
                continue
            current = match.node_id
            position = nodes.index(match) + 1
            continue

        if current is None:
            continue
        if current == "summary":
            if para.kind == "item" and para.level <= 1 and para.text.strip():
                points.append(_summary_point(para))
            elif para.kind == "table":
                actions.extend(_summary_actions(para.rows))
            continue
        sections[current].append(para)

    bodies = {node_id: _body_html(section) for node_id, section in sections.items()}
    summary = {"points": points, "actions": actions} if points or actions else {}
    return bodies, summary, warnings


# ── Word document → records ─────────────────────────────────

_HEADING_STYLE = re.compile(r"^(?:Heading|Titre)\s*(\d)$", re.IGNORECASE)
_LIST_STYLE = re.compile(r"^List (Bullet|Number)(?:\s+(\d))?$")


def _docx_runs(p_element) -> list[Run]:
    """Read the visible runs of a ``w:p``, hyperlinks and tracked insertions included."""
    from docx.oxml.ns import qn  # pylint: disable=import-outside-toplevel

    runs: list[Run] = []
    for r in p_element.iter(qn("w:r")):
        # Deleted text (tracked changes) uses w:delText and is skipped below
        rpr = r.find(qn("w:rPr"))

        def _on(tag, rpr=rpr):
            if rpr is None:
                return False
            el = rpr.find(qn(tag))
            return el is not None and el.get(qn("w:val"), "true") not in ("0", "false", "none")

        bold, italic, underline = _on("w:b"), _on("w:i"), _on("w:u")
        for child in r:
            if child.tag == qn("w:t"):
                runs.append(Run(child.text or "", bold, italic, underline))
            elif child.tag == qn("w:tab"):
                runs.append(Run(" ", bold, italic, underline))
            elif child.tag == qn("w:br") and child.get(qn("w:type")) in (None, "textWrapping"):
                runs.append(Run("\n"))
    return runs


def _docx_images(doc, p_element, folder: str) -> list[Para]:
    """Save the pictures of a ``w:p`` and return them as image records."""
    from docx.oxml.ns import qn  # pylint: disable=import-outside-toplevel

    images = []
    for drawing in p_element.iter(qn("w:drawing")):
        blip = next(drawing.iter(qn("a:blip")), None)
        rel_id = blip.get(qn("r:embed")) if blip is not None else None
        part = doc.part.related_parts.get(rel_id) if rel_id else None
        if part is None or not folder:
            continue
        extent = next(drawing.iter(qn("wp:extent")), None)
        width = round(int(extent.get("cx", "0")) / 9525) if extent is not None else 0  # EMU → px
        ext = os.path.splitext(str(part.partname))[1]
        images.append(Para("image", image=store_image(folder, part.blob, ext), width=width))
    return images


def docx_records(path: str, folder: str = "") -> list[Para]:
    """Flatten a Word PV into records, following the styles written by ``docx_exporter``.

    Args:
        path: The .docx file.
        folder: Meeting folder where the pictures are saved; "" drops them.

    Returns:
        Records in document order; the pictures of a paragraph follow its text.
    """
    from docx import Document  # pylint: disable=import-outside-toplevel
    from docx.oxml.ns import qn  # pylint: disable=import-outside-toplevel
    from docx.table import Table  # pylint: disable=import-outside-toplevel
    from docx.text.paragraph import Paragraph  # pylint: disable=import-outside-toplevel

    doc = Document(path)
    records: list[Para] = []
    list_key = 0
    for element in doc.element.body.iterchildren():
        if element.tag == qn("w:tbl"):
            table = Table(element, doc)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            records.append(Para("table", rows=rows))
            continue
        if element.tag != qn("w:p"):
            continue
        paragraph = Paragraph(element, doc)
        style = paragraph.style.name if paragraph.style is not None else ""
        runs = _docx_runs(element)
        images = _docx_images(doc, element, folder)
        if images and not "".join(r.text for r in runs).strip():
            records.extend(images)
            continue
        heading = _HEADING_STYLE.match(style)
        listed = _LIST_STYLE.match(style)
        ppr = element.find(qn("w:pPr"))
        numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
        if style == "Title":
            records.append(Para("title", runs))
        elif heading:
            records.append(Para("heading", runs, level=min(3, int(heading.group(1)))))
        elif style == _DOCX_SUBTOPIC:
            records.append(Para("subtopic", runs))
        elif style == _DOCX_LABEL:
            records.append(Para("label", runs))
        elif style == _DOCX_ACTION:
            records.append(Para("action", runs))
        elif listed or numpr is not None:
            if listed:
                level, ordered = int(listed.group(2) or 1), listed.group(1) == "Number"
            else:
                ilvl = numpr.find(qn("w:ilvl"))
                level, ordered = int(ilvl.get(qn("w:val"), "0")) + 1 if ilvl is not None else 1, False
            if not records or records[-1].kind != "item":
                list_key += 1
            records.append(Para("item", runs, level=level, ordered=ordered, list_key=list_key))
        elif _ARROW.match("".join(r.text for r in runs)):
            records.append(Para("action", runs))
        else:
            records.append(Para("text", runs))
        records.extend(images)
    return records


def import_docx(path: str, roots: list[AgendaNode], folder: str = "") -> tuple[dict[str, str], dict, list[str]]:
    """Read a corrected Word PV back into ``(bodies, summary, warnings)``.

    Args:
        path: The .docx file.
        roots: Agenda tree of the meeting.
        folder: Meeting folder where the pictures are saved; "" drops them.
    """
    return records_to_pv(docx_records(path, folder), roots)


# ── QTextDocument (editable tab) → records ──────────────────


def qt_records(document, folder: str = "") -> list[Para]:
    """Flatten the editable PV tab into records.

    The tab shows ``render_preview_html``: the title is ``<h1>``, the agenda, the synthesis
    and the level-1 items are ``<h2>``, levels 2 and 3 ``<h3>`` and ``<h4>``. A picture
    becomes a record of its own, between the text before and after it.

    Args:
        document: The ``QTextDocument`` of the tab.
        folder: Meeting folder, to turn absolute picture paths into relative ones.

    Returns:
        Records in document order.
    """
    from PySide6.QtGui import QFont, QTextCursor, QTextListFormat  # pylint: disable=import-outside-toplevel

    ordered_styles = {
        QTextListFormat.Style.ListDecimal,
        QTextListFormat.Style.ListLowerAlpha,
        QTextListFormat.Style.ListUpperAlpha,
        QTextListFormat.Style.ListLowerRoman,
        QTextListFormat.Style.ListUpperRoman,
    }
    records: list[Para] = []
    seen_tables: set[int] = set()
    top_list = None
    block = document.begin()
    while block.isValid():
        table = QTextCursor(block).currentTable()
        if table is not None:
            key = table.firstPosition()
            if key not in seen_tables:
                seen_tables.add(key)
                rows = [[_cell_text(table, r, c) for c in range(table.columns())] for r in range(table.rows())]
                records.append(Para("table", rows=rows))
            block = block.next()
            continue

        runs: list[Run] = []
        segments: list = []  # runs lists and image records, in order
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                image = fragment.charFormat().toImageFormat()
                segments.append(runs)
                segments.append(Para("image", image=_relative_image(image.name(), folder), width=int(image.width())))
                runs = []
            elif fragment.isValid():
                fmt = fragment.charFormat()
                bold = fmt.fontWeight() >= QFont.Weight.DemiBold.value
                for i, part in enumerate(fragment.text().split("\u2028")):
                    if i:
                        runs.append(Run("\n"))
                    if part:
                        runs.append(Run(part, bold, fmt.fontItalic(), fmt.fontUnderline()))
            it += 1
        segments.append(runs)

        heading = block.blockFormat().headingLevel()
        text_list = block.textList()
        for segment in segments:
            if isinstance(segment, Para):
                records.append(segment)
            elif heading == 1:
                records.append(Para("title", segment))
            elif heading >= 2:
                records.append(Para("heading", segment, level=min(3, heading - 1)))
            elif text_list is not None:
                fmt = text_list.format()
                level = max(1, fmt.indent())
                if level == 1:
                    top_list = text_list.objectIndex()
                ordered = fmt.style() in ordered_styles
                records.append(Para("item", segment, level=level, ordered=ordered, list_key=top_list))
            elif segment or len(segments) == 1:
                records.append(Para("text", segment))
        block = block.next()
    return records


def _relative_image(name: str, folder: str) -> str:
    """Return a picture path relative to the meeting folder, with "/" separators."""
    if name.startswith("file:"):
        from PySide6.QtCore import QUrl  # pylint: disable=import-outside-toplevel

        name = QUrl(name).toLocalFile()
    if folder and os.path.isabs(name):
        name = os.path.relpath(name, folder)
    return name.replace(os.sep, "/")


def _cell_text(table, row: int, column: int) -> str:
    """Return the plain text of a ``QTextTable`` cell (several paragraphs joined by spaces)."""
    cell = table.cellAt(row, column)
    parts = []
    block = cell.firstCursorPosition().block()
    end = cell.lastCursorPosition().position()
    while block.isValid() and block.position() <= end:
        parts.append(block.text())
        block = block.next()
    return " ".join(p.strip() for p in parts if p.strip())
