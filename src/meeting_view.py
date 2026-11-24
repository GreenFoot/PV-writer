"""Meeting view: recording controls, notes, transcript and PV preview for one meeting.

The recording / transcription orchestration is ported from dnd_logger@bc2bdf1
src/session_tab.py (thread tracking, live transcription on silences, import with ordering,
drag-and-drop, FLAC save, post-recording dialog, FLAC cleanup). The UI is new: the
bookmarks are replaced by timestamped notes and the summary by the PV pipeline.
"""

import glob
import logging
import os
import shutil
import time

import soundfile as sf
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QImageReader,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextImageFormat,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .agenda import AgendaNode, iter_nodes, parse_agenda
from .audio_order_dialog import AudioOrderDialog
from .audio_recorder import AudioRecorder
from .docx_exporter import export_docx
from .freeze_watchdog import FreezeWatchdog
from .i18n import tr
from .meeting import EXTRACTION_FILE, PV_FILE, Meeting, read_json, write_json
from .meeting_info_panel import MeetingInfoPanel
from .notes_editor import NotesEditor
from .pv_document import IMAGE_MAX_WIDTH, render_preview_html
from .pv_import import import_docx, qt_records, records_to_pv, store_image
from .pv_pipeline import DEFAULT_AGENDA, SUMMARY_NODE, PvInputs, start_pv_generation
from .transcriber import is_transcript_file, start_live_transcription, start_transcription
from .utils import format_duration, format_file_size

log = logging.getLogger("pvwriter.meeting_view")

COLOR_INFO = "#5f6b7a"
COLOR_WORK = "#b26a00"
COLOR_OK = "#2e7d32"
COLOR_ERROR = "#c62828"
_LIVE_COOLDOWN_SECONDS = 60
# Send a live chunk even without a pause in the talk, so a lively discussion (or steady
# background noise) never leaves a long stretch untranscribed until Stop.
_LIVE_MAX_CHUNK_SECONDS = 600


def meeting_agenda(meeting: Meeting) -> list[AgendaNode]:
    """Return the meeting's agenda tree (a default single item when empty)."""
    return parse_agenda(meeting.agenda_text if meeting.agenda_text.strip() else DEFAULT_AGENDA)


class PvEditor(QTextEdit):
    """Editable PV view; Ctrl/Cmd + click on an agenda link jumps to the section."""

    def mouseReleaseEvent(self, event):
        anchor = self.anchorAt(event.position().toPoint())
        if anchor.startswith("#") and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.scrollToAnchor(anchor[1:])
            return
        super().mouseReleaseEvent(event)


class SlidePickerDialog(QDialog):
    """Show the pages of a PDF as thumbnails and let the user pick one."""

    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)
        from PySide6.QtPdf import QPdfDocument  # pylint: disable=import-outside-toplevel

        self.setWindowTitle(tr("dialog.slide_pick_title"))
        self.resize(820, 560)
        self.document = QPdfDocument(self)
        self.document.load(pdf_path)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(240, 150))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setSpacing(8)
        for page in range(self.document.pageCount()):
            size = self.document.pagePointSize(page)
            scale = 240 / max(1.0, size.width())
            image = self.document.render(page, QSize(240, int(size.height() * scale)))
            item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), str(page + 1))
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_png(self, width: int = 1800) -> bytes:
        """Render the chosen page as PNG bytes, or b"" when nothing is selected."""
        item = self.list.currentItem()
        if item is None:
            return b""
        page = item.data(Qt.ItemDataRole.UserRole)
        size = self.document.pagePointSize(page)
        image = self.document.render(page, QSize(width, int(size.height() * width / max(1.0, size.width()))))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)


class _DropOverlay(QWidget):
    """Translucent overlay shown while files are dragged over the view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        """Draw the drop hint."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(17, 85, 204, 40))
        painter.setPen(QColor("#1155CC"))
        font = painter.font()
        font.setPointSize(16)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("meeting.drop_hint"))


class PostRecordingDialog(QDialog):
    """Dialog shown after stopping a recording that was not live-transcribed."""

    TRANSCRIBE = "transcribe"
    SAVE_ONLY = "save_only"

    def __init__(self, wav_path: str, duration: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("post.title"))
        self.setMinimumWidth(400)
        self.result_action = self.SAVE_ONLY

        layout = QVBoxLayout(self)
        size = os.path.getsize(wav_path) if os.path.exists(wav_path) else 0
        info = QLabel(
            f"{tr('post.duration')} {format_duration(duration)}\n"
            f"{tr('post.size')} {format_file_size(size)}\n"
            f"{tr('post.file')} {os.path.basename(wav_path)}"
        )
        layout.addWidget(info)
        layout.addSpacing(12)

        btn_layout = QHBoxLayout()
        btn_transcribe = QPushButton(tr("post.btn_transcribe"))
        btn_transcribe.setObjectName("btn_primary")
        btn_transcribe.clicked.connect(lambda: self._choose(self.TRANSCRIBE))
        btn_save = QPushButton(tr("post.btn_later"))
        btn_save.clicked.connect(lambda: self._choose(self.SAVE_ONLY))
        btn_layout.addWidget(btn_transcribe)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)

    def _choose(self, action):
        self.result_action = action
        self.accept()


