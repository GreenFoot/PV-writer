"""Meeting model: one folder per meeting, with its metadata in ``meeting.json``.

Folder layout (``<data>/meetings/AAAAMMJJ_HHMM_<slug>/``):

- ``meeting.json``: metadata (this module)
- ``recording*.wav`` and imported audio/transcript files
- ``notes.html`` (+ ``.bak``): the secretary's notes
- ``transcript.txt``: merged transcript
- ``extraction.json``: cached stage-A facts
- ``pv.json``: generated section bodies
- ``pv_vN.docx``: exported Word versions
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime

from .utils import ensure_dir, meetings_dir, slugify

log = logging.getLogger("pvwriter.meeting")

MEETING_FILE = "meeting.json"
NOTES_FILE = "notes.html"
TRANSCRIPT_FILE = "transcript.txt"
EXTRACTION_FILE = "extraction.json"
PV_FILE = "pv.json"


@dataclass
class Meeting:
    """Metadata of one meeting.

    Attributes:
        folder: Absolute path of the meeting folder (not persisted).
        title: Meeting name, e.g. "Réunion des délégués".
        date: ISO date of the meeting.
        organization: Body that writes the PV, the "nous" of the text.
        agenda_text: Agenda as an indented list (see ``agenda.parse_agenda``).
        glossary: Names, acronyms and terms with their correct spelling.
        previous_pv: File name (inside the folder) of the previous PV, or "".
        sources: Audio/transcript file names (inside the folder), in processing order.
        recorded_seconds: Total recorded audio duration, used to place untimed text.
        docx_versions: Exported Word files, oldest first.
        created: Creation timestamp (ISO).
    """

    folder: str = ""
    title: str = "Réunion"
    date: str = field(default_factory=lambda: date.today().isoformat())
    organization: str = ""
    agenda_text: str = ""
    glossary: list[str] = field(default_factory=list)
    previous_pv: str = ""
    sources: list[str] = field(default_factory=list)
    recorded_seconds: float = 0.0
    docx_versions: list[str] = field(default_factory=list)
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    # ── Paths ─────────────────────────────────────────────

    def path(self, name: str) -> str:
        """Return the absolute path of a file inside the meeting folder."""
        return os.path.join(self.folder, name)

    @property
    def notes_path(self) -> str:
        """Absolute path of the notes HTML file."""
        return self.path(NOTES_FILE)

    @property
    def transcript_path(self) -> str:
        """Absolute path of the merged transcript."""
        return self.path(TRANSCRIPT_FILE)

    def source_paths(self) -> list[str]:
        """Return the absolute paths of the audio/transcript sources that still exist."""
        return [self.path(name) for name in self.sources if os.path.exists(self.path(name))]

    def add_source(self, abs_path: str) -> None:
        """Register a file of the meeting folder as a source (ignores duplicates)."""
        name = os.path.relpath(abs_path, self.folder)
        if name not in self.sources:
            self.sources.append(name)

    def set_sources(self, abs_paths: list[str]) -> None:
        """Replace the ordered list of sources."""
        self.sources = [os.path.relpath(p, self.folder) for p in abs_paths]

    def next_docx_name(self) -> str:
        """Return the file name for the next Word export (``pv_vN.docx``)."""
        n = len(self.docx_versions) + 1
        while os.path.exists(self.path(f"pv_v{n}.docx")):
            n += 1
        return f"pv_v{n}.docx"

    # ── Display ───────────────────────────────────────────

    @property
    def date_fr(self) -> str:
        """Return the date as JJ/MM/AAAA."""
        try:
            return datetime.strptime(self.date, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return self.date

    @property
    def pv_title(self) -> str:
        """Return the document title, e.g. ``PV Réunion des délégués du 24/11/2025``."""
        return f"PV {self.title.strip() or 'Réunion'} du {self.date_fr}"

    # ── Persistence ───────────────────────────────────────

    def save(self) -> None:
        """Write ``meeting.json`` (atomically, through a temporary file)."""
        ensure_dir(self.folder)
        data = asdict(self)
        data.pop("folder")
        tmp = self.path(MEETING_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path(MEETING_FILE))

    @classmethod
    def load(cls, folder: str) -> "Meeting":
        """Load a meeting from its folder.

        Raises:
            OSError: If the folder has no readable ``meeting.json``.
            ValueError: If the file is not valid JSON.
        """
        with open(os.path.join(folder, MEETING_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
        known = {k: v for k, v in data.items() if k in {f.name for f in fields(cls)} and k != "folder"}
        return cls(folder=folder, **known)

    @classmethod
    def create(cls, title: str, meeting_date: str, organization: str = "", root: str | None = None) -> "Meeting":
        """Create a new meeting folder and its ``meeting.json``.

        Args:
            title: Meeting name.
            meeting_date: ISO date.
            organization: Body writing the PV ("nous").
            root: Parent directory (defaults to the app's meetings directory).

        Returns:
            The saved meeting.
        """
        root = root or meetings_dir()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(root, f"{stamp}_{slugify(title)}")
        n = 2
        while os.path.exists(folder):
            folder = os.path.join(root, f"{stamp}_{slugify(title)}_{n}")
            n += 1
        meeting = cls(folder=folder, title=title, date=meeting_date, organization=organization)
        meeting.save()
        return meeting


def list_meetings(root: str | None = None) -> list[Meeting]:
    """Return every readable meeting, most recent meeting date first.

    Args:
        root: Directory to scan (defaults to the app's meetings directory).
    """
    root = root or meetings_dir()
    meetings = []
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if not os.path.isfile(os.path.join(folder, MEETING_FILE)):
            continue
        try:
            meetings.append(Meeting.load(folder))
        except (OSError, ValueError, TypeError):
            log.warning("Skipping unreadable meeting folder %s", folder)
    meetings.sort(key=lambda m: (m.date, m.created), reverse=True)
    return meetings


def read_json(path: str, default):
    """Read a JSON file, returning ``default`` when it is missing or invalid."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        log.warning("Could not read %s", path)
        return default


def write_json(path: str, data) -> None:
    """Write a JSON file atomically."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
