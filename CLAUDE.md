# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PV Writer is a PySide6 desktop app (Windows and macOS) that writes the French "procès-verbal" (minutes) of a meeting. The secretary records the meeting and takes notes in parallel. The app then transcribes the audio with Mistral Voxtral, merges the transcript with the notes and has an LLM (Mistral or a local Claude CLI) write the PV. The PV is exported as an editable Word document (.docx). The UI and the output are in French only.

The recording, transcription and summarization engine is vendored from `../dnd_logger` (commit `bc2bdf1`). The plan is in `plan/implementation_plan.md`.

## Commands

```bash
# Run the app
python main.py              # macOS (or scripts/run.sh)
pythonw main.py             # Windows (or scripts\run.bat) — pythonw, no console window

# Launchers that show the app as "PV Writer" (not "python") in the Dock / taskbar
scripts/make_macos_app.sh   # ~/Applications/PV Writer.app: copied interpreter + pyvenv.cfg reusing .venv
scripts\make_windows_shortcut.ps1  # Start menu/desktop shortcuts carrying the AppUserModelID of main.py

# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Tests (Qt tests run offscreen)
pytest

# Lint
black --check --line-length 120 src/ main.py tests/
isort --check --profile black src/ main.py tests/
flake8 src/ main.py
pylint src/ main.py     # .pylintrc enables only a small subset of checks
```

## Architecture

**Entry point:** `main.py` sets up logging (`pv_writer.log`, rotating), a faulthandler crash log (`pv_writer_crash.log`) and the exception hook. It then sets the app icon (`assets/icons/pv_writer.png`; `.ico` and `.icns` are there for packaged builds), loads `assets/styles/neutral.qss`, shows `MainWindow` and runs the first-run wizard.

**Main window (`src/main_window.py`):** menus, new/open meeting dialogs, settings, notifications, window geometry. It hosts a single `MeetingView`.

**Meeting view (`src/meeting_view.py`):** the recording bar (record / pause / stop / timestamp, VU meter), the info panel on the left (`src/meeting_info_panel.py`), and three tabs: Notes, Transcription, Procès-verbal. It owns the recorder, live transcription, file import (drag & drop, ordering dialog), "save audio as FLAC", PV generation, per-item regeneration and the Word export.

**Meeting storage (`src/meeting.py`):** one folder per meeting under `meetings/` holding `meeting.json` (title, date, organization, agenda, glossary, slides, sources), an `images/` folder, `notes.html`, `transcript.txt`, `extraction.json` (stage A cache), `pv.json`, the audio files and the exported `.docx`.

**Audio pipeline (vendored):** `src/audio_recorder.py` (sounddevice → writer thread → WAV) → `src/transcriber.py` (FLAC conversion, chunking with offsets, French forced, `[hh:mm:ss]` segment timestamps).

**Notes (`src/notes_editor.py`):** a `RichTextEditorWidget` pre-structured by the agenda. While recording, each new paragraph gets a `[hh:mm:ss]` stamp (F2 adds one on demand). `src/notes_merge.py` uses the stamps to interleave the notes with the transcript.

**Agenda (`src/agenda.py`):** parses the indented agenda into a three-level tree. Level 1 is "Concernant … :", level 2 is "1) …" and level 3 is "a) …". An "Autres informations" node is always added.

**PV generation (`src/pv_pipeline.py`, `src/pv_prompts.py`, `src/summarizer.py`):**
- Stage A: extract and classify facts per agenda item as JSON (chunked transcript, side talk dropped). The result is cached in `extraction.json`.
- Stage B: write one HTML fragment per agenda item from its notes and facts. A single item can be regenerated. When the meeting has slides (`Meeting.slides`, a PDF copied as `slides.pdf`), `read_slides()` extracts their text slide by slide and every stage-B prompt gets it, so dates, lists and names from the slides can be reused. Slides that are only an image have no text; they are reported in a warning.
- Stage C (`src/pv_document.py`): add the title, the linked agenda and the numbered headings, and build the preview HTML.
- Stage D (`summarize_pv`): a one-page synthesis (main points and an action table) written from the finished bodies. It is stored as `summary` in `pv.json`, reruns after every generation, and can be regenerated alone (the "Synthèse" entry of the item picker, `SUMMARY_NODE`), which keeps hand-edited bodies.
- `src/summarizer.py` keeps the pluggable backends: Mistral chat, or the Claude CLI via `src/claude_cli.py`. Transcription always uses Mistral.

