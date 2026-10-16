"""Side panel with the meeting metadata: title, date, organization, agenda, glossary, previous PV."""

import os

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr
from .meeting import Meeting


class MeetingInfoPanel(QWidget):
    """Form bound to a ``Meeting``. Emits ``changed`` (debounced) after edits."""

    changed = Signal()
    agenda_changed = Signal()
    previous_pv_requested = Signal()
    previous_pv_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._meeting: Meeting | None = None
        self._loading = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(800)
        self._debounce.timeout.connect(self.changed.emit)
        self._agenda_debounce = QTimer(self)
        self._agenda_debounce.setSingleShot(True)
        self._agenda_debounce.setInterval(1500)
        self._agenda_debounce.timeout.connect(self.agenda_changed.emit)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        heading = QLabel(tr("info.heading"))
        heading.setObjectName("panel_heading")
        layout.addWidget(heading)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("info.title_placeholder"))
        form.addRow(tr("info.title"), self.title_edit)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        form.addRow(tr("info.date"), self.date_edit)

        self.org_edit = QLineEdit()
        self.org_edit.setPlaceholderText(tr("info.organization_placeholder"))
        self.org_edit.setToolTip(tr("info.organization_tooltip"))
        form.addRow(tr("info.organization"), self.org_edit)
        layout.addLayout(form)

        layout.addWidget(self._label(tr("info.agenda")))
        self.agenda_edit = QPlainTextEdit()
        self.agenda_edit.setObjectName("agenda_edit")
        self.agenda_edit.setPlaceholderText(tr("info.agenda_placeholder"))
        self.agenda_edit.setTabChangesFocus(False)
        layout.addWidget(self.agenda_edit, 3)

        layout.addWidget(self._label(tr("info.glossary")))
        self.glossary_edit = QPlainTextEdit()
        self.glossary_edit.setPlaceholderText(tr("info.glossary_placeholder"))
        layout.addWidget(self.glossary_edit, 1)

        layout.addWidget(self._label(tr("info.previous_pv")))
        pv_row = QHBoxLayout()
        self.previous_label = QLabel(tr("info.previous_none"))
        self.previous_label.setObjectName("hint_label")
        self.previous_label.setWordWrap(True)
        self.btn_previous = QPushButton(tr("info.previous_choose"))
        self.btn_previous_clear = QPushButton("✕")
        self.btn_previous_clear.setFixedWidth(32)
        self.btn_previous_clear.setToolTip(tr("info.previous_clear"))
        pv_row.addWidget(self.previous_label, 1)
        pv_row.addWidget(self.btn_previous)
        pv_row.addWidget(self.btn_previous_clear)
        layout.addLayout(pv_row)

        for signal in (self.title_edit.textChanged, self.org_edit.textChanged, self.glossary_edit.textChanged):
            signal.connect(self._on_edit)
        self.date_edit.dateChanged.connect(self._on_edit)
        self.agenda_edit.textChanged.connect(self._on_agenda_edit)
        self.btn_previous.clicked.connect(self.previous_pv_requested.emit)
        self.btn_previous_clear.clicked.connect(self.previous_pv_cleared.emit)

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    # ── Binding ───────────────────────────────────────────

    def load(self, meeting: Meeting):
        """Show a meeting's metadata without emitting change signals."""
        self._loading = True
        self._meeting = meeting
        self.title_edit.setText(meeting.title)
        self.date_edit.setDate(QDate.fromString(meeting.date, "yyyy-MM-dd"))
        self.org_edit.setText(meeting.organization)
        self.agenda_edit.setPlainText(meeting.agenda_text)
        self.glossary_edit.setPlainText("\n".join(meeting.glossary))
        self.refresh_previous_pv()
        self._loading = False

    def refresh_previous_pv(self):
        """Update the previous-PV label from the meeting."""
        name = self._meeting.previous_pv if self._meeting else ""
        self.previous_label.setText(os.path.basename(name) if name else tr("info.previous_none"))
        self.btn_previous_clear.setEnabled(bool(name))

    def apply_to(self, meeting: Meeting):
        """Copy the form values into the meeting (does not save it)."""
        meeting.title = self.title_edit.text().strip()
        meeting.date = self.date_edit.date().toString("yyyy-MM-dd")
        meeting.organization = self.org_edit.text().strip()
        meeting.agenda_text = self.agenda_edit.toPlainText()
        meeting.glossary = [line.strip() for line in self.glossary_edit.toPlainText().splitlines() if line.strip()]

    def flush(self):
        """Emit pending debounced signals immediately."""
        if self._debounce.isActive():
            self._debounce.stop()
            self.changed.emit()
        if self._agenda_debounce.isActive():
            self._agenda_debounce.stop()
            self.changed.emit()
            self.agenda_changed.emit()

    def _on_edit(self, *_args):
        if not self._loading:
            self._debounce.start()

    def _on_agenda_edit(self):
        if not self._loading:
            self._debounce.start()
            self._agenda_debounce.start()
