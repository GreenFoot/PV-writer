"""Notes editor: the secretary's live notes, pre-structured by the agenda and timestamped.

Built on the vendored ``RichTextEditorWidget`` (toolbar, search, folding, 2 s autosave with
``.bak``). While recording, every new paragraph starts with a grey ``[hh:mm:ss]`` stamp so
the notes can be placed in the transcript; F2 inserts a stamp on demand.
"""

import html

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor

from .agenda import AgendaNode, iter_nodes, match_heading
from .notes_merge import TIMESTAMP_RE, format_seconds
from .rich_editor import RichTextEditorWidget

STAMP_COLOR = QColor("#8a8f98")


def skeleton_html(roots: list[AgendaNode]) -> str:
    """Return the initial notes document: one heading per agenda entry."""
    return "".join(_heading_html(node) for node in iter_nodes(roots)) or "<p></p>"


def _heading_html(node: AgendaNode) -> str:
    tag = f"h{node.level}"
    return f"<{tag}>{html.escape(node.heading)}</{tag}>"


class NotesEditor(RichTextEditorWidget):
    """Rich-text notes with agenda headings and automatic timestamps."""

    def __init__(self, file_path: str, parent=None):
        """Initialize the editor.

        Args:
            file_path: ``notes.html`` of the meeting.
            parent: Parent widget.
        """
        super().__init__(
            file_path=file_path,
            default_html="<p></p>",
            editor_object_name="notes_editor",
            foldable_heading_levels={1, 2, 3},
            parent=parent,
        )
        self._time_source = None
        self._auto_stamp = True
        self.editor.installEventFilter(self)

    # ── Timestamps ────────────────────────────────────────

    def set_time_source(self, source):
        """Set a ``callable() -> float | None`` returning the recording time, None when idle."""
        self._time_source = source

    def set_auto_timestamps(self, enabled: bool):
        """Enable or disable the automatic stamp on new paragraphs."""
        self._auto_stamp = enabled

    def eventFilter(self, obj, event):
        """Stamp the paragraph created by Enter while recording."""
        if obj is self.editor and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                was_heading = self._detect_heading_level(self.editor.textCursor().block()) > 0
                QTimer.singleShot(0, lambda: self._after_enter(was_heading))
        return super().eventFilter(obj, event)

    def _current_time(self) -> float | None:
        return self._time_source() if self._time_source else None

    def _after_enter(self, was_heading: bool):
        """Reset the heading style after Enter on a heading, then auto-stamp."""
        cursor = self.editor.textCursor()
        if was_heading:
            self._make_body_block(cursor)
        if self._auto_stamp and cursor.block().text() == "" and self._current_time() is not None:
            self.insert_timestamp()

    def _make_body_block(self, cursor: QTextCursor):
        """Turn the cursor's (new, empty) block into normal body text."""
        block_fmt = QTextBlockFormat()
        block_fmt.setHeadingLevel(0)
        cursor.setBlockFormat(block_fmt)
        char_fmt = QTextCharFormat()
        char_fmt.setFontPointSize(13)
        char_fmt.setFontWeight(QFont.Weight.Normal)
        cursor.setBlockCharFormat(char_fmt)
        cursor.setCharFormat(char_fmt)
        self.editor.setTextCursor(cursor)

    def insert_timestamp(self):
        """Insert ``[hh:mm:ss]`` at the start of the current paragraph (F2)."""
        seconds = self._current_time()
        if seconds is None:
            return
        cursor = self.editor.textCursor()
        block_text = cursor.block().text()
        if TIMESTAMP_RE.match(block_text):
            return  # already stamped
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        stamp_fmt = QTextCharFormat()
        stamp_fmt.setForeground(STAMP_COLOR)
        stamp_fmt.setFontWeight(QFont.Weight.Normal)
        cursor.insertText(f"[{format_seconds(seconds)}] ", stamp_fmt)
        # Typing continues in the normal colour
        normal_fmt = QTextCharFormat()
        normal_fmt.setForeground(self.editor.palette().text().color())
        cursor.setCharFormat(normal_fmt)
        if not block_text:
            self.editor.setTextCursor(cursor)
            self.editor.setCurrentCharFormat(normal_fmt)

    # ── Agenda structure ──────────────────────────────────

    def note_blocks(self) -> list[tuple[int, str]]:
        """Return ``(heading_level, text)`` for every block of the document."""
        blocks = []
        block = self.editor.document().begin()
        while block.isValid():
            blocks.append((self._detect_heading_level(block), block.text()))
            block = block.next()
        return blocks

    def has_body_text(self) -> bool:
        """Return True when the secretary typed anything besides headings."""
        return any(level == 0 and text.strip() for level, text in self.note_blocks())

    def apply_agenda(self, roots: list[AgendaNode]):
        """Lay out the agenda headings in the notes.

        When nothing has been typed yet, the document is rebuilt from the agenda. Otherwise
        only the headings missing from the notes are appended at the end, so nothing the
        secretary wrote is ever moved or lost.
        """
        if not self.has_body_text():
            self.editor.setHtml(skeleton_html(roots))
            self._add_body_blocks()
            self.save()
            return
        present = set()
        for level, text in self.note_blocks():
            if level:
                node = match_heading(roots, text)
                if node:
                    present.add(node.node_id)
        missing = [n for n in iter_nodes(roots) if n.node_id not in present]
        if not missing:
            return
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for i, node in enumerate(missing):
            if i or cursor.block().text().strip():
                cursor.insertBlock()
            cursor.insertText(node.heading)
            self.editor.setTextCursor(cursor)
            self._set_heading(node.level)
        self._add_body_blocks()
        self.save()

    def _add_body_blocks(self):
        """Give every heading an empty body paragraph to type in (Qt drops empty ``<p>``)."""
        block = self.editor.document().begin()
        while block.isValid():
            nxt = block.next()
            if self._detect_heading_level(block) and (not nxt.isValid() or self._detect_heading_level(nxt)):
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                cursor.insertBlock()
                self._make_body_block(cursor)
                nxt = cursor.block().next()
            block = nxt
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.editor.setTextCursor(cursor)
