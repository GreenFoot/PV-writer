"""Stage C: assemble the PV deterministically from the agenda tree and the section bodies.

The LLM only writes section bodies (small HTML fragments). The title, the linked agenda,
the separator and the numbered headings are produced here, so they always follow the
conventions of the reference PVs. The same model feeds the in-app preview (HTML) and the
Word exporter (via ``parse_fragment``).
"""

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .agenda import AgendaNode, iter_nodes

# Google Docs default palette, as seen in the reference PVs
COLOR_TITLE = "#1155CC"
COLOR_H1 = "#3D85C6"
COLOR_H2 = "#76A5AF"
COLOR_H3 = "#B45F06"
HEADING_COLORS = {1: COLOR_H1, 2: COLOR_H2, 3: COLOR_H3}
AGENDA_LABEL = "Agenda :"
SUMMARY_LABEL = "Synthèse :"
SUMMARY_POINTS_LABEL = "Points principaux"
SUMMARY_ACTIONS_LABEL = "Actions et suivis"
SUMMARY_COLUMNS = ("Action", "Qui", "Échéance")
EMPTY_CELL = "–"
IMAGE_DIR = "images"  # sub-folder of the meeting holding the PV images
IMAGE_MAX_WIDTH = 620  # px (96 dpi): the width of the A4 text column


@dataclass
class Run:
    """A span of text with inline formatting. ``text == "\\n"`` is a line break."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


@dataclass
class Block:
    """A paragraph, possibly a list item.

    Attributes:
        runs: Inline content.
        list_level: 0 for a plain paragraph, 1..n for list nesting depth.
        ordered: True for a numbered list item.
        list_id: Index of the top-level list the item belongs to (0 outside lists).
        kind: Role set by ``classify_blocks``: ``text``, ``subtopic`` (a lone bold bullet
            that opens a topic), ``label`` (a bold paragraph ending with ":") or ``action``
            (a follow-up line starting with "->"). ``image`` is set by the parser.
        image: Path of the picture relative to the meeting folder (``image`` blocks).
        width: Display width of the picture in px at 96 dpi (0: natural size).
    """

    runs: list[Run] = field(default_factory=list)
    list_level: int = 0
    ordered: bool = False
    list_id: int = 0
    kind: str = "text"
    image: str = ""
    width: int = 0

    @property
    def text(self) -> str:
        """Plain text of the block."""
        return "".join(r.text for r in self.runs)


class _FragmentParser(HTMLParser):
    """Parse the small HTML subset produced by stage B into blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._lists: list[str] = []  # stack of "ul"/"ol"
        self._list_count = 0
        self._current: Block | None = None
        self._bold = 0
        self._italic = 0
        self._underline = 0

    # Block handling

    def _open_block(self, list_item: bool = False):
        self._close_block()
        if list_item and self._lists:
            self._current = Block(
                list_level=len(self._lists), ordered=self._lists[-1] == "ol", list_id=self._list_count
            )
        else:
            self._current = Block()

    def _close_block(self):
        if self._current is not None:
            # Trim the whitespace collapsed at the edges of the paragraph
            runs = self._current.runs
            while runs and runs[0].text != "\n" and not runs[0].text.strip():
                runs.pop(0)
            while runs and runs[-1].text != "\n" and not runs[-1].text.strip():
                runs.pop()
            if runs:
                runs[0].text = runs[0].text.lstrip() if runs[0].text != "\n" else runs[0].text
                runs[-1].text = runs[-1].text.rstrip() if runs[-1].text != "\n" else runs[-1].text
            if any(r.text.strip() for r in runs):
                self.blocks.append(self._current)
        self._current = None

    # HTMLParser hooks

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("ul", "ol"):
            self._close_block()
            if not self._lists:
                self._list_count += 1
            self._lists.append(tag)
        elif tag == "li":
            self._open_block(list_item=True)
        elif tag in ("p", "div"):
            # A <p> inside an <li> continues the list item
            if self._current is None or self._current.list_level == 0:
                self._open_block()
        elif tag in ("strong", "b"):
            self._bold += 1
        elif tag in ("em", "i"):
            self._italic += 1
        elif tag == "u":
            self._underline += 1
        elif tag == "br":
            self._ensure_block()
            self._current.runs.append(Run("\n"))
        elif tag == "img":
            # A picture is always a block of its own
            values = dict(attrs)
            if values.get("src"):
                self._close_block()
                width = values.get("width") or ""
                width = int(width) if width.isdigit() else 0
                self.blocks.append(Block(kind="image", image=values["src"], width=width))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("ul", "ol"):
            self._close_block()
            if self._lists:
                self._lists.pop()
        elif tag == "li":
            self._close_block()
        elif tag in ("p", "div"):
            if self._current is not None and self._current.list_level == 0:
                self._close_block()
        elif tag in ("strong", "b"):
            self._bold = max(0, self._bold - 1)
        elif tag in ("em", "i"):
            self._italic = max(0, self._italic - 1)
        elif tag == "u":
            self._underline = max(0, self._underline - 1)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data)
        if not text.strip() and self._current is None:
            return
        self._ensure_block()
        self._current.runs.append(Run(text, self._bold > 0, self._italic > 0, self._underline > 0))

    def _ensure_block(self):
        if self._current is None:
            self._open_block(list_item=bool(self._lists))

    def close(self):
        super().close()
        self._close_block()


def parse_fragment(fragment: str) -> list[Block]:
    """Parse a stage-B HTML fragment into paragraphs and list items.

    Args:
        fragment: HTML using ``p, br, ul, ol, li, strong/b, em/i, u, img``.

    Returns:
        Blocks in document order; unknown tags are ignored but their text is kept.
    """
    parser = _FragmentParser()
    parser.feed(fragment or "")
    parser.close()
    return parser.blocks


