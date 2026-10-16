"""Align the secretary's notes with the transcript (pure functions, no Qt).

Notes come from the notes editor as ``(heading_level, text)`` blocks. Headings that match
an agenda entry set the current agenda item, and paragraphs typed while recording start
with a ``[hh:mm:ss]`` timestamp. The transcript has the same prefixes on its lines when the
API returned segments. Merging both gives the extraction stage one chronological text in
which each note sits where it was written.
"""

import re
from dataclasses import dataclass

from .agenda import AgendaNode, find_node, match_heading

TIMESTAMP_RE = re.compile(r"^\s*\[(\d{1,2}):(\d{2}):(\d{2})\]\s*")


@dataclass
class NoteEntry:
    """One paragraph of notes.

    Attributes:
        node_id: Agenda item the note was typed under, or None (before any heading).
        seconds: Recording time when it was typed, or None.
        text: Note text without its timestamp.
    """

    node_id: str | None
    seconds: float | None
    text: str


@dataclass
class TranscriptLine:
    """One transcript line, with its start time when known."""

    seconds: float | None
    text: str


def parse_timestamp(text: str) -> tuple[float | None, str]:
    """Split a leading ``[hh:mm:ss]`` prefix from a line.

    Returns:
        ``(seconds or None, remaining text)``.
    """
    match = TIMESTAMP_RE.match(text)
    if not match:
        return None, text.strip()
    h, m, s = (int(g) for g in match.groups())
    return float(h * 3600 + m * 60 + s), text[match.end() :].strip()


def format_seconds(seconds: float) -> str:
    """Format seconds as ``hh:mm:ss``."""
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def parse_notes(blocks: list[tuple[int, str]], roots: list[AgendaNode]) -> list[NoteEntry]:
    """Turn editor blocks into note entries attached to agenda items.

    A heading that matches an agenda entry switches the current item. Any other heading
    is kept as a note (a sub-topic the secretary named). Untimed paragraphs inherit the
    time of the previous note under the same item.

    Args:
        blocks: ``(heading_level, text)`` per editor block, heading_level 0 for body text.
        roots: Agenda tree.

    Returns:
        Non-empty note entries in document order.
    """
    entries = []
    current = None
    last_seconds = None
    for level, raw in blocks:
        text = raw.strip()
        if not text:
            continue
        if level > 0:
            node = match_heading(roots, text)
            if node is not None:
                current = node.node_id
                last_seconds = None
                continue
            text = f"Sous-thème : {text}"
        seconds, body = parse_timestamp(text)
        if not body:
            continue
        if seconds is None:
            seconds = last_seconds
        else:
            last_seconds = seconds
        entries.append(NoteEntry(node_id=current, seconds=seconds, text=body))
    return entries


def notes_by_node(entries: list[NoteEntry]) -> dict[str | None, list[NoteEntry]]:
    """Group note entries per agenda item id (None = not under any item)."""
    grouped: dict[str | None, list[NoteEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.node_id, []).append(entry)
    return grouped


def format_notes(entries: list[NoteEntry]) -> str:
    """Render notes as bullet lines for a prompt."""
    lines = []
    for entry in entries:
        stamp = f"[{format_seconds(entry.seconds)}] " if entry.seconds is not None else ""
        lines.append(f"- {stamp}{entry.text}")
    return "\n".join(lines)


def parse_transcript(text: str) -> list[TranscriptLine]:
    """Split a transcript into lines with their timestamps (when present)."""
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        seconds, body = parse_timestamp(raw)
        if body:
            lines.append(TranscriptLine(seconds=seconds, text=body))
    return lines


def assign_missing_times(lines: list[TranscriptLine], total_seconds: float) -> list[TranscriptLine]:
    """Estimate times for an untimed transcript, proportionally to the text position.

    Only used when *no* line has a timestamp (an imported text transcript, or an API that
    returned no segments). This is dnd_logger's proportional bookmark placement, applied
    to every line.

    Args:
        lines: Transcript lines.
        total_seconds: Duration of the recording.

    Returns:
        The same lines, timed when an estimate was possible.
    """
    if not lines or total_seconds <= 0 or any(line.seconds is not None for line in lines):
        return lines
    total_chars = sum(len(line.text) for line in lines) or 1
    done = 0
    timed = []
    for line in lines:
        timed.append(TranscriptLine(seconds=total_seconds * done / total_chars, text=line.text))
        done += len(line.text)
    return timed


def _note_marker(entry: NoteEntry, roots: list[AgendaNode]) -> str:
    """Return the line that represents a note inside the merged text."""
    stamp = f" [{format_seconds(entry.seconds)}]" if entry.seconds is not None else ""
    node = find_node(roots, entry.node_id) if entry.node_id else None
    where = f" (point {node.node_id} — {node.heading})" if node else ""
    return f">>> NOTE DU SECRÉTAIRE{stamp}{where} : {entry.text}"


def merge(lines: list[TranscriptLine], notes: list[NoteEntry], roots: list[AgendaNode]) -> str:
    """Interleave notes into the transcript, in time order.

    A note typed at time T is placed after the transcript lines that started at or before T
    (the secretary writes after hearing). Untimed notes are listed first. Untimed transcript
    lines keep their position relative to the timed line before them.

    Args:
        lines: Transcript lines (possibly untimed).
        notes: Note entries.
        roots: Agenda tree, used to label notes with their item.

    Returns:
        The merged text, one line per transcript line or note.
    """
    out = []
    untimed_notes = [n for n in notes if n.seconds is None]
    timed_notes = sorted((n for n in notes if n.seconds is not None), key=lambda n: n.seconds)
    if untimed_notes:
        out.append(">>> NOTES DU SECRÉTAIRE SANS HORODATAGE :")
        out.extend(_note_marker(n, roots) for n in untimed_notes)
        out.append("")

    note_idx = 0
    for line in lines:
        if line.seconds is not None:
            # Flush the notes typed before this line started
            while note_idx < len(timed_notes) and timed_notes[note_idx].seconds < line.seconds:
                out.append(_note_marker(timed_notes[note_idx], roots))
                note_idx += 1
            out.append(f"[{format_seconds(line.seconds)}] {line.text}")
        else:
            out.append(line.text)
    out.extend(_note_marker(n, roots) for n in timed_notes[note_idx:])
    return "\n".join(out).strip()


def split_for_llm(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of at most ``max_chars``, on line boundaries when possible."""
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    chunks = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > max_chars:  # a single huge line (untimed transcript)
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if size + len(line) + 1 > max_chars and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current and "\n".join(current).strip():
        chunks.append("\n".join(current))
    return chunks
