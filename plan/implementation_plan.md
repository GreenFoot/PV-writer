# PV Writer — Implementation Plan (rev. 3)

Source project: `../dnd_logger` @ `bc2bdf1` (PySide6 desktop app, Mistral Voxtral + chat, optional Claude CLI).
Target: a French-only, **Windows + macOS** desktop app that records a meeting, lets the secretary take notes in
parallel, transcribes the audio, and produces a **Procès-Verbal (PV)** as an editable document (Google Docs by
default) that ends up as a PDF.

Reference output: the three PDFs at the repo root (`2025xxxx_PV Réunion des délégués.pdf`).

### Changes since rev. 1 (decisions taken)

| Topic | Decision |
|---|---|
| OS | Multi-platform: Windows **and** macOS. |
| Output | Editable document that can be reopened, then exported as PDF. **Google Docs** is the main output. No Aerospacelab/AMOS template: the style comes from the reference PVs. |
| Campaigns | No campaign or meeting-series concept. One meeting = one standalone folder, reopenable. |
| AI backends | Mistral **and** the Claude CLI are both kept. |
| Updates / CI | No auto-updater, no release pipeline, no installer. |
| Data governance | Confirmed OK. |
| **rev. 3** — Output (v1) | **Offline Word (.docx)** generated locally with `python-docx`. Edit it in Word/LibreOffice, then export the PDF from there. The Google Docs publishing (§7) is **postponed** (no Google Cloud setup for now). |
| **rev. 3** — Style anchors | Name-scrubbed excerpts of the reference PVs may be embedded in the default prompts. |
| **rev. 3** — Packaging | Run from source first (venv + launch script per OS). The PyInstaller spec comes last. |

---

## 1. What the reference PVs tell us (target spec)

**Format** (all three are Google Docs exports with the default Docs styles):

- **Title**: `PV <Nom de la réunion> du JJ/MM/AAAA` (Docs "Title" style, blue).
- **`Agenda :`** (H1) is a 2- or 3-level bullet list (● / ○ / ■) whose entries are **internal links** to the sections.
  A horizontal rule follows it.
- **Section levels**:
  - H1 is a thematic group ending with a colon: `Concernant les écoles :`, `Concernant l'ADP :`,
    `Concernant le CP - conseil de participation :`, `Autres informations :`.
  - H2 is a numbered agenda item: `1) École A`, `2) Point financier`.
  - H3 is a lettered sub-item: `a) St Nicolas`, `b) Fête d'hiver`.
  - Inside an item, sub-topics are **bold bullets** (`● Retour cantine`) followed by plain paragraphs, or nested
    bullet lists.
  - "Autres informations" is a flat bullet list of miscellaneous points.
- **Elements added by hand** that the app cannot produce: financial tables (screenshots or tables), photos and
  flyers, red bold call-outs, links to Drive or Facebook. This is why the output must be an editable document.

**Voice and tone**:

- The organizing body speaks in the **first person plural**: "Nous avons financé…", "Nous allons demander à Mme
  Martin…". Other people appear in reported speech, third person: "Mme Martin nous informe que…", "Certains parents
  demandent…", "Paul indique qu'il est prêt à…".
- The French is semi-formal and natural, not legal boilerplate. The present and future tenses dominate.
- Recurring patterns:
  - `Pour rappel, …` / `Pour information, …`
  - `Réaction des parents :` / `Réponse de l'ADP :` / `Réponse du corps enseignant présent :`
  - follow-ups written as `-> L'ADP va le relancer au CP`
  - votes recorded explicitly ("Les 2 ont été votés POUR à l'unanimité")
  - thanks are always kept ("Remerciements de Mme Claire : …")

**Level of detail**: these are **detailed minutes, not summaries** (5–15 pages for a 1.5–2 h meeting). Every
substantive point is kept, with exact figures, dates, amounts, names and who-asked-what, plus the argument and the
counter-argument ("Avocat du diable : …"). What gets dropped is side talk, jokes and logistics chatter. There is no
attendance list and no formal actions table: the follow-ups are written inline with `->`.