_ACTION_PREFIXES = ("->", "→")


def image_html(src: str, width: int = 0) -> str:
    """Return the body markup of a picture (its own paragraph)."""
    size = f' width="{min(width, IMAGE_MAX_WIDTH)}"' if width else ""
    return f'<p><img src="{html.escape(src)}"{size}></p>'


def body_images(fragment: str) -> list[str]:
    """Return the ``<img>`` paragraphs of a body, to carry them over a regeneration."""
    return [image_html(b.image, b.width) for b in parse_fragment(fragment) if b.kind == "image"]


def _all_bold(block: Block) -> bool:
    return all(r.bold for r in block.runs if r.text.strip())


def _split_actions(block: Block) -> list[Block]:
    """Split a paragraph at line breaks that are followed by a "->" follow-up line."""
    parts: list[Block] = [Block()]
    runs = block.runs
    for i, run in enumerate(runs):
        nxt = runs[i + 1].text.lstrip() if i + 1 < len(runs) else ""
        if run.text == "\n" and nxt.startswith(_ACTION_PREFIXES) and parts[-1].runs:
            parts.append(Block())
            continue
        parts[-1].runs.append(run)
    return [p for p in parts if p.runs]


def classify_blocks(blocks: list[Block]) -> list[Block]:
    """Tag the blocks with their role in the PV so the exporter can style them.

    Args:
        blocks: Output of ``parse_fragment``.

    Returns:
        New list of blocks: paragraphs holding a "->" line after a ``<br>`` are split, and
        every block has its ``kind`` set.
    """
    items_per_list: dict[int, int] = {}
    for block in blocks:
        if block.list_level:
            items_per_list[block.list_id] = items_per_list.get(block.list_id, 0) + 1

    result: list[Block] = []
    for block in blocks:
        if block.kind == "image":
            result.append(block)
            continue
        if block.list_level:
            if block.list_level == 1 and items_per_list[block.list_id] == 1 and _all_bold(block):
                block.kind = "subtopic"
            result.append(block)
            continue
        for part in _split_actions(block):
            text = part.text.strip()
            if text.startswith(_ACTION_PREFIXES):
                part.kind = "action"
            elif text.endswith(":") and _all_bold(part) and "\n" not in text:
                part.kind = "label"
            result.append(part)
    return result


def render_preview_html(
    title: str, roots: list[AgendaNode], bodies: dict[str, str], summary: dict | None = None
) -> str:
    """Render the whole PV as HTML for the in-app preview.

    Args:
        title: Document title (``PV … du JJ/MM/AAAA``).
        roots: Agenda tree.
        bodies: Section HTML per agenda id.
        summary: Stage-D synthesis ``{"points": [...], "actions": [...]}``, or None.

    Returns:
        A full HTML document that QTextEdit can display.
    """
    out = [
        "<html><body style='font-family: Arial, sans-serif; font-size: 11pt;'>",
        f"<h1 style='color:{COLOR_TITLE}; font-size: 24pt; font-weight: normal;'>{html.escape(title)}</h1>",
        f"<h2 style='color:{COLOR_H1}; font-size: 18pt; font-weight: normal;'>{AGENDA_LABEL}</h2>",
        _render_toc(roots),
        "<hr/>",
        _render_summary(summary),
    ]
    sizes = {1: "18pt", 2: "14pt", 3: "13pt"}
    for node in iter_nodes(roots):
        out.append(
            f"<h{node.level + 1} style='color:{HEADING_COLORS[node.level]}; font-size:{sizes[node.level]};"
            f" font-weight: normal;'><a name='{node.anchor}'></a>{html.escape(node.heading)}</h{node.level + 1}>"
        )
        body = bodies.get(node.node_id, "")
        if body:
            out.append(body)
    out.append("</body></html>")
    return "\n".join(out)


def _render_summary(summary: dict | None) -> str:
    """Render the synthesis (main points, then the action table) as preview HTML."""
    points = (summary or {}).get("points", [])
    actions = (summary or {}).get("actions", [])
    if not points and not actions:
        return ""
    esc = html.escape
    out = [f"<h2 style='color:{COLOR_H1}; font-size: 18pt; font-weight: normal;'>{SUMMARY_LABEL}</h2>"]
    if points:
        out.append(f"<p><b>{SUMMARY_POINTS_LABEL}</b></p><ul>")
        for point in points:
            lead = f"<b>{esc(point['sujet'])} :</b> " if point.get("sujet") else ""
            out.append(f"<li>{lead}{esc(point['texte'])}</li>")
        out.append("</ul>")
    if actions:
        out.append(f"<p><b>{SUMMARY_ACTIONS_LABEL}</b></p>")
        out.append("<table border='1' cellspacing='0' cellpadding='4' width='100%' style='border-color:#BBBBBB;'>")
        out.append("<tr>" + "".join(f"<th align='left'>{c}</th>" for c in SUMMARY_COLUMNS) + "</tr>")
        for action in actions:
            cells = (action["action"], action.get("qui") or EMPTY_CELL, action.get("echeance") or EMPTY_CELL)
            out.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
        out.append("</table>")
    out.append("<hr/>")
    return "\n".join(out)


def _render_toc(nodes: list[AgendaNode]) -> str:
    """Render the agenda as nested lists with internal links."""
    if not nodes:
        return ""
    items = []
    for node in nodes:
        link = f"<a href='#{node.anchor}'>{html.escape(node.toc_label)}</a>"
        items.append(f"<li>{link}{_render_toc(node.children)}</li>")
    return "<ul>" + "".join(items) + "</ul>"