class MeetingView(QWidget):
    """Everything about the currently open meeting."""

    transcription_completed = Signal()
    generation_completed = Signal()
    operation_failed = Signal()
    title_changed = Signal(str)
    recording_state_changed = Signal(bool)

    _AUDIO_EXTENSIONS = {".flac", ".wav", ".mp3", ".ogg", ".m4a", ".wma"}
    _TRANSCRIPT_EXTENSIONS = {".txt", ".md", ".text"}

    def __init__(self, config: dict, meeting: Meeting, parent=None):
        super().__init__(parent)
        self._config = config
        self._meeting = meeting
        self._recorder = AudioRecorder(config, self)
        self._active_threads = []  # (thread, worker) pairs kept alive until QThread.finished
        self._transcription_thread = None
        self._transcription_worker = None
        self._pv_thread = None
        self._pv_worker = None
        self._transcribing = False
        self._generating = False
        self._transcribing_file_label = ""  # "file 2/3" prefix while a batch is transcribed
        self._elapsed = 0
        self._base_seconds = 0.0  # audio already recorded for this meeting before this take
        self._bodies: dict[str, str] = {}
        self._summary: dict = {}
        self._freeze_watchdog = None
        self._drop_overlay = None

        # Live transcription state (ported from dnd_logger)
        self._live_parts = []
        self._last_live_transcription = 0.0
        self._live_tx_thread = None
        self._live_tx_worker = None
        self._live_tx_pending = False
        self._stop_after_current = False
        self._is_final_live_chunk = False
        self._current_wav_path = None
        self._quiet_stop = False  # stopping because the app closes: no dialog, no new worker

        self.setAcceptDrops(True)
        self._build_ui()
        self._connect_signals()
        self._freeze_watchdog = FreezeWatchdog(self)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(1000)
        self._pulse_timer.timeout.connect(self._pulse_record_button)
        self._pulse_state = False
        self.load_meeting(meeting)

    # ── UI ────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Recording bar
        rec_bar = QHBoxLayout()
        self.btn_record = QPushButton(tr("rec.btn.record"))
        self.btn_record.setObjectName("btn_record")
        self.btn_record.setToolTip(tr("rec.btn.record_tooltip"))
        self.btn_stop = QPushButton(tr("rec.btn.stop"))
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        self.btn_stamp = QPushButton(tr("rec.btn.stamp"))
        self.btn_stamp.setToolTip(tr("rec.btn.stamp_tooltip"))
        self.btn_stamp.setEnabled(False)
        self.duration_label = QLabel("00:00:00")
        self.duration_label.setObjectName("duration_label")
        self.vu_meter = QProgressBar()
        self.vu_meter.setRange(0, 100)
        self.vu_meter.setTextVisible(False)
        self.vu_meter.setMaximumHeight(10)
        self.vu_meter.setMaximumWidth(220)
        rec_bar.addWidget(self.btn_record)
        rec_bar.addWidget(self.btn_stop)
        rec_bar.addWidget(self.btn_stamp)
        rec_bar.addSpacing(12)
        rec_bar.addWidget(self.duration_label)
        rec_bar.addWidget(self.vu_meter)
        rec_bar.addStretch()
        layout.addLayout(rec_bar)

        self.status_label = QLabel(tr("status.ready"))
        self.status_label.setObjectName("status_label")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Main area: info panel | tabs
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.info_panel = MeetingInfoPanel()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.info_panel)
        scroll.setMinimumWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.splitter.addWidget(scroll)

        self.tabs = QTabWidget()
        # Notes tab (editor created in load_meeting, it needs the file path)
        self._notes_tab = QWidget()
        notes_layout = QVBoxLayout(self._notes_tab)
        notes_layout.setContentsMargins(0, 4, 0, 0)
        notes_bar = QHBoxLayout()
        notes_hint = QLabel(tr("notes.hint"))
        notes_hint.setObjectName("hint_label")
        notes_hint.setWordWrap(True)
        self.btn_insert_agenda = QPushButton(tr("notes.btn_insert_agenda"))
        notes_bar.addWidget(notes_hint, 1)
        notes_bar.addWidget(self.btn_insert_agenda)
        notes_layout.addLayout(notes_bar)
        self._notes_layout = notes_layout
        self.notes_editor: NotesEditor | None = None
        self.tabs.addTab(self._notes_tab, tr("tab.notes"))

        # Transcript tab
        self.transcript_edit = QPlainTextEdit()
        self.transcript_edit.setObjectName("transcript_edit")
        self.transcript_edit.setPlaceholderText(tr("transcript.placeholder"))
        self.tabs.addTab(self.transcript_edit, tr("tab.transcript"))

        # PV tab
        pv_tab = QWidget()
        pv_layout = QVBoxLayout(pv_tab)
        pv_layout.setContentsMargins(0, 4, 0, 0)
        pv_bar = QHBoxLayout()
        pv_bar.addWidget(QLabel(tr("pv.regen_label")))
        self.node_combo = QComboBox()
        self.node_combo.setMinimumWidth(220)
        self.btn_regen_node = QPushButton(tr("pv.btn_regen_node"))
        self.btn_open_docx = QPushButton(tr("pv.btn_open_docx"))
        pv_bar.addWidget(self.node_combo, 1)
        pv_bar.addWidget(self.btn_regen_node)
        self._image_menu = QMenu(self)
        self._act_image_slide = self._image_menu.addAction(tr("pv.image_from_slides"))
        self._act_image_file = self._image_menu.addAction(tr("pv.image_from_file"))
        # QPushButton (not QToolButton) so it takes the stylesheet of the other buttons
        self.btn_insert_image = QPushButton(tr("pv.btn_insert_image") + "  ▾")
        self.btn_insert_image.setObjectName("btn_menu")
        self.btn_insert_image.setToolTip(tr("pv.insert_image_tooltip"))
        self.btn_insert_image.setMenu(self._image_menu)
        pv_bar.addWidget(self.btn_insert_image)
        pv_bar.addStretch()
        pv_bar.addWidget(self.btn_open_docx)
        pv_layout.addLayout(pv_bar)
        self.pv_preview = PvEditor()
        self.pv_preview.setObjectName("pv_preview")
        self.pv_preview.setAcceptRichText(False)  # pasted text keeps the PV formatting
        self.pv_preview.setPlaceholderText(tr("pv.placeholder"))
        self.pv_preview.setToolTip(tr("pv.edit_hint"))
        pv_layout.addWidget(self.pv_preview)
        self._pv_loading = False  # True while the view is filled from the bodies
        self._pv_edit_timer = QTimer(self)
        self._pv_edit_timer.setSingleShot(True)
        self._pv_edit_timer.setInterval(1000)
        self._pv_edit_timer.timeout.connect(self._save_pv_edits)
        self.pv_versions_label = QLabel("")
        self.pv_versions_label.setObjectName("hint_label")
        pv_layout.addWidget(self.pv_versions_label)
        self.tabs.addTab(pv_tab, tr("tab.pv"))

        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([340, 900])
        layout.addWidget(self.splitter, 1)

        # Action bar
        actions = QHBoxLayout()
        self.btn_transcribe = QPushButton(tr("action.transcribe"))
        self.btn_generate = QPushButton(tr("action.generate"))
        self.btn_generate.setObjectName("btn_primary")
        self.btn_export = QPushButton(tr("action.export_docx"))
        self.btn_export.setObjectName("btn_primary")

        self._more_menu = QMenu(self)
        self._act_import = self._more_menu.addAction(tr("action.import"))
        self._act_save_audio = self._more_menu.addAction(tr("action.save_audio"))
        self._act_import_docx = self._more_menu.addAction(tr("action.import_docx"))
        self._more_menu.addSeparator()
        self._act_open_folder = self._more_menu.addAction(tr("action.open_folder"))
        self.btn_more = QPushButton("⋮")
        self.btn_more.setObjectName("btn_more")
        self.btn_more.setToolTip(tr("action.more_tooltip"))
        self.btn_more.setMenu(self._more_menu)

        actions.addWidget(self.btn_transcribe)
        actions.addWidget(self.btn_generate)
        actions.addWidget(self.btn_export)
        actions.addStretch()
        actions.addWidget(self.btn_more)
        layout.addLayout(actions)

        self._transcript_timer = QTimer(self)
        self._transcript_timer.setSingleShot(True)
        self._transcript_timer.setInterval(1500)
        self._transcript_timer.timeout.connect(self._save_transcript)

    def _connect_signals(self):
        self.btn_record.clicked.connect(self._on_record_btn_clicked)
        self.btn_stop.clicked.connect(self._recorder.stop_recording)
        self.btn_stamp.clicked.connect(self._insert_stamp)
        QShortcut(QKeySequence(Qt.Key.Key_F2), self, activated=self._insert_stamp)
        self.btn_transcribe.clicked.connect(self._start_transcription)
        self.btn_generate.clicked.connect(lambda: self._start_generation(None))
        self.btn_regen_node.clicked.connect(self._regenerate_selected_node)
        self._act_image_slide.triggered.connect(self._insert_slide_image)
        self._act_image_file.triggered.connect(self._insert_file_image)
        self.btn_export.clicked.connect(self._export_docx)
        self.btn_open_docx.clicked.connect(self._open_last_docx)
        self.btn_insert_agenda.clicked.connect(self._insert_agenda_in_notes)
        self._act_import.triggered.connect(self._import_files)
        self._act_save_audio.triggered.connect(self._save_audio)
        self._act_open_folder.triggered.connect(self.open_folder)
        self._act_import_docx.triggered.connect(self._import_pv_docx)
        self.pv_preview.textChanged.connect(self._on_pv_edited)
        self.transcript_edit.textChanged.connect(self._on_transcript_edited)

        self.info_panel.changed.connect(self._on_info_changed)
        self.info_panel.agenda_changed.connect(self._on_agenda_changed)
        self.info_panel.previous_pv_requested.connect(self._choose_previous_pv)
        self.info_panel.previous_pv_cleared.connect(self._clear_previous_pv)
        self.info_panel.slides_requested.connect(self._choose_slides)
        self.info_panel.slides_cleared.connect(self._clear_slides)

        self._recorder.recording_started.connect(self._on_recording_started)
        self._recorder.recording_stopped.connect(self._on_recording_stopped)
        self._recorder.recording_paused.connect(self._on_recording_paused)
        self._recorder.recording_resumed.connect(self._on_recording_resumed)
        self._recorder.level_update.connect(lambda level: self.vu_meter.setValue(int(level * 100)))
        self._recorder.duration_update.connect(self._on_duration_update)
        self._recorder.error_occurred.connect(self._on_error)
        self._recorder.silence_detected.connect(self._on_silence_detected)

    def _set_status(self, text: str, color: str = COLOR_INFO):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def _update_buttons(self):
        """Enable actions according to the recording / busy state."""
        recording = self._recorder.is_recording
        busy = self._transcribing or self._generating
        has_sources = bool(self._meeting.source_paths())
        self.btn_transcribe.setEnabled(not recording and not busy and has_sources)
        self.btn_generate.setEnabled(not recording and not busy)
        self.btn_regen_node.setEnabled(not recording and not busy and bool(self._bodies))
        self.btn_insert_image.setEnabled(not self._generating and bool(self._bodies))
        self._act_image_slide.setEnabled(self._meeting.slides.lower().endswith(".pdf"))
        self.btn_export.setEnabled(not busy and any(self._bodies.values()))
        self.btn_open_docx.setEnabled(bool(self._meeting.docx_versions))
        self._act_import.setEnabled(not recording and not busy)
        self._act_save_audio.setEnabled(any(not is_transcript_file(p) for p in self._meeting.source_paths()))
        self._act_import_docx.setEnabled(not busy)
        self.pv_preview.setReadOnly(self._generating or not self._bodies)
        self.btn_stamp.setEnabled(recording)

    # ── Meeting binding ───────────────────────────────────

    @property
    def meeting(self) -> Meeting:
        """The meeting currently shown."""
        return self._meeting

    @property
    def is_recording(self) -> bool:
        """True while a recording is in progress (paused included)."""
        return self._recorder.is_recording

    @property
    def is_busy(self) -> bool:
        """True while a transcription or a generation runs."""
        return self._transcribing or self._generating or self._live_tx_pending

    def load_meeting(self, meeting: Meeting):
        """Show another meeting. The caller makes sure nothing is recording or running."""
        if self.notes_editor is not None:
            self.save_all()
        self._meeting = meeting
        self.info_panel.load(meeting)

        if self.notes_editor is None:
            self.notes_editor = NotesEditor(meeting.notes_path)
            self.notes_editor.set_time_source(self._current_recording_time)
            self._notes_layout.addWidget(self.notes_editor, 1)
        else:
            self.notes_editor.switch_file(meeting.notes_path, "<p></p>")
        self.notes_editor.set_auto_timestamps(self._config.get("auto_timestamp_notes", True))
        if not os.path.exists(meeting.notes_path) and meeting.agenda_text.strip():
            self.notes_editor.apply_agenda(meeting_agenda(meeting))

        self.transcript_edit.blockSignals(True)
        self.transcript_edit.setPlainText(self._read_text(meeting.transcript_path))
        self.transcript_edit.blockSignals(False)

        pv_data = read_json(meeting.path(PV_FILE), {})
        self._bodies = pv_data.get("bodies", {})
        self._summary = pv_data.get("summary", {})
        self._refresh_agenda_views()
        self._refresh_versions()
        self.duration_label.setText(format_duration(int(self._audio_duration())))
        self._set_status(tr("status.ready"))
        self._update_buttons()
        self.title_changed.emit(meeting.pv_title)

    def save_all(self):
        """Persist notes, transcript and metadata."""
        self.info_panel.flush()
        if self.notes_editor is not None:
            self.notes_editor.save()
        if self._transcript_timer.isActive():
            self._transcript_timer.stop()
            self._save_transcript()
        if self._pv_edit_timer.isActive():
            self._pv_edit_timer.stop()
            self._save_pv_edits()
        self._sync_meeting()

    def _sync_meeting(self):
        """Copy the form into the meeting and save it."""
        self.info_panel.apply_to(self._meeting)
        self._meeting.save()

    def _on_info_changed(self):
        self._sync_meeting()
        self._refresh_preview()
        self.title_changed.emit(self._meeting.pv_title)

    def _on_agenda_changed(self):
        self._sync_meeting()
        if self.notes_editor is not None and not self.notes_editor.has_body_text():
            self.notes_editor.apply_agenda(meeting_agenda(self._meeting))
        self._refresh_agenda_views()

    def _insert_agenda_in_notes(self):
        self._sync_meeting()
        self.notes_editor.apply_agenda(meeting_agenda(self._meeting))

    def _refresh_agenda_views(self):
        """Refresh the node picker and the preview after an agenda change."""
        current = self.node_combo.currentData()
        self.node_combo.clear()
        self.node_combo.addItem(tr("pv.regen_summary"), SUMMARY_NODE)
        for node in iter_nodes(meeting_agenda(self._meeting)):
            self.node_combo.addItem(("    " * (node.level - 1)) + node.heading, node.node_id)
        idx = self.node_combo.findData(current)
        if idx >= 0:
            self.node_combo.setCurrentIndex(idx)
        self._refresh_preview()

    def _refresh_preview(self):
        self._pv_loading = True
        try:
            if not self._bodies:
                self.pv_preview.clear()
                return
            scroll = self.pv_preview.verticalScrollBar().value()
            html = render_preview_html(
                self._meeting.pv_title, meeting_agenda(self._meeting), self._bodies, self._summary
            )
            # The <img src> of the bodies are relative to the meeting folder
            self.pv_preview.document().setBaseUrl(QUrl.fromLocalFile(self._meeting.folder + os.sep))
            self.pv_preview.setHtml(html)
            self.pv_preview.verticalScrollBar().setValue(scroll)
        finally:
            self._pv_loading = False

    def _on_pv_edited(self):
        if not self._pv_loading and not self._generating and self._bodies:
            self._pv_edit_timer.start()

    def _save_pv_edits(self):
        """Read the edited PV tab back into the bodies and the synthesis, then save them."""
        if self._generating or not self._bodies:
            return
        bodies, summary, warnings = records_to_pv(
            qt_records(self.pv_preview.document(), self._meeting.folder), meeting_agenda(self._meeting)
        )
        self._bodies, self._summary = bodies, summary
        self._write_pv(edited=time.strftime("%Y-%m-%dT%H:%M"))
        if warnings:
            self._set_status(" ".join(warnings), COLOR_WORK)
        else:
            self._set_status(tr("status.pv_saved"), COLOR_OK)

    def _insert_slide_image(self):
        """Insert one page of the meeting's slides (PDF) at the cursor of the PV tab."""
        dialog = SlidePickerDialog(self._meeting.path(self._meeting.slides), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.selected_png()
        if data:
            self._insert_image(store_image(self._meeting.folder, data, "png"), IMAGE_MAX_WIDTH)

    def _insert_file_image(self):
        """Insert a picture file at the cursor of the PV tab."""
        path, _ = QFileDialog.getOpenFileName(self, tr("dialog.image_title"), "", tr("dialog.image_filter"))
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            self._on_error(tr("error.copy_failed", error=e))
            return
        size = QImageReader(path).size()
        width = min(IMAGE_MAX_WIDTH, size.width()) if size.isValid() else IMAGE_MAX_WIDTH
        self._insert_image(store_image(self._meeting.folder, data, os.path.splitext(path)[1]), width)

    def _insert_image(self, src: str, width: int):
        """Add a picture in a new paragraph after the cursor's paragraph; the autosave stores it."""
        cursor = self.pv_preview.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        image = QTextImageFormat()
        image.setName(src)
        image.setWidth(width)
        cursor.insertImage(image)
        self.pv_preview.setTextCursor(cursor)
        self.pv_preview.setFocus()

    def _write_pv(self, **extra):
        """Save the bodies and the synthesis to pv.json, keeping the other keys."""
        path = self._meeting.path(PV_FILE)
        data = read_json(path, {})
        data.update({"bodies": self._bodies, "summary": self._summary}, **extra)
        write_json(path, data)

    def _import_pv_docx(self):
        """Replace the PV with the content of a Word document corrected by hand."""
        self.save_all()
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialog.import_docx_title"), self._meeting.folder, tr("dialog.import_docx_filter")
        )
        if not path:
            return
        backup = "pv_avant_import.json"
        if self._bodies:
            answer = QMessageBox.question(
                self, tr("confirm.title"), tr("confirm.import_docx", name=os.path.basename(path), backup=backup)
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            bodies, summary, warnings = import_docx(path, meeting_agenda(self._meeting), self._meeting.folder)
        except Exception as e:  # python-docx raises several types on a damaged file
            log.exception("Word import failed")
            self._on_error(tr("error.import_docx_failed", error=e))
            return
        if os.path.exists(self._meeting.path(PV_FILE)):
            shutil.copy2(self._meeting.path(PV_FILE), self._meeting.path(backup))
        self._bodies, self._summary = bodies, summary
        self._write_pv(imported=os.path.basename(path), edited=time.strftime("%Y-%m-%dT%H:%M"))
        self._refresh_preview()
        self.tabs.setCurrentIndex(2)
        message = tr("status.pv_imported", name=os.path.basename(path))
        self._set_status(" ".join([message] + warnings), COLOR_WORK if warnings else COLOR_OK)
        self._update_buttons()

    def _refresh_versions(self):
        versions = self._meeting.docx_versions
        self.pv_versions_label.setText(tr("pv.versions", files=", ".join(versions)) if versions else "")

    @staticmethod
    def _read_text(path: str) -> str:
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _on_transcript_edited(self):
        self._transcript_timer.start()

    def _save_transcript(self):
        with open(self._meeting.transcript_path, "w", encoding="utf-8") as f:
            f.write(self.transcript_edit.toPlainText())

    def _audio_duration(self) -> float:
        """Total duration of the meeting's audio sources, in seconds."""
        total = 0.0
        for path in self._meeting.source_paths():
            if is_transcript_file(path):
                continue
            try:
                total += sf.info(path).duration
            except Exception:
                log.warning("Could not read duration of %s", path)
        return total

    def open_folder(self):
        """Open the meeting folder in the file manager."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._meeting.folder))

    # ── Recording ─────────────────────────────────────────

    def _current_recording_time(self) -> float | None:
        if not self._recorder.is_recording:
            return None
        return self._base_seconds + self._recorder.recorded_seconds

    def _insert_stamp(self):
        if self.notes_editor is not None and self._recorder.is_recording:
            self.notes_editor.insert_timestamp()
            self.notes_editor.editor.setFocus()

    def toggle_recording(self):
        """Start, pause or resume (used by the Ctrl+R shortcut)."""
        self._on_record_btn_clicked()

    def _on_record_btn_clicked(self):
        """Handle the record/pause/resume button click based on current state."""
        if not self._recorder.is_recording:
            if self._transcribing or self._generating:
                return
            self.save_all()
            self._base_seconds = self._audio_duration()
            self._recorder.start_recording(self._meeting.folder)
        elif self._recorder.is_paused:
            self._recorder.resume_recording()
        else:
            self._recorder.pause_recording()

    def _on_recording_started(self):
        self.btn_record.setText(tr("rec.btn.pause"))
        self.btn_stop.setEnabled(True)
        self._elapsed = 0
        self._live_parts = []
        self._last_live_transcription = time.time()
        self._current_wav_path = self._recorder.wav_path
        self._set_status(tr("status.recording"), COLOR_ERROR)
        self._freeze_watchdog.start("recording")
        self._pulse_timer.start()
        self.tabs.setCurrentWidget(self._notes_tab)
        if self.notes_editor is not None:
            self.notes_editor.editor.setFocus()
        self._update_buttons()
        self.recording_state_changed.emit(True)

    def _on_recording_paused(self):
        self.btn_record.setText(tr("rec.btn.resume"))
        self._pulse_timer.stop()
        self.btn_record.setStyleSheet("")
        self._set_status(tr("status.paused"), COLOR_WORK)

    def _on_recording_resumed(self):
        self.btn_record.setText(tr("rec.btn.pause"))
        self._pulse_timer.start()
        self._set_status(tr("status.recording"), COLOR_ERROR)

    def _pulse_record_button(self):
        self._pulse_state = not self._pulse_state
        self.btn_record.setStyleSheet("QPushButton#btn_record { border-color: #c62828; }" if self._pulse_state else "")

    def _on_duration_update(self, seconds: int):
        self._elapsed = seconds
        self.duration_label.setText(format_duration(int(self._base_seconds) + seconds))
        self._maybe_send_live_chunk(_LIVE_MAX_CHUNK_SECONDS)

    def _on_recording_stopped(self, wav_path: str):
        self.btn_record.setText(tr("rec.btn.record"))
        self.btn_record.setStyleSheet("")
        self.btn_stop.setEnabled(False)
        self.vu_meter.setValue(0)
        self._pulse_timer.stop()
        self._freeze_watchdog.stop()
        self._current_wav_path = wav_path
        self._meeting.add_source(wav_path)
        self._meeting.recorded_seconds = self._audio_duration()
        self._meeting.save()
        if self.notes_editor is not None:
            self.notes_editor.save()
        self.recording_state_changed.emit(False)

        if self._quiet_stop:
            # Closing: keep what was transcribed live, don't start anything new
            self._save_transcript()
            return
        if self._live_parts or self._live_tx_pending:
            # Live transcription was active — finalize automatically
            self._set_status(tr("status.finalizing"), COLOR_WORK)
            if self._live_tx_pending:
                self._stop_after_current = True  # a chunk is in flight — queue finalization
            else:
                self._do_final_live_transcription()
        else:
            self._set_status(tr("status.stopped"))
            self._update_buttons()
            dlg = PostRecordingDialog(wav_path, self._elapsed, self)
            dlg.exec()
            if dlg.result_action == PostRecordingDialog.TRANSCRIBE:
                self._start_transcription()
            else:
                self._set_status(tr("status.saved"), COLOR_OK)

    # ── Worker thread lifetime (ported) ───────────────────

    def _track_thread(self, thread, worker):
        """Keep a worker thread and its worker alive until the thread has stopped.

        A QThread held only by an attribute is destroyed as soon as that attribute is
        rebound for the next run. When the previous thread has not finished yet, Qt aborts
        the whole process. Holding the pair in a list and dropping it from
        QThread.finished (delivered on the main thread) makes the lifetime explicit.

        Args:
            thread: The QThread to track.
            worker: The worker object living in that thread.
        """
        entry = (thread, worker)
        self._active_threads.append(entry)
        thread.finished.connect(lambda e=entry: self._retire_thread(e))

    def _retire_thread(self, entry):
        """Drop a finished thread/worker pair once the thread has fully stopped."""
        thread, _worker = entry
        thread.wait()  # safe: called from the main thread on an already-finished thread
        try:
            self._active_threads.remove(entry)
        except ValueError:
            pass

    # ── Live transcription (ported) ───────────────────────

    def _with_base_offset(self, pending):
        """Shift a pending chunk's start by the audio recorded in earlier takes."""
        blocks, samplerate, flac_path, start = pending
        return blocks, samplerate, flac_path, start + self._base_seconds

    def _on_silence_detected(self):
        """Trigger live transcription when silence is detected during recording."""
        self._maybe_send_live_chunk(_LIVE_COOLDOWN_SECONDS)

    def _maybe_send_live_chunk(self, min_interval: float):
        """Send the audio recorded since the last live chunk, if enough time has passed.

        Args:
            min_interval: Seconds that must have elapsed since the previous live chunk.
        """
        if not self._recorder.is_recording or self._recorder.is_paused or self._live_tx_pending:
            return
        if not self._config.get("api_key"):
            return
        now = time.time()
        if now - self._last_live_transcription < min_interval:
            return
        pending = self._recorder.take_pending_audio()
        if not pending:
            return
        self._last_live_transcription = now
        self._launch_live_chunk(pending, final=False)
        self._set_status(tr("status.transcribing_segment", count=len(self._live_parts) + 1), COLOR_ERROR)

    def _launch_live_chunk(self, pending, final: bool):
        self._live_tx_pending = True
        self._is_final_live_chunk = final
        self._live_tx_thread, self._live_tx_worker = start_live_transcription(
            self._with_base_offset(pending), self._config_for_transcription()
        )
        self._live_tx_worker.completed.connect(self._on_live_tx_done)
        self._live_tx_worker.error.connect(self._on_live_tx_error)
        self._track_thread(self._live_tx_thread, self._live_tx_worker)
        self._live_tx_thread.start()

    def _do_final_live_transcription(self):
        """Transcribe remaining audio after recording stopped."""
        remaining = self._recorder.take_pending_audio()
        if remaining:
            self._launch_live_chunk(remaining, final=True)
        else:
            self._finalize_live_transcription()

    def _on_live_tx_done(self, text: str):
        """Handle a completed live transcription chunk."""
        self._live_tx_pending = False
        if text.strip():
            self._live_parts.append(text)
            self.transcript_edit.appendPlainText(text)
        if self._is_final_live_chunk:
            self._is_final_live_chunk = False
            self._finalize_live_transcription()
        elif self._stop_after_current:
            self._stop_after_current = False
            self._do_final_live_transcription()
        else:
            self._set_status(tr("status.segments_transcribed", count=len(self._live_parts)), COLOR_ERROR)

    def _on_live_tx_error(self, msg: str):
        """Handle live transcription error — continue recording."""
        self._live_tx_pending = False
        self._set_status(tr("status.transcription_error", msg=msg), COLOR_ERROR)
        if self._is_final_live_chunk:
            self._is_final_live_chunk = False
            self._finalize_live_transcription()
        elif self._stop_after_current:
            self._stop_after_current = False
            self._do_final_live_transcription()

    def _finalize_live_transcription(self):
        """Save the transcript built live (already appended to the transcript tab)."""
        self._save_transcript()
        self._live_parts = []
        self._set_status(tr("status.transcription_done"), COLOR_OK)
        self._update_buttons()
        self.transcription_completed.emit()

    # ── Batch transcription ───────────────────────────────

    def _config_for_transcription(self) -> dict:
        """Return the config with the meeting glossary merged into the context bias."""
        config = dict(self._config)
        config["context_bias"] = list(self._meeting.glossary) + list(self._config.get("context_bias", []))
        return config

    def _start_transcription(self):
        paths = self._meeting.source_paths()
        if not paths:
            self._on_error(tr("error.no_sources"))
            return
        if self.transcript_edit.toPlainText().strip():
            answer = QMessageBox.question(self, tr("confirm.title"), tr("confirm.overwrite_transcript"))
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._transcribing = True
        self._update_buttons()
        self._set_status(tr("status.transcribing"), COLOR_WORK)
        self.transcript_edit.blockSignals(True)
        self.transcript_edit.clear()
        self.transcript_edit.blockSignals(False)
        self.tabs.setCurrentWidget(self.transcript_edit)
        self._transcribing_file_label = ""
        self._freeze_watchdog.start("transcription")

        self._transcription_thread, self._transcription_worker = start_transcription(
            paths, self._config_for_transcription()
        )
        self._transcription_worker.file_started.connect(self._on_transcription_file_started)
        self._transcription_worker.progress.connect(self._on_transcription_progress)
        self._transcription_worker.chunk_completed.connect(lambda _i, text: self.transcript_edit.appendPlainText(text))
        self._transcription_worker.completed.connect(self._on_transcription_done)
        self._transcription_worker.error.connect(self._on_error)
        self._track_thread(self._transcription_thread, self._transcription_worker)
        self._transcription_thread.start()

    def _on_transcription_file_started(self, index: int, count: int, name: str):
        self._transcribing_file_label = tr("status.transcribing_file", index=index, count=count, name=name)
        self._set_status(self._transcribing_file_label, COLOR_WORK)

    def _on_transcription_progress(self, current: int, total: int):
        text = tr("status.transcribing_chunk", current=current, total=total)
        if self._transcribing_file_label:
            text = f"{self._transcribing_file_label} — {text}"
        self._set_status(text, COLOR_WORK)

    def _on_transcription_done(self, full_text: str):
        self._freeze_watchdog.stop()
        self._transcribing = False
        self.transcript_edit.blockSignals(True)
        self.transcript_edit.setPlainText(full_text)
        self.transcript_edit.blockSignals(False)
        self._save_transcript()
        self._set_status(tr("status.transcription_done"), COLOR_OK)
        self._update_buttons()
        self.transcription_completed.emit()

    # ── Import (ported) ───────────────────────────────────

    def _import_files(self):
        """Pick audio and/or transcript files to add to the meeting."""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, tr("session.dialog.import_title"), "", tr("session.dialog.import_filter")
        )
        if file_paths:
            self._import_from_paths(file_paths)

    def _import_from_paths(self, file_paths):
        """Copy files into the meeting folder, letting the user order all the sources.

        Args:
            file_paths: Newly picked or dropped files.
        """
        candidates = self._meeting.source_paths()
        candidates += [p for p in file_paths if p not in candidates]
        if len(candidates) > 1:
            dlg = AudioOrderDialog(candidates, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            candidates = dlg.ordered_paths()
        if not candidates:
            return

        folder = self._meeting.folder
        dest_paths = []
        for src in candidates:
            if os.path.dirname(os.path.abspath(src)) == os.path.abspath(folder):
                dest_paths.append(src)  # already in the meeting
                continue
            dest_path = self._unique_dest(folder, os.path.basename(src))
            try:
                shutil.copy2(src, dest_path)
            except OSError as e:
                self._on_error(tr("error.copy_failed", error=e))
                return
            dest_paths.append(dest_path)

        self._meeting.set_sources(dest_paths)
        self._meeting.recorded_seconds = self._audio_duration()
        self._meeting.save()
        total_size = sum(os.path.getsize(p) for p in dest_paths if os.path.exists(p))
        self._set_status(tr("status.imported", count=len(dest_paths), size=format_file_size(total_size)), COLOR_OK)
        self._update_buttons()

    @staticmethod
    def _unique_dest(folder, name):
        """Return a path in folder for name, suffixed if the name is taken or reserved."""
        reserved = {"transcript.txt", "notes.html", "meeting.json", "pv.json", "extraction.json"}
        dest = os.path.join(folder, name)
        if not os.path.exists(dest) and name.lower() not in reserved:
            return dest
        stem, ext = os.path.splitext(name)
        i = 2
        while os.path.exists(os.path.join(folder, f"{stem}_{i}{ext}")):
            i += 1
        return os.path.join(folder, f"{stem}_{i}{ext}")

    def _is_importable_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        return ext in self._AUDIO_EXTENSIONS or ext in self._TRANSCRIPT_EXTENSIONS

    def dragEnterEvent(self, event):
        """Accept drag if it contains audio or transcript files."""
        if event.mimeData().hasUrls() and any(
            self._is_importable_file(url.toLocalFile()) for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            if self._drop_overlay is None:
                self._drop_overlay = _DropOverlay(self)
            self._drop_overlay.setGeometry(self.rect())
            self._drop_overlay.show()
            self._drop_overlay.raise_()
            return
        event.ignore()

    def dragLeaveEvent(self, event):
        """Hide the drop overlay."""
        if self._drop_overlay is not None:
            self._drop_overlay.hide()

    def dropEvent(self, event):
        """Import every valid file from the drop."""
        if self._drop_overlay is not None:
            self._drop_overlay.hide()
        if self._recorder.is_recording or self.is_busy:
            self._set_status(tr("error.drop_while_busy"), COLOR_ERROR)
            event.ignore()
            return
        # Drop order is not guaranteed by the OS — sort by name; the ordering dialog fixes it
        paths = sorted(
            (url.toLocalFile() for url in event.mimeData().urls() if self._is_importable_file(url.toLocalFile())),
            key=lambda p: os.path.basename(p).lower(),
        )
        if paths:
            self._import_from_paths(paths)

    def resizeEvent(self, event):
        """Keep the drop overlay sized to the widget."""
        super().resizeEvent(event)
        if self._drop_overlay is not None and self._drop_overlay.isVisible():
            self._drop_overlay.setGeometry(self.rect())

    # ── Save audio (ported) ───────────────────────────────

    def _save_audio(self):
        """Save an audio source as FLAC (converting it if needed)."""
        audio = [p for p in self._meeting.source_paths() if not is_transcript_file(p)]
        if not audio:
            self._on_error(tr("error.no_audio_save"))
            return
        src_path = audio[0]
        if len(audio) > 1:
            names = [os.path.basename(p) for p in audio]
            name, ok = QInputDialog.getItem(self, tr("dialog.save_audio_title"), tr("dialog.save_audio_pick"), names)
            if not ok:
                return
            src_path = audio[names.index(name)]

        base = os.path.splitext(os.path.basename(src_path))[0]
        save_path, _ = QFileDialog.getSaveFileName(
            self, tr("dialog.save_audio_title"), f"{base}.flac", tr("dialog.save_audio_filter")
        )
        if not save_path:
            return
        try:
            if src_path.lower().endswith(".flac"):
                shutil.copy2(src_path, save_path)
            else:
                data, sr = sf.read(src_path, dtype="int16")
                sf.write(save_path, data, sr, format="FLAC")
            self._set_status(tr("status.audio_saved", name=os.path.basename(save_path)), COLOR_OK)
        except Exception as e:
            self._on_error(tr("error.save_failed", error=e))

    # ── PV generation ─────────────────────────────────────

    def _regenerate_selected_node(self):
        node_id = self.node_combo.currentData()
        if node_id:
            self._start_generation(node_id)

    def _start_generation(self, only_node: str | None):
        self.save_all()
        meeting = self._meeting
        inputs = PvInputs(
            title=meeting.title,
            date_fr=meeting.date_fr,
            organization=meeting.organization,
            agenda_text=meeting.agenda_text,
            note_blocks=self.notes_editor.note_blocks() if self.notes_editor else [],
            transcript=self.transcript_edit.toPlainText(),
            recorded_seconds=self._audio_duration(),
            glossary=list(meeting.glossary) + list(self._config.get("context_bias", [])),
        )
        cached = read_json(meeting.path(EXTRACTION_FILE), None)
        previous = meeting.path(meeting.previous_pv) if meeting.previous_pv else ""
        slides = meeting.path(meeting.slides) if meeting.slides else ""

        self._generating = True
        self._update_buttons()
        self._set_status(tr("status.generating"), COLOR_WORK)
        self.tabs.setCurrentIndex(2)
        self._pv_thread, self._pv_worker = start_pv_generation(
            inputs, dict(self._config), cached, only_node, dict(self._bodies), previous, slides
        )
        self._pv_worker.progress.connect(lambda text: self._set_status(text, COLOR_WORK))
        self._pv_worker.section_done.connect(self._on_section_done)
        self._pv_worker.completed.connect(self._on_generation_done)
        self._pv_worker.error.connect(self._on_error)
        self._track_thread(self._pv_thread, self._pv_worker)
        self._pv_thread.start()

    def _on_section_done(self, node_id: str, html: str):
        self._bodies[node_id] = html
        self._refresh_preview()

    def _on_generation_done(self, result, cache):
        self._generating = False
        self._bodies = result.bodies
        self._summary = result.summary
        self._write_pv(generated=time.strftime("%Y-%m-%dT%H:%M"))
        write_json(self._meeting.path(EXTRACTION_FILE), cache)
        self._refresh_preview()
        if result.warnings:
            self._set_status(" ".join(result.warnings), COLOR_WORK)
        else:
            self._set_status(tr("status.generation_done"), COLOR_OK)
        self._update_buttons()
        self.generation_completed.emit()

    def _export_docx(self):
        self.save_all()
        name = self._meeting.next_docx_name()
        path = self._meeting.path(name)
        try:
            export_docx(
                path,
                self._meeting.pv_title,
                meeting_agenda(self._meeting),
                self._bodies,
                self._summary,
                image_root=self._meeting.folder,
            )
        except Exception as e:
            log.exception("Word export failed")
            self._on_error(tr("error.export_failed", error=e))
            return
        self._meeting.docx_versions.append(name)
        self._meeting.save()
        self._refresh_versions()
        self._update_buttons()
        self._set_status(tr("status.exported", name=name), COLOR_OK)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_last_docx(self):
        if self._meeting.docx_versions:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._meeting.path(self._meeting.docx_versions[-1])))

    # ── Previous PV ───────────────────────────────────────

    def _choose_previous_pv(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("dialog.previous_title"), "", tr("dialog.previous_filter"))
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        name = f"pv_precedent{ext}"
        try:
            shutil.copy2(path, self._meeting.path(name))
        except OSError as e:
            self._on_error(tr("error.copy_failed", error=e))
            return
        self._meeting.previous_pv = name
        self._meeting.save()
        self.info_panel.refresh_previous_pv()

    def _choose_slides(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("dialog.slides_title"), "", tr("dialog.slides_filter"))
        if not path:
            return
        name = f"slides{os.path.splitext(path)[1].lower()}"
        try:
            shutil.copy2(path, self._meeting.path(name))
        except OSError as e:
            self._on_error(tr("error.copy_failed", error=e))
            return
        self._meeting.slides = name
        self._meeting.save()
        self.info_panel.refresh_slides()

    def _clear_slides(self):
        self._meeting.slides = ""
        self._meeting.save()
        self.info_panel.refresh_slides()

    def _clear_previous_pv(self):
        self._meeting.previous_pv = ""
        self._meeting.save()
        self.info_panel.refresh_previous_pv()

    # ── Errors and cleanup ────────────────────────────────

    def _on_error(self, msg: str):
        self._set_status(msg, COLOR_ERROR)
        self._transcribing = False
        self._generating = False
        self._freeze_watchdog.stop()
        if not self._recorder.is_recording:
            self.btn_record.setText(tr("rec.btn.record"))
            self.btn_record.setStyleSheet("")
            self.btn_stop.setEnabled(False)
            self._pulse_timer.stop()
        self._update_buttons()
        self.operation_failed.emit()

    def stop_recording(self, quiet: bool = False):
        """Stop the recording if one is in progress.

        Args:
            quiet: True when the app is closing: no post-recording dialog and no final
                live-transcription chunk.
        """
        if self._recorder.is_recording:
            self._quiet_stop = quiet
            self._recorder.stop_recording()
            self._quiet_stop = False

    def cleanup(self):
        """Save everything, stop threads and remove temporary FLAC files on exit."""
        self.save_all()
        if self._freeze_watchdog:
            self._freeze_watchdog.stop()
        self._pulse_timer.stop()
        for thread, _worker in list(self._active_threads):
            if thread.isRunning():
                thread.quit()
                thread.wait(2000)
        self._cleanup_flac_files()

    def _cleanup_flac_files(self):
        """Remove temporary FLAC chunks from the meeting folder (never the sources)."""
        sources = {os.path.abspath(p) for p in self._meeting.source_paths()}
        for flac_file in glob.glob(os.path.join(self._meeting.folder, "*.flac")):
            if os.path.abspath(flac_file) in sources:
                continue  # an imported FLAC is a source, not a temporary chunk
            try:
                os.remove(flac_file)
            except OSError:
                pass

    def update_config(self, config: dict):
        """Apply new settings."""
        self._config = config
        self._recorder._config = config  # pylint: disable=protected-access
        if self.notes_editor is not None:
            self.notes_editor.set_auto_timestamps(config.get("auto_timestamp_notes", True))
