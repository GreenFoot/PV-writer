"""Settings dialog and first-run wizard.

Adapted from dnd_logger@bc2bdf1 src/settings.py: the AI, Audio and Prompts tabs, the API
test and the microphone test are kept; Drive, language, themes, cursors and campaign
settings are removed; PV-specific options are added.
"""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import claude_cli, pv_prompts
from .audio_recorder import AudioRecorder
from .i18n import tr
from .mistral_compat import mistral_class
from .summarizer import PROVIDER_CLAUDE_CLI, PROVIDER_MISTRAL
from .utils import save_config


class SettingsDialog(QDialog):
    """Multi-tab settings dialog."""

    # (config key, label key, default factory)
    _PROMPTS = [
        ("prompt_extract", "settings.prompts.extract", pv_prompts.default_extract_system),
        ("prompt_redact", "settings.prompts.redact", pv_prompts.default_redact_system),
    ]

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = dict(config)  # Work on a copy
        self._prompt_texts = {key: self._config.get(key, "") for key, _label, _fn in self._PROMPTS}
        self._current_prompt_index = 0
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumWidth(760)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        # === AI tab ===
        ai_tab = QWidget()
        ai_layout = QVBoxLayout(ai_tab)
        mistral_group = QGroupBox(tr("settings.ai.mistral_group"))
        api_layout = QFormLayout(mistral_group)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(tr("settings.api.key_placeholder"))
        api_layout.addRow(tr("settings.api.key_label"), self.api_key_edit)
        self.btn_test_api = QPushButton(tr("settings.api.btn_test"))
        self.btn_test_api.clicked.connect(self._test_api)
        api_layout.addRow("", self.btn_test_api)
        self.api_status = QLabel("")
        api_layout.addRow("", self.api_status)
        self.summary_model_edit = QLineEdit()
        self.summary_model_edit.setPlaceholderText("mistral-large-latest")
        api_layout.addRow(tr("settings.ai.mistral_model_label"), self.summary_model_edit)
        self.transcription_model_edit = QLineEdit()
        self.transcription_model_edit.setPlaceholderText("voxtral-mini-latest")
        api_layout.addRow(tr("settings.ai.transcription_model_label"), self.transcription_model_edit)
        note = QLabel(tr("settings.ai.transcription_note"))
        note.setObjectName("hint_label")
        note.setWordWrap(True)
        api_layout.addRow("", note)
        ai_layout.addWidget(mistral_group)

        gen_group = QGroupBox(tr("settings.ai.generation_group"))
        gen_layout = QFormLayout(gen_group)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem(tr("settings.ai.provider_mistral"), PROVIDER_MISTRAL)
        self.provider_combo.addItem(tr("settings.ai.provider_claude"), PROVIDER_CLAUDE_CLI)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        gen_layout.addRow(tr("settings.ai.provider_label"), self.provider_combo)
        claude_row = QHBoxLayout()
        self.claude_model_combo = QComboBox()
        self.btn_refresh_claude = QPushButton(tr("settings.ai.btn_refresh_models"))
        self.btn_refresh_claude.clicked.connect(self._refresh_claude_models)
        claude_row.addWidget(self.claude_model_combo, stretch=1)
        claude_row.addWidget(self.btn_refresh_claude)
        gen_layout.addRow(tr("settings.ai.claude_model_label"), claude_row)
        self.claude_status = QLabel("")
        self.claude_status.setWordWrap(True)
        gen_layout.addRow("", self.claude_status)
        ai_layout.addWidget(gen_group)
        ai_layout.addStretch()
        self.tabs.addTab(ai_tab, tr("settings.tab.ai"))

        # === Audio tab ===
        audio_tab = QWidget()
        audio_layout = QFormLayout(audio_tab)
        self.device_combo = QComboBox()
        self.device_combo.addItem(tr("settings.audio.device_default"), None)
        for dev in AudioRecorder.list_devices():
            self.device_combo.addItem(dev["name"], dev["index"])
        audio_layout.addRow(tr("settings.audio.device_label"), self.device_combo)
        self.btn_test_mic = QPushButton(tr("settings.audio.btn_test_mic"))
        self.btn_test_mic.clicked.connect(self._test_mic)
        audio_layout.addRow("", self.btn_test_mic)
        self.sample_rate_spin = QSpinBox()
        self.sample_rate_spin.setRange(8000, 48000)
        self.sample_rate_spin.setSingleStep(8000)
        audio_layout.addRow(tr("settings.audio.sample_rate_label"), self.sample_rate_spin)
        self.tabs.addTab(audio_tab, tr("settings.tab.audio"))

        # === Advanced tab ===
        adv_tab = QWidget()
        adv_layout = QFormLayout(adv_tab)
        self.org_edit = QLineEdit()
        self.org_edit.setPlaceholderText(tr("info.organization_placeholder"))
        adv_layout.addRow(tr("settings.advanced.default_org"), self.org_edit)
        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(10, 300)
        self.chunk_spin.setSuffix(" min")
        adv_layout.addRow(tr("settings.advanced.chunk_label"), self.chunk_spin)
        self.diarize_check = QCheckBox(tr("settings.advanced.diarize"))
        adv_layout.addRow(self.diarize_check)
        self.timestamps_check = QCheckBox(tr("settings.advanced.auto_timestamps"))
        adv_layout.addRow(self.timestamps_check)
        self.sounds_check = QCheckBox(tr("settings.advanced.notification_sounds"))
        adv_layout.addRow(self.sounds_check)
        self.bias_edit = QPlainTextEdit()
        self.bias_edit.setMaximumHeight(120)
        self.bias_edit.setPlaceholderText(tr("settings.advanced.glossary_placeholder"))
        adv_layout.addRow(tr("settings.advanced.glossary_label"), self.bias_edit)

        limits_group = QGroupBox(tr("settings.limits.group"))
        limits_layout = QFormLayout(limits_group)
        limits_hint = QLabel(tr("settings.limits.hint"))
        limits_hint.setObjectName("hint_label")
        limits_hint.setWordWrap(True)
        limits_hint.setOpenExternalLinks(True)
        limits_layout.addRow(limits_hint)
        self.chat_tpm_spin = self._tpm_spin()
        self.chat_rps_spin = self._rps_spin()
        self.tx_tpm_spin = self._tpm_spin()
        self.tx_rps_spin = self._rps_spin()
        limits_layout.addRow(tr("settings.limits.chat_tpm"), self.chat_tpm_spin)
        limits_layout.addRow(tr("settings.limits.chat_rps"), self.chat_rps_spin)
        limits_layout.addRow(tr("settings.limits.transcription_tpm"), self.tx_tpm_spin)
        limits_layout.addRow(tr("settings.limits.transcription_rps"), self.tx_rps_spin)
        adv_layout.addRow(limits_group)
        self.tabs.addTab(adv_tab, tr("settings.tab.advanced"))

        # === Prompts tab ===
        prompts_tab = QWidget()
        prompts_layout = QVBoxLayout(prompts_tab)
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel(tr("settings.prompts.prompt_label")))
        self.prompt_combo = QComboBox()
        for _key, label_key, _fn in self._PROMPTS:
            self.prompt_combo.addItem(tr(label_key))
        selector_row.addWidget(self.prompt_combo, 1)
        self.btn_reset_prompt = QPushButton(tr("settings.prompts.btn_reset"))
        self.btn_reset_prompt.clicked.connect(self._reset_current_prompt)
        selector_row.addWidget(self.btn_reset_prompt)
        prompts_layout.addLayout(selector_row)
        hint = QLabel(tr("settings.prompts.hint"))
        hint.setObjectName("hint_label")
        hint.setWordWrap(True)
        prompts_layout.addWidget(hint)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setMinimumHeight(320)
        self.prompt_edit.textChanged.connect(self._on_prompt_text_changed)
        prompts_layout.addWidget(self.prompt_edit)
        self.prompt_combo.currentIndexChanged.connect(self._on_prompt_selected)
        self.tabs.addTab(prompts_tab, tr("settings.tab.prompts"))

        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _tpm_spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 10_000_000)
        spin.setSingleStep(1000)
        spin.setGroupSeparatorShown(True)
        spin.setSpecialValueText(tr("settings.limits.unlimited"))
        return spin

    @staticmethod
    def _rps_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 100.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSpecialValueText(tr("settings.limits.unlimited"))
        return spin

    def _populate(self):
        cfg = self._config
        self.api_key_edit.setText(cfg.get("api_key", ""))
        self.summary_model_edit.setText(cfg.get("summary_model", "mistral-large-latest"))
        self.transcription_model_edit.setText(cfg.get("transcription_model", "voxtral-mini-latest"))
        idx = self.provider_combo.findData(cfg.get("summary_provider", PROVIDER_MISTRAL))
        self.provider_combo.setCurrentIndex(max(idx, 0))
        self._refresh_claude_models()
        self._on_provider_changed()
        idx = self.device_combo.findData(cfg.get("audio_device"))
        self.device_combo.setCurrentIndex(max(idx, 0))
        self.sample_rate_spin.setValue(cfg.get("sample_rate", 16000))
        self.org_edit.setText(cfg.get("default_organization", ""))
        self.chunk_spin.setValue(cfg.get("chunk_duration_minutes", 60))
        self.diarize_check.setChecked(cfg.get("diarize", False))
        self.timestamps_check.setChecked(cfg.get("auto_timestamp_notes", True))
        self.sounds_check.setChecked(cfg.get("notification_sounds_enabled", True))
        self.bias_edit.setPlainText("\n".join(cfg.get("context_bias", [])))
        self.chat_tpm_spin.setValue(cfg.get("rate_limit_chat_tpm", 20000))
        self.chat_rps_spin.setValue(cfg.get("rate_limit_chat_rps", 0.8))
        self.tx_tpm_spin.setValue(cfg.get("rate_limit_transcription_tpm", 50000))
        self.tx_rps_spin.setValue(cfg.get("rate_limit_transcription_rps", 1.0))
        self._on_prompt_selected(0)

    def _save_and_accept(self):
        self._store_current_prompt()
        cfg = self._config
        cfg["api_key"] = self.api_key_edit.text().strip()
        cfg["summary_model"] = self.summary_model_edit.text().strip() or "mistral-large-latest"
        cfg["transcription_model"] = self.transcription_model_edit.text().strip() or "voxtral-mini-latest"
        cfg["summary_provider"] = self.provider_combo.currentData()
        cfg["claude_model"] = self.claude_model_combo.currentData() or ""
        cfg["audio_device"] = self.device_combo.currentData()
        cfg["sample_rate"] = self.sample_rate_spin.value()
        cfg["default_organization"] = self.org_edit.text().strip()
        cfg["chunk_duration_minutes"] = self.chunk_spin.value()
        cfg["diarize"] = self.diarize_check.isChecked()
        cfg["auto_timestamp_notes"] = self.timestamps_check.isChecked()
        cfg["notification_sounds_enabled"] = self.sounds_check.isChecked()
        cfg["rate_limit_chat_tpm"] = self.chat_tpm_spin.value()
        cfg["rate_limit_chat_rps"] = self.chat_rps_spin.value()
        cfg["rate_limit_transcription_tpm"] = self.tx_tpm_spin.value()
        cfg["rate_limit_transcription_rps"] = self.tx_rps_spin.value()
        cfg["context_bias"] = [line.strip() for line in self.bias_edit.toPlainText().splitlines() if line.strip()]
        for key, _label, _fn in self._PROMPTS:
            cfg[key] = self._prompt_texts.get(key, "")
        self.accept()

    def get_config(self) -> dict:
        """Return the edited configuration dict."""
        return self._config

    # ── Prompts (ported) ──────────────────────────────────

    def _on_prompt_selected(self, index: int):
        """Switch the editor to the selected prompt."""
        self._store_current_prompt()
        self._current_prompt_index = index
        key, _label, default_fn = self._PROMPTS[index]
        custom = self._prompt_texts.get(key, "")
        self.prompt_edit.blockSignals(True)
        self.prompt_edit.setPlainText(custom if custom else default_fn())
        self.prompt_edit.blockSignals(False)
        self.btn_reset_prompt.setEnabled(bool(custom))

    def _on_prompt_text_changed(self):
        """Enable the reset button when the editor text differs from default."""
        _key, _label, default_fn = self._PROMPTS[self._current_prompt_index]
        self.btn_reset_prompt.setEnabled(self.prompt_edit.toPlainText().strip() != default_fn().strip())

    def _store_current_prompt(self):
        """Save the editor text back to the internal dict (empty = default)."""
        if not hasattr(self, "prompt_edit"):
            return
        key, _label, default_fn = self._PROMPTS[self._current_prompt_index]
        text = self.prompt_edit.toPlainText().strip()
        if not text:
            return
        self._prompt_texts[key] = "" if text == default_fn().strip() else text

    def _reset_current_prompt(self):
        """Reset the current prompt to its built-in default."""
        key, _label, default_fn = self._PROMPTS[self._current_prompt_index]
        self.prompt_edit.setPlainText(default_fn())
        self._prompt_texts[key] = ""
        self.btn_reset_prompt.setEnabled(False)

    # ── AI backend (ported) ───────────────────────────────

    def _refresh_claude_models(self, _checked: bool = False):
        """Detect the Claude CLI and repopulate the model list."""
        current = self.claude_model_combo.currentData() or self._config.get("claude_model", "")
        self.claude_model_combo.clear()
        if not claude_cli.is_available(refresh=True):
            self.claude_status.setText(tr("settings.ai.claude_not_found"))
            self.claude_model_combo.setEnabled(False)
            if current:
                self.claude_model_combo.addItem(current, current)
            return
        self.claude_model_combo.setEnabled(True)
        for name in claude_cli.list_models():
            self.claude_model_combo.addItem(name, name)
        if current:
            idx = self.claude_model_combo.findData(current)
            if idx < 0:
                self.claude_model_combo.insertItem(0, current, current)
                idx = 0
            self.claude_model_combo.setCurrentIndex(idx)
        self.claude_status.setText(tr("settings.ai.claude_detected", version=claude_cli.get_version() or "?"))

    def _on_provider_changed(self, _index: int = 0):
        """Enable the Claude model picker only for the Claude CLI provider."""
        uses_claude = self.provider_combo.currentData() == PROVIDER_CLAUDE_CLI
        self.claude_model_combo.setEnabled(uses_claude and claude_cli.is_available())
        self.btn_refresh_claude.setEnabled(uses_claude)

    def _test_api(self):
        """Test the Mistral API connection."""
        key = self.api_key_edit.text().strip()
        if not key:
            self.api_status.setText(tr("settings.api.enter_key_first"))
            return
        try:
            with mistral_class()(api_key=key) as client:
                client.models.list()
            self.api_status.setText(tr("settings.api.test_success"))
        except Exception as e:
            self.api_status.setText(tr("settings.api.test_fail", error=e))

    def _test_mic(self):
        """Record one second and report the level."""
        try:
            import numpy as np
            import sounddevice as sd

            dev = self.device_combo.currentData()
            sr = self.sample_rate_spin.value()
            data = sd.rec(int(sr * 1), samplerate=sr, channels=1, dtype="int16", device=dev)
            sd.wait()
            rms = np.sqrt(np.mean(data.astype(np.float32) ** 2))
            if rms > 100:
                QMessageBox.information(
                    self, tr("settings.audio.test_title"), tr("settings.audio.test_ok", level=f"{rms:.0f}")
                )
            else:
                QMessageBox.warning(self, tr("settings.audio.test_title"), tr("settings.audio.test_weak"))
        except Exception as e:
            QMessageBox.critical(self, tr("settings.audio.test_title"), tr("settings.audio.test_error", error=e))


