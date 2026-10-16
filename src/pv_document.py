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
    """

    runs: list[Run] = field(default_factory=list)
    list_level: int = 0
    ordered: bool = False

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
        self._current: Block | None = None
        self._bold = 0
        self._italic = 0
        self._underline = 0

    # Block handling

    def _open_block(self, list_item: bool = False):
        self._close_block()
        if list_item and self._lists:
            self._current = Block(list_level=len(self._lists), ordered=self._lists[-1] == "ol")
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
        fragment: HTML using ``p, br, ul, ol, li, strong/b, em/i, u``.

    Returns:
        Blocks in document order; unknown tags are ignored but their text is kept.
    """
    parser = _FragmentParser()
    parser.feed(fragment or "")
    parser.close()
    return parser.blocks


def render_preview_html(title: str, roots: list[AgendaNode], bodies: dict[str, str]) -> str:
    """Render the whole PV as HTML for the in-app preview.

    Args:
        title: Document title (``PV … du JJ/MM/AAAA``).
        roots: Agenda tree.
        bodies: Section HTML per agenda id.

    Returns:
        A full HTML document that QTextEdit can display.
    """
    out = [
        "<html><body style='font-family: Arial, sans-serif; font-size: 11pt;'>",
        f"<h1 style='color:{COLOR_TITLE}; font-size: 24pt; font-weight: normal;'>{html.escape(title)}</h1>",
        f"<h2 style='color:{COLOR_H1}; font-size: 18pt; font-weight: normal;'>{AGENDA_LABEL}</h2>",
        _render_toc(roots),
        "<hr/>",
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


def _render_toc(nodes: list[AgendaNode]) -> str:
    """Render the agenda as nested lists with internal links."""
    if not nodes:
        return ""
    items = []
    for node in nodes:
        link = f"<a href='#{node.anchor}'>{html.escape(node.toc_label)}</a>"
        items.append(f"<li>{link}{_render_toc(node.children)}</li>")
    return "<ul>" + "".join(items) + "</ul>"
