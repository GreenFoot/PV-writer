"""Main window: menus, meeting lifecycle (new / open), settings, notifications."""

import logging
import os

from PySide6.QtCore import QByteArray, QDate, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
)

from . import __version__
from .i18n import tr
from .meeting import Meeting, list_meetings
from .meeting_view import MeetingView
from .notifications import NotificationSounds, alert_window
from .settings import FirstRunWizard, SettingsDialog
from .utils import APP_NAME, load_config, save_config

log = logging.getLogger("pvwriter.main_window")


class NewMeetingDialog(QDialog):
    """Asks for the meeting name, date and organization."""

    def __init__(self, default_organization: str = "", default_title: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("new.title"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(default_title)
        self.title_edit.setPlaceholderText(tr("info.title_placeholder"))
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.org_edit = QLineEdit(default_organization)
        self.org_edit.setPlaceholderText(tr("info.organization_placeholder"))
        form.addRow(tr("info.title"), self.title_edit)
        form.addRow(tr("info.date"), self.date_edit)
        form.addRow(tr("info.organization"), self.org_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        """Return ``(title, iso_date, organization)``."""
        return (
            self.title_edit.text().strip() or tr("new.default_title"),
            self.date_edit.date().toString("yyyy-MM-dd"),
            self.org_edit.text().strip(),
        )


class OpenMeetingDialog(QDialog):
    """Lists the saved meetings, most recent first."""

    def __init__(self, meetings: list[Meeting], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("open.title"))
        self.setMinimumSize(480, 380)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        for meeting in meetings:
            item = QListWidgetItem(f"{meeting.date_fr} — {meeting.title}")
            item.setData(Qt.ItemDataRole.UserRole, meeting.folder)
            item.setToolTip(meeting.folder)
            self.list.addItem(item)
        if meetings:
            self.list.setCurrentRow(0)
        else:
            layout.addWidget(QLabel(tr("open.none")))
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_folder(self) -> str | None:
        """Return the folder of the selected meeting."""
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class MainWindow(QMainWindow):
    """Top-level window hosting one ``MeetingView``."""

    def __init__(self):
        super().__init__()
        self._config = load_config()
        self._sounds = NotificationSounds()
        self.setMinimumSize(1000, 680)

        meeting = self._initial_meeting()
        self.view = MeetingView(self._config, meeting, self)
        self.setCentralWidget(self.view)
        self.view.title_changed.connect(self._update_title)
        self.view.transcription_completed.connect(lambda: self._notify("transcription_done"))
        self.view.generation_completed.connect(lambda: self._notify("summary_done"))
        self.view.operation_failed.connect(lambda: self._notify("error"))
        self.view.recording_state_changed.connect(self._on_recording_state)
        self._build_menu()
        self._update_title(meeting.pv_title)
        self._restore_geometry()

    # ── Meetings ──────────────────────────────────────────

    def _initial_meeting(self) -> Meeting:
        """Reopen the last meeting, or create a first one."""
        last = self._config.get("last_meeting", "")
        if last and os.path.isdir(last):
            try:
                return Meeting.load(last)
            except (OSError, ValueError, TypeError):
                log.warning("Could not reopen last meeting %s", last)
        meetings = list_meetings()
        if meetings:
            return meetings[0]
        meeting = Meeting.create(tr("new.default_title"), QDate.currentDate().toString("yyyy-MM-dd"))
        self._remember(meeting)
        return meeting

    def _remember(self, meeting: Meeting):
        self._config["last_meeting"] = meeting.folder
        save_config(self._config)

    def _can_switch(self) -> bool:
        if self.view.is_recording or self.view.is_busy:
            QMessageBox.information(self, tr("busy.title"), tr("busy.message"))
            return False
        return True

    def _new_meeting(self):
        if not self._can_switch():
            return
        current = self.view.meeting
        dlg = NewMeetingDialog(
            current.organization or self._config.get("default_organization", ""), current.title, self
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        title, iso_date, organization = dlg.values()
        self.view.save_all()
        meeting = Meeting.create(title, iso_date, organization)
        # Recurring meetings usually keep the same glossary: carry it over
        meeting.glossary = list(current.glossary)
        meeting.save()
        self.view.load_meeting(meeting)
        self._remember(meeting)

    def _open_meeting(self):
        if not self._can_switch():
            return
        self.view.save_all()
        dlg = OpenMeetingDialog(list_meetings(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected_folder():
            return
        try:
            meeting = Meeting.load(dlg.selected_folder())
        except (OSError, ValueError, TypeError) as e:
            QMessageBox.warning(self, tr("open.title"), tr("open.error", error=e))
            return
        self.view.load_meeting(meeting)
        self._remember(meeting)

    # ── Menus ─────────────────────────────────────────────

    def _build_menu(self):
        menu = self.menuBar()
        meeting_menu = menu.addMenu(tr("menu.meeting"))
        self._add_action(meeting_menu, tr("menu.new"), self._new_meeting, QKeySequence.StandardKey.New)
        self._add_action(meeting_menu, tr("menu.open"), self._open_meeting, QKeySequence.StandardKey.Open)
        self._add_action(meeting_menu, tr("menu.open_folder"), self.view.open_folder)
        meeting_menu.addSeparator()
        self._add_action(meeting_menu, tr("menu.record"), self.view.toggle_recording, QKeySequence("Ctrl+R"))
        meeting_menu.addSeparator()
        quit_action = self._add_action(meeting_menu, tr("menu.quit"), self.close, QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)

        tools = menu.addMenu(tr("menu.tools"))
        settings_action = self._add_action(tools, tr("menu.settings"), self._open_settings, QKeySequence("Ctrl+,"))
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)

        help_menu = menu.addMenu(tr("menu.help"))
        self._add_action(help_menu, tr("menu.shortcuts"), self._show_shortcuts, QKeySequence("F1"))
        about = self._add_action(help_menu, tr("menu.about"), self._show_about)
        about.setMenuRole(QAction.MenuRole.AboutRole)

    def _add_action(self, menu, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _open_settings(self):
        dlg = SettingsDialog(self._config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._config = dlg.get_config()
            save_config(self._config)
            self.view.update_config(self._config)

    def _show_shortcuts(self):
        QMessageBox.information(self, tr("menu.shortcuts"), tr("help.shortcuts"))

    def _show_about(self):
        QMessageBox.about(self, tr("menu.about"), tr("help.about", version=__version__))

    def check_first_run(self):
        """Show the setup wizard when no Mistral API key is configured."""
        if self._config.get("api_key"):
            return
        wizard = FirstRunWizard(self._config, self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self._config = wizard.get_config()
            save_config(self._config)
            self.view.update_config(self._config)

    # ── Misc ──────────────────────────────────────────────

    def _update_title(self, pv_title: str):
        self.setWindowTitle(f"{pv_title} — {APP_NAME}")

    def _on_recording_state(self, recording: bool):
        self.setWindowTitle(("● " if recording else "") + self.windowTitle().lstrip("● "))

    def _notify(self, sound_name: str):
        if self._config.get("notification_sounds_enabled", True):
            self._sounds.play(sound_name)
        if not self.isActiveWindow():
            alert_window(self)

    def _restore_geometry(self):
        geometry = self._config.get("window_geometry", "")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        else:
            self.resize(1280, 800)

    def closeEvent(self, event):
        """Confirm when recording, then save and clean up."""
        if self.view.is_recording:
            answer = QMessageBox.question(self, tr("quit.title"), tr("quit.recording"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.view.stop_recording(quiet=True)
        elif self.view.is_busy:
            answer = QMessageBox.question(self, tr("quit.title"), tr("quit.busy"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.view.cleanup()
        self._config["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self._config["last_meeting"] = self.view.meeting.folder
        save_config(self._config)
        event.accept()
