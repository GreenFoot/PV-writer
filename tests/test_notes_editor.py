"""Offscreen tests of the notes editor's agenda layout and timestamps."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402  pylint: disable=wrong-import-position
from PySide6.QtGui import QTextCursor  # noqa: E402  pylint: disable=wrong-import-position
from PySide6.QtWidgets import QApplication  # noqa: E402  pylint: disable=wrong-import-position

from src.agenda import parse_agenda  # noqa: E402  pylint: disable=wrong-import-position
from src.notes_editor import NotesEditor  # noqa: E402  pylint: disable=wrong-import-position


@pytest.fixture(name="editor")
def fixture_editor(tmp_path):
    """A NotesEditor bound to a temporary notes.html."""
    _app = QApplication.instance() or QApplication([])  # noqa: F841  keep the app alive
    widget = NotesEditor(str(tmp_path / "notes.html"))
    yield widget
    widget.deleteLater()


def test_skeleton_has_a_body_line_under_each_heading(editor):
    """Each agenda heading is followed by an empty paragraph to type in."""
    editor.apply_agenda(parse_agenda("École\n    Rentrée\nAssociation"))
    assert editor.note_blocks() == [
        (1, "École :"),
        (0, ""),
        (2, "1) Rentrée"),
        (0, ""),
        (1, "Association :"),
        (0, ""),
        (1, "Autres informations :"),
        (0, ""),
    ]


def test_new_agenda_point_is_appended_without_touching_notes(editor):
    """Once notes exist, only missing headings are added, as real headings."""
    editor.apply_agenda(parse_agenda("École"))
    cursor = editor.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.movePosition(QTextCursor.MoveOperation.NextBlock)
    cursor.insertText("Ma note")
    editor.apply_agenda(parse_agenda("École\nBudget"))
    blocks = editor.note_blocks()
    assert (0, "Ma note") in blocks
    assert blocks[-2:] == [(1, "Budget :"), (0, "")]


def test_timestamp_only_while_recording(editor):
    """F2 inserts a stamp when a time source returns a value, never twice."""
    editor.insert_timestamp()
    assert editor.note_blocks()[0][1] == ""
    editor.set_time_source(lambda: 65.0)
    editor.insert_timestamp()
    editor.insert_timestamp()
    assert editor.note_blocks()[0][1] == "[00:01:05] "