Consequences for the design:

1. The PV is **long**, so generating it in a single LLM call would hit output limits and lose fidelity. It is
   generated **section by section**, then assembled (§5).
2. The **agenda tree** drives the whole document (title, TOC, sections). It is a first-class input.
3. The title, TOC, numbering and heading styles are produced **deterministically by code**. The LLM only writes
   section bodies.
4. "Nous" = the **organizing body**, which is configurable per meeting (e.g. "ADP").

---

## 2. Product scope

1. **Prepare**: meeting name, date, organizing body ("nous"), the agenda tree (typed or pasted as an indented list),
   and an optional glossary or list of names (for spelling and STT bias). Optionally, the previous PV (PDF or text)
   so that "Pour rappel" points can be resolved.
2. **Record**, as in dnd_logger: pause/resume, VU meter, live transcription on silences.
3. **Take notes in parallel**: the notes editor is **pre-filled with the agenda headings**. The secretary types under
   the item being discussed. Every paragraph typed while recording gets a timestamp `[hh:mm:ss]`, so notes are
   classified by agenda item *and* placed in time.
4. **Transcribe**: live, or in batch afterwards. Import audio and/or existing transcripts, ordered with the existing
   dialog (the audio + transcript combination is kept).
5. **Generate the PV**: the notes are authoritative and the transcript adds detail. Off-topic content is filtered
   out. Output in the reference style.
6. **Publish and edit**: one click creates a **Google Doc** in the user's Drive and opens it in the browser. The
   secretary adds images and tables and corrects the text there. **PDF**: export from Google Docs, or the in-app
   button "Télécharger le PDF" (Drive export of the edited doc).
7. **Reopen** any past meeting: notes, transcript, regenerate (as a new doc version), reopen the Google Doc, export
   the PDF again.

Non-goals: web viewer, Drive *sync*, TTS, themes, multi-language UI, auto-update, installer, CI release.

---

## 3. Keep / adapt / drop: module inventory

Legend: **VENDOR** = copied verbatim (only the logger name, string keys and a provenance header change) ·
**ADAPT** = targeted modification of the dnd_logger file · **REWRITE** = new code, which may borrow snippets ·
**DROP**.

### 3.1 Engine