class FirstRunWizard(QDialog):
    """Shown on first launch when no API key is configured."""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle(tr("wizard.title"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        title = QLabel(tr("wizard.welcome"))
        title.setObjectName("panel_heading")
        layout.addWidget(title)
        subtitle = QLabel(tr("wizard.subtitle"))
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        api_group = QGroupBox(tr("wizard.api_group"))
        api_layout = QFormLayout(api_group)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(tr("settings.api.key_placeholder"))
        api_layout.addRow(self.api_key_edit)
        api_hint = QLabel(tr("wizard.api_hint"))
        api_hint.setObjectName("hint_label")
        api_hint.setWordWrap(True)
        api_layout.addRow(api_hint)
        layout.addWidget(api_group)

        audio_group = QGroupBox(tr("wizard.mic_group"))
        audio_layout = QFormLayout(audio_group)
        self.device_combo = QComboBox()
        self.device_combo.addItem(tr("settings.audio.device_default"), None)
        for dev in AudioRecorder.list_devices():
            self.device_combo.addItem(dev["name"], dev["index"])
        audio_layout.addRow(tr("settings.audio.device_label"), self.device_combo)
        layout.addWidget(audio_group)

        notice = QLabel(tr("wizard.data_notice"))
        notice.setObjectName("hint_label")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        buttons = QHBoxLayout()
        btn_skip = QPushButton(tr("wizard.btn_skip"))
        btn_done = QPushButton(tr("wizard.btn_done"))
        btn_done.setObjectName("btn_primary")
        buttons.addWidget(btn_skip)
        buttons.addStretch()
        buttons.addWidget(btn_done)
        layout.addLayout(buttons)
        btn_skip.clicked.connect(self.reject)
        btn_done.clicked.connect(self._finish)

    def _finish(self):
        key = self.api_key_edit.text().strip()
        if key:
            self._config["api_key"] = key
        self._config["audio_device"] = self.device_combo.currentData()
        save_config(self._config)
        self.accept()

    def get_config(self) -> dict:
        """Return the updated configuration dict."""
        return self._config
