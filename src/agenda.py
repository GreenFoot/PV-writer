"""Agenda tree: parsing from an indented list, numbering and heading labels.

The agenda drives the whole PV, following the reference documents:

- level 1 is a thematic group, rendered as ``<title> :`` (Heading 1);
- level 2 is a numbered item, rendered as ``1) <title>`` (Heading 2);
- level 3 is a lettered sub-item, rendered as ``a) <title>`` (Heading 3).
"""

import re
import string
import unicodedata
from dataclasses import dataclass, field

OTHER_INFO_TITLE = "Autres informations"
_BULLET_PREFIX = re.compile(r"^(?:(?:[-*•●○■◦▪❖]|\d+[).]|[a-zA-Z][).])\s+)+")
_MAX_LEVEL = 3


@dataclass
class AgendaNode:
    """One agenda entry.

    Attributes:
        node_id: Stable dotted id, e.g. ``"2.1"``.
        title: Title as typed by the user, without numbering.
        level: 1 (group), 2 (numbered item) or 3 (lettered sub-item).
        index: 1-based position among its siblings.
        children: Sub-items.
    """

    node_id: str
    title: str
    level: int
    index: int
    children: list["AgendaNode"] = field(default_factory=list)

    @property
    def heading(self) -> str:
        """Return the heading text shown in the PV."""
        if self.level == 1:
            return self.title if self.title.rstrip().endswith(":") else f"{self.title} :"
        if self.level == 2:
            return f"{self.index}) {self.title}"
        return f"{_letter(self.index)}) {self.title}"

    @property
    def toc_label(self) -> str:
        """Return the label shown in the agenda list (like the reference PVs)."""
        if self.level == 3:
            return f"{_letter(self.index)}) {self.title}"
        return self.title.rstrip(" :")

    @property
    def anchor(self) -> str:
        """Return a bookmark/anchor name for internal links."""
        return "pt_" + self.node_id.replace(".", "_")


def _letter(index: int) -> str:
    """Return a, b, …, z, aa, ab… for a 1-based index."""
    letters = string.ascii_lowercase
    result = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        result = letters[rem] + result
    return result


def _indent_width(line: str) -> int:
    """Return the visual indentation of a line (tab = 4 spaces)."""
    expanded = line.replace("\t", "    ")
    return len(expanded) - len(expanded.lstrip(" "))


def normalize_title(text: str) -> str:
    """Normalize a title for fuzzy matching (accents, case, numbering, punctuation)."""
    text = _BULLET_PREFIX.sub("", text.strip())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def parse_agenda(text: str, add_other_info: bool = True) -> list[AgendaNode]:
    """Parse an indented list into an agenda tree.

    Indentation (spaces or tabs) gives the level; bullets and ``1)`` / ``a)`` prefixes
    are ignored. Deeper indentation than three levels is clamped to level 3.

    Args:
        text: One agenda entry per line.
        add_other_info: Append "Autres informations" when it is not already there.

    Returns:
        The level-1 nodes, each with its children.
    """
    entries = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        title = _BULLET_PREFIX.sub("", raw.strip()).strip()
        if title:
            entries.append((_indent_width(raw), title))

    roots: list[AgendaNode] = []
    stack: list[tuple[int, AgendaNode]] = []  # (indent, node)
    for indent, title in entries:
        while stack and stack[-1][0] >= indent:
            stack.pop()
        level = min(len(stack) + 1, _MAX_LEVEL)
        if level == _MAX_LEVEL and len(stack) >= _MAX_LEVEL:
            # Too deep: attach to the deepest allowed parent
            stack = stack[: _MAX_LEVEL - 1]
        siblings = stack[-1][1].children if stack else roots
        node_id = f"{stack[-1][1].node_id}.{len(siblings) + 1}" if stack else str(len(siblings) + 1)
        node = AgendaNode(node_id=node_id, title=title, level=level, index=len(siblings) + 1)
        siblings.append(node)
        stack.append((indent, node))

    if add_other_info and not any(normalize_title(n.title) == normalize_title(OTHER_INFO_TITLE) for n in roots):
        roots.append(AgendaNode(node_id=str(len(roots) + 1), title=OTHER_INFO_TITLE, level=1, index=len(roots) + 1))
    return roots


def iter_nodes(roots: list[AgendaNode]):
    """Yield every node depth-first, in document order."""
    for node in roots:
        yield node
        yield from iter_nodes(node.children)


def find_node(roots: list[AgendaNode], node_id: str) -> AgendaNode | None:
    """Return the node with the given id, or None."""
    return next((n for n in iter_nodes(roots) if n.node_id == node_id), None)


def node_path(roots: list[AgendaNode], node_id: str) -> list[AgendaNode]:
    """Return the chain of nodes from a root to ``node_id`` (empty if unknown)."""

    def _walk(nodes, trail):
        for node in nodes:
            if node.node_id == node_id:
                return trail + [node]
            found = _walk(node.children, trail + [node])
            if found:
                return found
        return []

    return _walk(roots, [])


def other_info_node(roots: list[AgendaNode]) -> AgendaNode | None:
    """Return the "Autres informations" node, if any."""
    return next((n for n in roots if normalize_title(n.title) == normalize_title(OTHER_INFO_TITLE)), None)


def match_heading(roots: list[AgendaNode], heading_text: str) -> AgendaNode | None:
    """Find the agenda node a notes heading refers to.

    Matches on the normalized title, so ``"1) ARO"``, ``"ARO"`` and ``"aro :"`` all hit.

    Args:
        roots: Agenda tree.
        heading_text: Text of a heading typed in the notes.

    Returns:
        The matching node, or None.
    """
    wanted = normalize_title(heading_text)
    if not wanted:
        return None
    for node in iter_nodes(roots):
        if normalize_title(node.title) == wanted:
            return node
    return None


def agenda_outline(roots: list[AgendaNode]) -> str:
    """Return a compact ``id — heading`` outline used inside prompts."""
    lines = []
    for node in iter_nodes(roots):
        lines.append(f"{'  ' * (node.level - 1)}[{node.node_id}] {node.heading}")
    return "\n".join(lines)