**Rate limits (`src/rate_limit.py`):** one limiter per Mistral model paces the calls to the key's tokens per minute and requests per second (Settings > Avancé; the defaults are the free tier, 20k TPM for chat). Calls reserve an estimate, which is then corrected with the reported usage. A 429 holds requests for the server's `Retry-After` delay. Stage A sizes its transcript chunks with `extraction_budget()` so a single call always fits the per-minute budget.

**Export (`src/docx_exporter.py`):** python-docx with the style of the reference PVs: blue title, agenda with internal links to bookmarks, horizontal rule, then H1/H2/H3. A4 page, title in the header, "Page X / Y" in the footer. `classify_blocks()` in `src/pv_document.py` tags body blocks by role: a lone bold bullet becomes a topic heading (`PV Sujet`), a bold "… :" paragraph a label (`PV Intitulé`), a "->" line a shaded follow-up box (`PV Suite`). The user edits the document in Word and exports to PDF from there.

**Editing and re-import (`src/pv_import.py`):** the Procès-verbal tab is an editable `QTextEdit`. One second after an edit, `qt_records()` flattens its document and `records_to_pv()` rebuilds the bodies and the synthesis, which are saved to `pv.json`. Headings are matched to the agenda by text, then by position. The title and the agenda come from the meeting info, so edits to them are ignored. "Importer un PV Word corrigé…" (the ⋮ menu) does the same from a .docx, reading the styles `docx_exporter` writes. It first saves the current PV as `pv_avant_import.json`.

**Images:** a body may hold `<p><img src="images/img_<hash>.png" width="N"></p>`. The path is relative to the meeting folder and the width is in px at 96 dpi, at most `IMAGE_MAX_WIDTH`. `store_image()` names the files after their content, so importing a Word file again adds no duplicates. "Insérer une image" (PV tab) adds a page of the slides (thumbnail picker) or an image file under the cursor's paragraph. The Word export centres each picture and fits it to the text column; a missing file shows a "[image manquante]" note. The Word import saves the pictures back to `images/`. A regenerated item keeps its pictures at the end, because the LLM never sees them.

**Config (`src/utils.py`):** `config.json` in `project_root()`. That is the repo root when running from source, `%APPDATA%\PV Writer` on Windows and `~/Library/Application Support/PV Writer` on macOS for frozen builds.

**i18n (`src/i18n/`):** French only. `tr(key, **kwargs)` looks up `src/i18n/fr.py` and is kept so the vendored modules stay unchanged. Every UI string goes through `tr()`.

## Vendored modules

`mistral_compat.py`, `claude_cli.py`, `freeze_watchdog.py`, `audio_order_dialog.py`, `fold_manager.py`, `fold_gutter.py`, `rich_editor.py`, `notifications.py`, `audio_recorder.py`, `transcriber.py` start with a `Vendored from dnd_logger@bc2bdf1 …` header that lists the local changes. Keep those changes minimal and update the header when you change one of these files.

## UI Framework

Uses **PySide6** (Qt for Python). Do NOT use PyQt6 imports.

## Code Style

- **black** with line length 120
- **isort** with black profile
- **Docstrings:** Google format (Args/Returns/Raises)
- **pylint:** only a few checks enabled (see `.pylintrc`) — `missing-function-docstring`, `invalid-name`, `undefined-variable`, `used-before-assignment`, `no-member`, `attribute-defined-outside-init`
- Qt event overrides (`paintEvent`, `closeEvent`, `keyPressEvent`, etc.) are listed in pylint's `good-names` to allow camelCase

## Cross-platform rules

- Build paths with `os.path.join` and never hard-code separators. Open text files with `encoding="utf-8"`.
- Guard OS-specific code with `sys.platform` (`win32` / `darwin`). Examples: the Windows AppUserModelID in `main.py`, `KeepAwake` in `src/power.py` (SetThreadExecutionState / `caffeinate`), the Claude CLI search paths.
- Open files and folders with `QDesktopServices.openUrl`, not `os.startfile`.
- Use Qt standard key sequences (`QKeySequence.StandardKey`) so shortcuts map to Cmd on macOS.
- macOS asks for microphone permission the first time the app records. The permission is given to the terminal (or the app bundle) that launched Python.

## Key Gotchas

- `examples/` holds real reference PVs with private names and is gitignored. Only name-scrubbed excerpts may go into prompts (`STYLE_EXAMPLE` in `src/pv_prompts.py`).
- Qt drops empty `<p></p>` from HTML. `NotesEditor._add_body_blocks()` inserts the empty paragraphs under the headings with a cursor instead.
- Not for ITAR-controlled or classified meetings: audio and text are sent to Mistral (or Anthropic through the Claude CLI).
