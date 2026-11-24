"""Three-stage PV generation: extract & classify, redact per agenda item, assemble.

- Stage A turns the merged transcript and notes into facts classified by agenda item. It
  reuses dnd_logger's chunked condensation loop but returns JSON, which makes the
  off-topic filtering explicit. The result is cached in ``extraction.json``.
- Stage B writes one HTML fragment per agenda item from its notes and facts, which keeps
  every call short and lets a single item be regenerated.
- Stage C (``pv_document.py``) adds the title, linked agenda and numbered headings.
- Stage D writes the one-page synthesis (main points and action list) from the finished
  bodies. It runs after every generation, including a single-item regeneration.

``generate_pv`` is plain Python so it can be tested with a fake backend. The Qt worker at
the bottom runs it in a QThread.
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Signal

from . import pv_prompts
from .agenda import AgendaNode, agenda_outline, iter_nodes, node_path, other_info_node, parse_agenda
from .i18n import tr
from .notes_merge import (
    NoteEntry,
    assign_missing_times,
    format_notes,
    merge,
    notes_by_node,
    parse_notes,
    parse_transcript,
    split_for_llm,
)
from .pv_document import body_images, parse_fragment
from .rate_limit import CHARS_PER_TOKEN, estimate_tokens
from .summarizer import build_backend, markdown_emphasis_to_html, strip_code_fences

log = logging.getLogger("pvwriter.pipeline")

EXTRACT_CHUNK_CHARS = 40_000  # upper bound; lowered to fit the model's tokens per minute
EXTRACT_MAX_TOKENS = 12_000
MIN_EXTRACT_CHUNK_CHARS = 4_000
PREVIOUS_PV_MAX_CHARS = 12_000
SLIDES_MAX_CHARS = 12_000
DEFAULT_AGENDA = "Points abordés"
SUMMARY_NODE = "synthese"  # ``only_node`` value that regenerates the synthesis alone
_ALLOWED_FACT_TYPES = {
    "info",
    "decision",
    "vote",
    "action",
    "question",
    "reponse",
    "reaction",
    "rappel",
    "remerciement",
}


@dataclass
class PvInputs:
    """Everything stage A and B need, captured on the GUI thread.

    Attributes:
        title: Meeting name.
        date_fr: Meeting date as JJ/MM/AAAA.
        organization: The "nous" of the PV.
        agenda_text: Indented agenda.
        note_blocks: ``(heading_level, text)`` per notes editor block.
        transcript: Merged transcript text (possibly empty).
        recorded_seconds: Audio duration, to place an untimed transcript.
        glossary: Spelling references.
        previous_pv_text: Text of the previous PV, or "".
        slides_text: Text of the slides shown during the meeting (``read_slides``), or "".
    """

    title: str
    date_fr: str
    organization: str
    agenda_text: str
    note_blocks: list[tuple[int, str]]
    transcript: str = ""
    recorded_seconds: float = 0.0
    glossary: list[str] = field(default_factory=list)
    previous_pv_text: str = ""
    slides_text: str = ""


@dataclass
class PvResult:
    """Output of a generation run.

    Attributes:
        bodies: HTML fragment per agenda id.
        facts: Stage-A facts.
        warnings: Messages for the status bar.
        summary: Stage-D synthesis ``{"points": [...], "actions": [...]}``, or {} if it failed.
    """

    bodies: dict[str, str]
    facts: list[dict]
    warnings: list[str]
    summary: dict = field(default_factory=dict)


def agenda_for(inputs: PvInputs) -> list[AgendaNode]:
    """Return the agenda tree, with a single default item when none was typed."""
    return parse_agenda(inputs.agenda_text if inputs.agenda_text.strip() else DEFAULT_AGENDA)


# ── Stage A ────────────────────────────────────────────────────────────────


def parse_facts(answer: str) -> list[dict]:
    """Parse the extraction answer, tolerating code fences and text around the JSON.

    Args:
        answer: Raw model output.

    Returns:
        Normalized facts (``point``, ``type``, ``texte``, ``qui``, ``heure``, ``source``).

    Raises:
        ValueError: If no JSON object or array can be recovered.
    """
    text = strip_code_fences(answer)
    data = None
    for candidate in (text, _between(text, "{", "}"), _between(text, "[", "]")):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            break
        except ValueError:
            continue
    if data is None:
        raise ValueError("no JSON found in the extraction answer")
    items = data.get("faits", data.get("facts", [])) if isinstance(data, dict) else data
    facts = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        texte = str(item.get("texte") or item.get("text") or "").strip()
        if not texte:
            continue
        kind = str(item.get("type") or "info").strip().lower()
        facts.append(
            {
                "point": str(item.get("point") or "").strip(),
                "type": kind if kind in _ALLOWED_FACT_TYPES else "info",
                "texte": texte,
                "qui": str(item.get("qui") or "").strip(),
                "heure": str(item.get("heure") or "").strip(),
                "source": str(item.get("source") or "transcription").strip(),
            }
        )
    return facts


def _between(text: str, start: str, end: str) -> str:
    """Return the substring from the first ``start`` to the last ``end`` (inclusive)."""
    i, j = text.find(start), text.rfind(end)
    return text[i : j + 1] if 0 <= i < j else ""


def extraction_key(merged: str, roots: list[AgendaNode], system_prompt: str, backend_name: str) -> str:
    """Return the cache key of a stage-A run."""
    payload = "\x1f".join([merged, agenda_outline(roots), system_prompt, backend_name])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extraction_budget(tokens_per_minute: int, system_prompt: str, user_template_chars: int) -> tuple[int, int]:
    """Size stage-A calls so that one call fits the per-minute token budget.

    Args:
        tokens_per_minute: The model's budget (0 = unlimited).
        system_prompt: Extraction system prompt.
        user_template_chars: Length of the user prompt without the transcript part.

    Returns:
        ``(chunk_chars, max_tokens)``: the transcript characters per call and the answer budget.
    """
    if not tokens_per_minute:
        return EXTRACT_CHUNK_CHARS, EXTRACT_MAX_TOKENS
    usable = int(tokens_per_minute * 0.9)  # margin for the estimate's error
    max_tokens = min(EXTRACT_MAX_TOKENS, max(2_000, int(usable * 0.35)))
    fixed = estimate_tokens(system_prompt) + int(user_template_chars / CHARS_PER_TOKEN)
    chunk_chars = int((usable - max_tokens - fixed) * CHARS_PER_TOKEN)
    return max(MIN_EXTRACT_CHUNK_CHARS, min(EXTRACT_CHUNK_CHARS, chunk_chars)), max_tokens


def run_extraction(backend, inputs: PvInputs, roots, notes: list[NoteEntry], system_prompt: str, progress=None):
    """Run stage A over the merged text.

    Returns:
        ``(facts, warnings)``.
    """
    lines = assign_missing_times(parse_transcript(inputs.transcript), inputs.recorded_seconds)
    unplaced = [n for n in notes if n.node_id is None]
    if not lines and not unplaced:
        return [], []
    merged = merge(lines, notes, roots)
    template_chars = len(pv_prompts.EXTRACT_USER) + len(agenda_outline(roots)) + len(_glossary_text(inputs.glossary))
    chunk_chars, max_tokens = extraction_budget(
        getattr(backend, "tokens_per_minute", 0), system_prompt, template_chars + 500
    )
    chunks = split_for_llm(merged, chunk_chars)
    facts, warnings = [], []
    for i, chunk in enumerate(chunks, start=1):
        if progress:
            progress(tr("pipeline.progress.extract", current=i, total=len(chunks)))
        user = pv_prompts.EXTRACT_USER.format(
            titre=inputs.title,
            date=inputs.date_fr,
            organisation=inputs.organization or tr("pipeline.no_organization"),
            agenda=agenda_outline(roots),
            glossaire=_glossary_text(inputs.glossary),
            partie=f"{i}/{len(chunks)}",
            texte=chunk,
        )
        answer = backend.complete(system_prompt, user, temperature=0.0, max_tokens=max_tokens, json_mode=True)
        try:
            chunk_facts = parse_facts(answer)
        except ValueError:
            log.warning("Unparsable extraction answer for chunk %d, retrying once", i)
            answer = backend.complete(
                system_prompt,
                user + "\nRéponds UNIQUEMENT avec l'objet JSON demandé.",
                temperature=0.0,
                max_tokens=max_tokens,
                json_mode=True,
            )
            try:
                chunk_facts = parse_facts(answer)
            except ValueError:
                warnings.append(tr("pipeline.warning.extract_failed", part=i))
                continue
        log.info("Extraction chunk %d/%d: %d facts", i, len(chunks), len(chunk_facts))
        facts.extend(chunk_facts)
    return facts, warnings


# ── Stage B ────────────────────────────────────────────────────────────────


def facts_by_node(facts: list[dict], roots: list[AgendaNode]) -> dict[str, list[dict]]:
    """Group facts per agenda id. Unknown ids go to "Autres informations"."""
    known = {n.node_id for n in iter_nodes(roots)}
    fallback = other_info_node(roots)
    grouped: dict[str, list[dict]] = {}
    for fact in facts:
        point = fact.get("point", "")
        if point not in known:
            point = fallback.node_id if fallback else next(iter(known), "")
        grouped.setdefault(point, []).append(fact)
    return grouped


def format_facts(facts: list[dict]) -> str:
    """Render facts as bullet lines for the redaction prompt."""
    lines = []
    for fact in facts:
        meta = [fact["type"]]
        if fact.get("qui"):
            meta.append(f"qui : {fact['qui']}")
        if fact.get("heure"):
            meta.append(fact["heure"])
        lines.append(f"- ({', '.join(meta)}) {fact['texte']}")
    return "\n".join(lines)


_HEADING_TAG = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)


def clean_fragment(answer: str) -> str:
    """Normalize a stage-B answer into the HTML subset the exporters understand."""
    text = strip_code_fences(answer)
    if text.strip().strip('"').strip() == "":
        return ""
    text = markdown_emphasis_to_html(text)
    text = _HEADING_TAG.sub(r"<p><strong>\1</strong></p>", text)
    if "<" not in text:
        # Plain text answer: one paragraph per line
        text = "".join(f"<p>{line.strip()}</p>" for line in text.splitlines() if line.strip())
    return text.strip()


def redact_node(backend, inputs: PvInputs, roots, node: AgendaNode, notes, facts, system_prompt: str) -> str:
    """Run stage B for one agenda item.

    Returns:
        The HTML fragment, or "" when the item has no material.
    """
    if not notes and not facts:
        return ""
    chain = node_path(roots, node.node_id)
    children = "".join(f"\n- {child.heading}" for child in node.children)
    user = pv_prompts.REDACT_USER.format(
        titre=inputs.title,
        date=inputs.date_fr,
        organisation=inputs.organization or tr("pipeline.no_organization"),
        chemin=" > ".join(n.heading for n in chain),
        sous_points=f"SOUS-POINTS RÉDIGÉS SÉPARÉMENT (ne pas traiter ici) :{children}\n" if children else "",
        notes=format_notes(notes) or "(aucune)",
        faits=format_facts(facts) or "(aucun)",
        glossaire=_glossary_text(inputs.glossary),
        slides=inputs.slides_text[:SLIDES_MAX_CHARS] or "(non fournies)",
        pv_precedent=inputs.previous_pv_text[:PREVIOUS_PV_MAX_CHARS] or "(non fourni)",
    )
    return clean_fragment(backend.complete(system_prompt, user, temperature=0.3, max_tokens=6000))


def _glossary_text(glossary: list[str]) -> str:
    """Render the glossary for a prompt."""
    return ", ".join(g for g in glossary if g.strip()) or "(vide)"


# ── Orchestration ──────────────────────────────────────────────────────────


def generate_pv(
    backend,
    inputs: PvInputs,
    config: dict,
    cached_extraction: dict | None = None,
    only_node: str | None = None,
    previous_bodies: dict | None = None,
    progress=None,
    on_section=None,
) -> tuple[PvResult, dict]:
    """Run stages A and B.

    Args:
        backend: Object with ``complete(system, user, temperature, max_tokens, json_mode=False)``.
        inputs: Captured meeting inputs.
        config: App config (custom prompts).
        cached_extraction: Content of ``extraction.json`` from a previous run, if any.
        only_node: Regenerate this agenda item only; other bodies come from ``previous_bodies``.
            ``SUMMARY_NODE`` regenerates the synthesis alone from ``previous_bodies``.
        previous_bodies: Bodies of the previous run.
        progress: Optional ``callable(str)`` for status messages.
        on_section: Optional ``callable(node_id, html)`` called after each item.

    Returns:
        ``(result, extraction_cache)``: the generated bodies, and the cache to persist.
    """
    roots = agenda_for(inputs)
    if progress and hasattr(backend, "on_wait"):
        backend.on_wait = lambda seconds: progress(tr("pipeline.progress.rate_wait", seconds=seconds))
    if only_node == SUMMARY_NODE:
        # Synthesis only: the bodies (possibly edited by hand) are kept as they are
        bodies = dict(previous_bodies or {})
        if not any(bodies.values()):
            raise RuntimeError(tr("pipeline.error.nothing"))
        if progress:
            progress(tr("pipeline.progress.summary"))
        summary = summarize_pv(backend, inputs, roots, bodies)
        return PvResult(bodies=bodies, facts=[], warnings=[], summary=summary), cached_extraction or {}

    notes = parse_notes(inputs.note_blocks, roots)
    extract_prompt = pv_prompts.extract_system(config)

    lines_present = bool(inputs.transcript.strip())
    merged_key_source = merge(
        assign_missing_times(parse_transcript(inputs.transcript), inputs.recorded_seconds), notes, roots
    )
    key = extraction_key(merged_key_source, roots, extract_prompt, getattr(backend, "name", "?"))
    warnings: list[str] = []
    if cached_extraction and cached_extraction.get("key") == key:
        facts = cached_extraction.get("facts", [])
        log.info("Reusing cached extraction (%d facts)", len(facts))
    else:
        facts, warnings = run_extraction(backend, inputs, roots, notes, extract_prompt, progress)
    cache = {"key": key, "facts": facts}
    if not lines_present and not facts and not notes:
        raise RuntimeError(tr("pipeline.error.nothing"))

    grouped_notes = notes_by_node([n for n in notes if n.node_id is not None])
    grouped_facts = facts_by_node(facts, roots)
    redact_prompt = pv_prompts.redact_system(config)
    bodies = dict(previous_bodies or {})
    targets = [n for n in iter_nodes(roots) if only_node is None or n.node_id == only_node]
    for i, node in enumerate(targets, start=1):
        if progress:
            progress(tr("pipeline.progress.redact", current=i, total=len(targets), title=node.heading))
        html = redact_node(
            backend,
            inputs,
            roots,
            node,
            grouped_notes.get(node.node_id, []),
            grouped_facts.get(node.node_id, []),
            redact_prompt,
        )
        # Pictures added by hand are not known to the LLM: keep them, at the end of the item
        kept = body_images(bodies.get(node.node_id, ""))
        if kept:
            html = "\n".join(([html] if html else []) + kept)
        bodies[node.node_id] = html
        if on_section:
            on_section(node.node_id, html)
    # Drop bodies of agenda items that no longer exist
    valid = {n.node_id for n in iter_nodes(roots)}
    bodies = {k: v for k, v in bodies.items() if k in valid}

    # Stage D, rerun after every generation so it follows the regenerated items
    summary: dict = {}
    if any(bodies.values()):
        if progress:
            progress(tr("pipeline.progress.summary"))
        try:
            summary = summarize_pv(backend, inputs, roots, bodies)
        except Exception:  # the PV itself is fine without its synthesis
            log.exception("PV summary failed")
            warnings.append(tr("pipeline.warning.summary_failed"))
    return PvResult(bodies=bodies, facts=facts, warnings=warnings, summary=summary), cache


# ── Stage D ────────────────────────────────────────────────────────────────


def pv_plain_text(roots: list[AgendaNode], bodies: dict[str, str]) -> str:
    """Render the PV as plain text (headings, paragraphs, indented bullets) for a prompt."""
    lines = []
    for node in iter_nodes(roots):
        body = bodies.get(node.node_id, "")
        lines.append(f"\n{'#' * node.level} {node.heading}")
        for block in parse_fragment(body):
            text = block.text.replace("\n", " ").strip()
            lines.append(f"{'  ' * (block.list_level - 1)}- {text}" if block.list_level else text)
    return "\n".join(lines).strip()


def parse_summary(answer: str) -> dict:
    """Parse the stage-D answer into ``{"points": [...], "actions": [...]}``.

    Raises:
        ValueError: If no JSON object can be recovered or it holds nothing usable.
    """
    text = strip_code_fences(answer)
    data = None
    for candidate in (text, _between(text, "{", "}")):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            break
        except ValueError:
            continue
    if not isinstance(data, dict):
        raise ValueError("no JSON object found in the summary answer")

    def _str(item: dict, key: str) -> str:
        return str(item.get(key) or "").strip()

    points = [
        {"sujet": _str(p, "sujet"), "texte": _str(p, "texte")}
        for p in data.get("points", [])
        if isinstance(p, dict) and _str(p, "texte")
    ][: pv_prompts.SUMMARY_MAX_POINTS]
    actions = [
        {"action": _str(a, "action"), "qui": _str(a, "qui"), "echeance": _str(a, "echeance")}
        for a in data.get("actions", [])
        if isinstance(a, dict) and _str(a, "action")
    ][: pv_prompts.SUMMARY_MAX_ACTIONS]
    if not points and not actions:
        raise ValueError("empty summary")
    return {"points": points, "actions": actions}


def summarize_pv(backend, inputs: PvInputs, roots: list[AgendaNode], bodies: dict[str, str]) -> dict:
    """Run stage D: the one-page synthesis and action list placed at the top of the PV."""
    user = pv_prompts.SUMMARY_USER.format(
        titre=inputs.title,
        date=inputs.date_fr,
        organisation=inputs.organization or tr("pipeline.no_organization"),
        pv=pv_plain_text(roots, bodies),
    )
    answer = backend.complete(pv_prompts.SUMMARY_SYSTEM, user, temperature=0.2, max_tokens=3000, json_mode=True)
    return parse_summary(answer)


def read_previous_pv(path: str) -> str:
    """Extract plain text from a previous PV (PDF, DOCX, TXT or MD).

    Args:
        path: File path, or "" for none.

    Returns:
        The text, or "" when the file is missing or unreadable.
    """
    if not path or not os.path.exists(path):
        return ""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        if ext == ".docx":
            import docx

            return "\n".join(p.text for p in docx.Document(path).paragraphs)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        log.exception("Could not read previous PV %s", path)
        return ""


def read_slides(path: str) -> tuple[str, list[int]]:
    """Extract the text of a slide deck exported as PDF (or a TXT / MD file), slide by slide.

    Args:
        path: File path, or "" for none.

    Returns:
        ``(text, image_only)``: the text with a ``[Slide N]`` line before each slide, and the
        numbers of the slides that hold no text (pictures, scans), which the LLM cannot see.
    """
    if not path or not os.path.exists(path):
        return "", []
    if os.path.splitext(path)[1].lower() != ".pdf":
        return read_previous_pv(path), []
    try:
        from pypdf import PdfReader

        pages = [page.extract_text() or "" for page in PdfReader(path).pages]
    except Exception:
        log.exception("Could not read slides %s", path)
        return "", []
    parts, image_only = [], []
    for number, text in enumerate(pages, start=1):
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if text:
            parts.append(f"[Slide {number}]\n{text}")
        else:
            image_only.append(number)
    return "\n\n".join(parts), image_only


class PvGenerationWorker(QObject):
    """Runs ``generate_pv`` in a QThread."""

    progress = Signal(str)
    section_done = Signal(str, str)  # node_id, html
    completed = Signal(object, object)  # PvResult, extraction cache
    error = Signal(str)

    def __init__(
        self, inputs: PvInputs, config: dict, cached_extraction, only_node, previous_bodies, previous_pv, slides=""
    ):
        """Initialize the worker.

        Args:
            inputs: Captured inputs (previous PV text is read here, off the GUI thread).
            config: App configuration.
            cached_extraction: Content of ``extraction.json`` or None.
            only_node: Agenda id to regenerate alone, or None for the whole PV.
            previous_bodies: Bodies of the previous run.
            previous_pv: Path of the previous PV file, or "".
            slides: Path of the slides shown during the meeting, or "".
        """
        super().__init__()
        self._inputs = inputs
        self._config = config
        self._cached = cached_extraction
        self._only_node = only_node
        self._previous_bodies = previous_bodies
        self._previous_pv = previous_pv
        self._slides = slides

    def run(self):
        """Generate the PV and emit the result."""
        backend = None
        try:
            backend = build_backend(self._config)
            if self._previous_pv:
                self._inputs.previous_pv_text = read_previous_pv(self._previous_pv)
            image_only = []
            if self._slides:
                self._inputs.slides_text, image_only = read_slides(self._slides)
            result, cache = generate_pv(
                backend,
                self._inputs,
                self._config,
                cached_extraction=self._cached,
                only_node=self._only_node,
                previous_bodies=self._previous_bodies,
                progress=self.progress.emit,
                on_section=self.section_done.emit,
            )
            if image_only and self._only_node != SUMMARY_NODE:
                pages = ", ".join(str(n) for n in image_only)
                result.warnings.append(tr("pipeline.warning.slides_image_only", pages=pages))
            self.completed.emit(result, cache)
        except Exception as e:
            log.exception("PV generation failed")
            self.error.emit(tr("pipeline.error.generic", error=e))
        finally:
            if backend is not None:
                backend.close()


def start_pv_generation(
    inputs: PvInputs, config: dict, cached_extraction, only_node, previous_bodies, previous_pv, slides=""
) -> tuple[QThread, PvGenerationWorker]:
    """Create a PV generation worker in a new thread; the caller starts the thread."""
    thread = QThread()
    worker = PvGenerationWorker(inputs, config, cached_extraction, only_node, previous_bodies, previous_pv, slides)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.completed.connect(thread.quit)
    worker.error.connect(thread.quit)
    return thread, worker