| dnd_logger file | Verdict | Notes |
|---|---|---|
| `mistral_compat.py` | **VENDOR** | mistralai 1.x/2.x import shim (result of PRs #40–#44). |
| `claude_cli.py` | **VENDOR** | Already cross-platform (Windows-only kwargs are guarded, candidate paths include `/usr/local/bin` and `~/.local/bin`). **Add** `/opt/homebrew/bin` to the candidate paths for macOS. |
| `audio_recorder.py` | **VENDOR + ADAPT** | Streaming recorder, silence detection, pending buffer. **Add** a running sample counter so `take_pending_audio()` returns the chunk **start offset**. **Replace** `_keep_screen_awake` (Windows `SetThreadExecutionState`) with a small cross-platform helper in `power.py`: Windows API on win32, `caffeinate -dimsu -w <pid>` subprocess on macOS. |
| `transcriber.py` | **VENDOR + ADAPT** | Chunker, Voxtral upload, 429 retry, STT-loop artifact stripping, mixed audio and transcript batches, live worker. **Changes**: `language="fr"` forced. Segment timestamps are always requested and lines prefixed `[hh:mm:ss]` with chunk/file offsets (**spike first**: check that the API accepts `timestamp_granularities` without `diarize`). Context bias comes from the glossary. |
| `summarizer.py` | **ADAPT** | Keep `_MistralBackend`, `_ClaudeCliBackend`, `build_backend`, `_call_with_retry`, `_strip_code_fences`, the worker/thread pattern and the chunked condense loop. **Replace** the D&D prompt resolution and the single-shot summary with the 3-stage PV pipeline (§5). |
| `freeze_watchdog.py` | **VENDOR** | Log file renamed `pv_writer_stalls.log`. |
| `notifications.py` | **VENDOR + ADAPT** | Lazy QSoundEffect is kept. `flash_taskbar` (Win32 ctypes) is replaced by `QApplication.alert(window)`, which is cross-platform (taskbar flash on Windows, Dock bounce on macOS). |
| `audio_order_dialog.py` | **VENDOR** | Only the button object names change. |
| `drive_auth.py` | **VENDOR + ADAPT** | OAuth installed-app flow in a QThread with token persistence. **Changes**: scope `drive.file` only (the app sees only the files it creates). The client config is loaded from a user-supplied `client_secret.json` (path set in Settings) instead of the gitignored `drive_credentials.py` that CI used to inject. |
| `drive_sync.py` | **DROP**, but borrow ~20 lines | Only the `files().create(... MediaFileUpload ...)` pattern is reused in the new `gdocs_publisher.py`. Polling, conflict handling and sync state are not needed. |

### 3.2 Infrastructure

| dnd_logger file | Verdict | Notes |
|---|---|---|
| `main.py` | **ADAPT** | Keep the rotating log, `faulthandler` crash log, exception hook and app icon. **Drop** the Windows AppUserModelID (keep it, guarded, for win32 only), the QtWebEngine env vars and the browser-settle splash. |
| `utils.py` | **ADAPT** | Keep `resource_path`, `ensure_dir`, `format_duration`, `format_file_size` and config load/save. **Drop** campaigns and the shared/personal split. **Data dir**: `QStandardPaths.AppDataLocation` (gives `%APPDATA%\PV Writer` on Windows, `~/Library/Application Support/PV Writer` on macOS); the repo root in dev, as today. **Add** `meetings_dir()` / `meeting_dir(id)`. |
| `i18n/` | **ADAPT** | Keep `tr()` so vendored modules are untouched. Only `fr.py` remains. Remove `set_language` and the other 6 languages. |
| `settings.py` | **ADAPT** | Tabs: **IA** (Mistral key + test, provider Mistral/Claude CLI, models), **Audio** (device, sample rate, mic test), **Google** (client_secret.json path, connect/disconnect, target Drive folder), **Avancé** (chunk duration, diarization, auto-timestamp notes, notification sounds), **Prompts** (editable, reset to default). Drop the language, themes, cursors, recap and campaign-Drive settings. `FirstRunWizard`: Mistral key + audio device; Google is optional and done later. |
| `rich_editor.py`, `fold_manager.py`, `fold_gutter.py` | **VENDOR + small ADAPT** | Toolbar, Ctrl+F search, 2 s autosave with `.bak`, heading folding (useful with the agenda headings in the notes). **Remove** the TTS hooks. |
| `.pylintrc`, lint config | **VENDOR** | Same standards (§8). |
| `dnd_logger.spec` | **ADAPT (optional, local only)** | One spec file, branching on the platform: `--onedir` on Windows, `.app` bundle on macOS with `NSMicrophoneUsageDescription` in `info_plist` (mandatory, or macOS silently delivers zeros from the mic). No CI. |

### 3.3 UI

| dnd_logger file | Verdict | What is reused |
|---|---|---|
| `app.py` | **REWRITE** → `main_window.py` | Borrow: notification wiring, geometry save/restore, settings open/refresh, `closeEvent` cleanup, first-run check. |
| `session_tab.py` | **REWRITE UI, PORT LOGIC** → `meeting_controller.py` + `meeting_view.py` | Port nearly verbatim: `_track_thread`/`_retire_thread` (critical Qt-abort fix), the live transcription state machine, import + drag-and-drop + `_unique_dest`, `_save_audio`, `PostRecordingDialog`, `_cleanup_flac_files`, error reset, the past-session picker (now "Ouvrir une réunion"). The bookmarks become timestamped notes (F2 kept). `_inject_bookmarks_proportional` is generalized as the fallback in `notes_merge.py`. |

### 3.4 Dropped entirely

`web_panel.py`, `drive_sync.py` (beyond the borrowed snippet), `sync_conflict_dialog.py`, `quest_extractor.py`,
`quest_log.py`, `journal.py`, `campaign_assistant.py`, `session_recap_overlay.py`, `tts_engine.py`, `tts_overlay.py`,
`snow_particles.py`, `filigree_overlay.py`, `themed_cursor.py`, `shortcuts_overlay.py`, `themed_dialogs.py`,
`diff_utils.py`, `updater.py`, `setup_appdata.py`, `installer.iss`, `.github/workflows/release.yml`, the 6 non-French
i18n tables, and all fantasy assets (fonts, backgrounds, textures, cursors, tab icons, 7 QSS themes,
`theme_meta.json`).

**Dependencies.**

- Kept: `PySide6`, `sounddevice`, `soundfile`, `numpy`, `mistralai>=2.4.5,<3`, `google-api-python-client`,
  `google-auth-oauthlib`, `google-auth-httplib2`.
- Dropped: `edge_tts`, `requests` (it only served the updater), QtWebEngine.
- Added: `pypdf` (read a previous PV given as a PDF). `python-docx` only if the offline DOCX fallback is retained
  (§9).

---

## 4. Target structure

```
PV-writer/
├── main.py
├── requirements.txt / requirements-dev.txt
├── pyproject.toml               # black/isort/pytest config
├── .pylintrc / .flake8
├── CLAUDE.md / README.md
├── pv_writer.spec               # optional local build (Win onedir / macOS .app)
├── examples/                    # reference PVs (gitignored: they contain private names)
├── assets/{images/app, sounds, styles/neutral.qss}
├── src/
│   ├── __init__.py
│   ├── i18n/{__init__.py, fr.py}
│   ├── utils.py  power.py                         # power.py = keep-awake (win/mac)
│   ├── mistral_compat.py  claude_cli.py           # VENDOR
│   ├── audio_recorder.py  transcriber.py          # VENDOR+
│   ├── summarizer.py                              # ADAPT: backends + workers
│   ├── pv_pipeline.py                             # NEW: stages A/B/C orchestration
│   ├── pv_prompts.py                              # NEW: prompt assembly
│   ├── pv_document.py                             # NEW: agenda tree + deterministic HTML assembly
│   ├── notes_merge.py                             # NEW: notes ↔ transcript alignment (pure)
│   ├── meeting.py                                 # NEW: Meeting dataclass + meeting.json
│   ├── drive_auth.py                              # VENDOR+ (scope drive.file)
│   ├── gdocs_publisher.py                         # NEW: HTML → Google Doc, PDF export
│   ├── freeze_watchdog.py  notifications.py  audio_order_dialog.py
│   ├── rich_editor.py  fold_manager.py  fold_gutter.py
│   ├── notes_editor.py  meeting_info_panel.py  agenda_editor.py
│   ├── meeting_controller.py  meeting_view.py  main_window.py  settings.py
└── tests/
```

Each vendored file keeps a header: `# Vendored from dnd_logger@bc2bdf1 src/<file> — keep in sync manually.`

Per-meeting folder `<data>/meetings/AAAAMMJJ_HHMM_<slug>/`:

- `meeting.json`: metadata, agenda tree, and the list of published Google Doc versions (id, URL, date)
- `recording.wav`, `notes.html` (+`.bak`), `transcript.txt`
- `extraction.json`: stage A output, cached so that regenerating doesn't re-run it
- `pv.html`: the last generated draft
- `pv_vN.pdf`: downloaded exports

---

## 5. PV generation pipeline

```
agenda tree ─────────────┐
notes (per agenda item,  │
       timestamped)      ├─► A. EXTRACT & CLASSIFY (per ~40k-char transcript chunk, temperature 0, JSON out)
merged transcript ───────┘      → facts [{agenda_id, sub_topic, kind, content, speaker_if_explicit, t}]
                                   kind ∈ info|decision|vote|follow_up|question|reaction|thanks|recall
                                   off-topic / jokes / logistics / STT artifacts → dropped here
previous PV (optional) ──► used only to resolve "Pour rappel" facts

   ─► B. REDACT per agenda item (one call per H2/H3 item: its notes + its facts + style guide)
        → HTML body fragment in the reference style (bold-bullet sub-topics, paragraphs, nested lists, "->")
   ─► C. ASSEMBLE in code (pv_document.py)
        → Title, "Agenda :" linked TOC, <hr>, H1 groups, numbered H2 "1) …", lettered H3 "a) …", fragments
        → pv.html (Google-Docs-friendly HTML with inline heading styles)
```

**Why 3 stages.** Stage A reuses dnd_logger's chunked condense loop, but returns classified facts instead of
prose. That makes the off-topic filter explicit and auditable. Stage B keeps each call short, so the output is
detailed without output-token truncation, and a single section can be regenerated on its own. Stage C guarantees
the exact heading, numbering and TOC conventions.

**Prompt principles** (French, editable in Settings > Prompts, derived from the reference PVs):

- **Source hierarchy**: notes > transcript. Every note must appear. Transcript-only content goes in only if it is
  classified under an agenda item. Unclassifiable but substantive points go to "Autres informations".
- **Fidelity** (carried over from the dnd_logger prompts): invent nothing, omit rather than guess, keep figures,
  dates and amounts exactly, and no STT-artifact names.
- **Attribution**: the reference PVs *do* attribute ("Mme Martin nous informe…"). A name is used only when it is
  explicit in the notes or in the transcript (self-introduction, someone addressed by name). Diarization labels are
  never mapped to names by guess. Otherwise, use group wording: "Certains parents", "Le corps enseignant présent".
- **Canonical names**: the glossary and the previous PV give the authoritative spellings (dnd_logger's "NOMS
  CANONIQUES" rule).
- **Voice**: "nous" = the organizing body. Others in reported speech. Present and future tenses. Patterns `Pour
  rappel`, `Pour information`, `Réaction des … :`, `Réponse de … :`, `-> <follow-up>`.
- **Level of detail**: detailed minutes. Keep arguments and counter-arguments, votes and thanks. Drop jokes, side
  talk and process chatter ("tu m'entends ?", "on passe au point suivant").
- **Style anchors**: short excerpts of the reference PVs with names replaced, used as few-shot examples (pending
  your OK, §9).
- **Output**: an HTML subset only (`p`, `ul/li`, `strong`, `em`). Headings are excluded because stage C owns them.
  `_strip_code_fences` is kept.

**Inputs that also work**: notes only (no audio), audio only (no notes; the agenda is still required to classify),
and imported transcript files.

---

## 6. Notes ↔ transcript merge

1. **Notes**: the editor is pre-filled with the agenda as headings. While recording, each new paragraph gets a grey
   `[hh:mm:ss]` prefix (it can be turned off in Settings). F2 inserts one manually. Parsing: headings give the
   `agenda_id`, the regex gives the time.
2. **Transcript times**:
   - *Live*: chunk start offset (new recorder counter) + Voxtral segment start.
   - *Batch*: chunk index × (chunk length − 10 s overlap) + segment start. For multi-file imports, the offset is the
     cumulative duration of the previous files.
   - *Imported `.txt` transcripts*: inserted as a block, without timestamps.
3. **Merge** (`notes_merge.py`, pure and unit-tested): notes are interleaved as `[NOTE hh:mm:ss | <agenda item>] …`
   blocks. This gives stage A a strong hint: the transcript around a note very likely belongs to the same agenda
   item. Fallback when timestamps are missing: proportional placement (the dnd_logger algorithm, generalized).

---

## 7. Output: Word (v1) → PDF; Google Docs (later)

**v1, Word (`docx_exporter.py`)**: built directly from the structured PV produced by stage C (agenda tree + HTML
fragments), not by parsing a full HTML page.

- Styles: Title / Heading 1–3 coloured like the reference PVs, `List Bullet` levels 1–3, bold-bullet sub-topics.
- The agenda entries are real internal hyperlinks to Word bookmarks placed on the section headings.
- The fragments' small HTML subset (`p`, `ul/ol/li`, `strong/b`, `em/i`, `u`, `br`) is mapped to runs.
- The file is saved as `pv_vN.docx` in the meeting folder and opened with the default app (`QDesktopServices`).
  The user adds images and tables there and exports the PDF.

The Google Docs section below is kept for a later phase.

**Main path, Google Docs** (`gdocs_publisher.py`, runs in a QThread like every other worker):

- Upload `pv.html` with `files().create(body={name, mimeType: "application/vnd.google-apps.document", parents:
  [folder]}, media_body=<html>)`. Drive converts HTML to a native Google Doc and keeps headings, lists, bold, links
  and colours. This is a single API call and needs no Docs API `batchUpdate` choreography.
- Store the doc id and URL in `meeting.json` and open the URL in the default browser (`QDesktopServices`).
- The secretary edits in Google Docs (images, tables, corrections).
- **PDF**: either *Fichier > Télécharger > PDF* in Docs, or the in-app "Télécharger le PDF" button, which calls
  `files().export(fileId, mimeType="application/pdf")` and saves `pv_vN.pdf`. This works with the `drive.file`
  scope because the app created the file.
- **Regenerating** never overwrites an edited doc: it creates `PV … (v2)` and keeps both links.

**Spikes to run first** (half a day): check how the HTML→Docs conversion handles (a) inline heading colours and
fonts, (b) **internal anchor links** for the agenda TOC (fallback: plain agenda list, plus a note to insert the Docs
TOC in one click, or a Docs API pass that links the agenda bullets to the heading ids), and (c) nested list levels
(● ○ ■).

**Google setup (one-off, cross-platform)**: a Google Cloud project with an OAuth client of type *Desktop app* and
scope `drive.file`. The user points Settings to the `client_secret.json`. ⚠️ While the OAuth consent screen is in
*Testing* status, refresh tokens expire **after 7 days** (weekly re-login). Moving the consent screen to *In
production* is free for the non-sensitive `drive.file` scope and avoids this.

**Offline fallback** (optional, §9): export `pv.html` → `.docx` locally with `python-docx`. The file opens in Word,
LibreOffice or Google Docs and can be exported to PDF there.

**In-app view**: the generated `pv.html` is shown read-only in the PV tab (preview plus a "Régénérer cette section"
action per agenda item). Final editing happens in Google Docs, not in the app, so we don't rebuild a word processor.

---

## 8. Cross-platform and engineering standards

**Cross-platform checklist**:

| Concern | Windows | macOS |
|---|---|---|
| Launch (dev) | `pythonw main.py` (dnd_logger rule) | `python main.py` |
| Data dir | `%APPDATA%\PV Writer` | `~/Library/Application Support/PV Writer` (both via `QStandardPaths`) |
| Keep awake while recording | `SetThreadExecutionState` | `caffeinate -dimsu -w <pid>` |
| Attention on completion | `QApplication.alert` | `QApplication.alert` (Dock bounce) |
| Mic permission | — | System prompt. For a bundled `.app`, `NSMicrophoneUsageDescription` is mandatory. In dev, the Terminal/IDE needs mic access |
| Claude CLI lookup | existing paths | plus `/opt/homebrew/bin` |
| Fonts/QSS | system font in a neutral QSS, no bundled fonts | same |
| Process kwargs | `CREATE_NO_WINDOW` (already guarded) | none |

**Standards inherited from dnd_logger's CLAUDE.md**:

- **PySide6 only**, never PyQt.
- **black** line length 120, **isort** profile black, **flake8**, **pylint** with the same restricted `.pylintrc`.
- **Google-style docstrings**.
- Long work runs in `QObject` workers moved to a `QThread` through `start_*()` factories. Threads are tracked until
  `finished`. No encoding or network calls on the GUI thread.
- Logging under the `pvwriter.*` namespace, rotating file, `faulthandler` crash log.

**Additions**:

- `pytest` for the pure logic: STT artifact stripping, chunker, timestamp offsets, `notes_merge`, agenda parsing and
  numbering, stage C HTML assembly, the stage A JSON parser (robust to malformed model output) and config
  load/save.
- A `CLAUDE.md` for PV-writer from phase 0, written in the dnd_logger format, with the cross-platform rules added.
- The reference PVs move to `examples/`, which is gitignored (they contain names of private individuals).

---

## 9. Remaining open questions

1. **Google Cloud OAuth client**: do you already have a Google Cloud project where we can create the *Desktop* OAuth
   client (and move the consent screen to *In production* to avoid the 7-day token expiry)? Or should the README
   document the setup step by step for each user?
2. **Offline DOCX fallback**: in v1, or Google Docs only?
3. **Few-shot style anchors**: may short, **name-scrubbed** excerpts of the 3 reference PVs be embedded in the
   default prompt? Alternatively, they would only guide my writing of the style rules.
4. **Packaging**: run from source (a venv plus a launch script per OS) or local PyInstaller builds (`.exe` folder /
   `.app`)? I suggest from source first and the spec in the last phase.

---

## 10. Phased delivery

| Phase | Content | Exit criterion |
|---|---|---|
| **0. Scaffold** | Layout, `pyproject.toml`, lint config, requirements, `CLAUDE.md`, README skeleton. Copy the VENDOR files with headers. FR-only i18n. `examples/` gitignored. | Lint clean, an empty window opens on Win + macOS. |
| **1. Spikes** | (a) Voxtral segment timestamps without diarize. (b) Drive HTML→Doc conversion: heading styles, anchors, list levels. (c) macOS mic and keep-awake. | Findings recorded in `plan/`, design adjusted if needed. |
| **2. Engine port** | `utils`/`power`, recorder (+offset), transcriber (+FR, +timestamps), backends, watchdog, notifications. | Headless: record 30 s → timestamped transcript on both OSes. Tests green. |
| **3. Meeting model + UI shell** | `meeting.py`, info panel, agenda editor (paste an indented list), notes editor (agenda skeleton, timestamps, autosave), main window, neutral QSS, settings, wizard. | Create a meeting, take notes while recording, quit, reopen: nothing lost. |
| **4. Pipeline wiring** | Port the session_tab logic: live/batch transcription, import + ordering + drag-drop, save audio, post-recording dialog, open a past meeting. | Every dnd_logger audio feature works end to end. |
| **5. PV pipeline** | `notes_merge`, stages A/B/C, prompts, PV preview tab, per-section regeneration. | PV generated from notes only, from audio only, and from both. |
| **6. Word export** | `docx_exporter` (styles, linked agenda, bookmarks, lists, versioning). | One click gives a .docx that looks like the reference PVs and opens in Word/LibreOffice. |
| *6b. Google Docs (postponed)* | Vendored `drive_auth`, `gdocs_publisher`. | Pending a Google Cloud OAuth client. |
| **7. Calibration** | Tune the prompts on real meetings against the reference PVs (structure, voice, detail, filtering). | You judge 2–3 real meetings acceptable. |
| **8. Packaging (optional)** | Launch scripts, and/or the `pv_writer.spec` for Win + macOS. | Runs on a clean machine of each OS. |

---

## 11. Implementation status and deviations (2026-10-16)

Phases 0 and 2–6 are implemented and run from source. The app starts, lint is clean (black, isort, flake8,
pylint 10/10), and 26 tests pass, including offscreen tests of the notes editor. Launch scripts: `scripts/run.sh` and
`scripts/run.bat`.

Not done yet:
- Phase 1 spikes on real hardware: Voxtral segment timestamps, the macOS mic permission, keep-awake on Windows.
- Phase 7: prompt calibration on real meetings.
- Phase 8: the `pv_writer.spec` (optional).
- Phase 6b: Google Docs (postponed).

Deviations from §3–§4:
- `meeting_controller.py` was not split from `meeting_view.py`. The ported session_tab logic lives in `MeetingView`,
  as it did in dnd_logger.
- `agenda.py` was added (agenda tree, headings, matching of note headings).
- `summarizer.py` keeps only the backends and the helpers. The chunked loop and the worker moved to `pv_pipeline.py`.
- The vendored `rich_editor.py` and `fold_gutter.py` got light-theme colours, as noted in their headers.
- The answers to the open questions in §9 are: no Google Cloud for now (offline Word), scrubbed style excerpts
  allowed, run from source first.
