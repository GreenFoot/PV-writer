"""Tests for the pure logic: agenda, notes merge, pipeline parsing, assembly and export."""

import json
import os

import numpy as np
import pytest
import soundfile as sf

from src.agenda import find_node, match_heading, parse_agenda
from src.docx_exporter import export_docx
from src.meeting import Meeting, list_meetings
from src.notes_merge import (
    TranscriptLine,
    assign_missing_times,
    merge,
    parse_notes,
    parse_timestamp,
    parse_transcript,
    split_for_llm,
)
from src.pv_document import parse_fragment, render_preview_html
from src.pv_pipeline import PvInputs, clean_fragment, facts_by_node, generate_pv, parse_facts
from src.transcriber import AudioChunker, _format_segments, _strip_stt_artifacts, build_context_bias

AGENDA = """Concernant l'ADP
  Chocolats d'hiver
  Les cellules
    Cellule événements
    Cellule tombola
Concernant les écoles
\tÉcole A
"""


# ── Agenda ──────────────────────────────────────────────


def test_agenda_levels_and_headings():
    roots = parse_agenda(AGENDA)
    assert [n.heading for n in roots] == ["Concernant l'ADP :", "Concernant les écoles :", "Autres informations :"]
    cellules = find_node(roots, "1.2")
    assert cellules.heading == "2) Les cellules"
    assert [c.heading for c in cellules.children] == ["a) Cellule événements", "b) Cellule tombola"]
    assert find_node(roots, "2.1").heading == "1) École A"


def test_agenda_strips_bullets_and_does_not_duplicate_other_info():
    roots = parse_agenda("● Budget\n  ○ 1) Dépenses\nAutres informations")
    assert [n.title for n in roots] == ["Budget", "Autres informations"]
    assert roots[0].children[0].title == "Dépenses"


def test_agenda_clamps_depth():
    roots = parse_agenda("A\n  B\n    C\n      D")
    c = find_node(roots, "1.1.1")
    d = find_node(roots, "1.1.2")
    assert c.level == 3 and d.level == 3 and d.title == "D"


def test_match_heading_tolerates_numbering_and_accents():
    roots = parse_agenda(AGENDA)
    assert match_heading(roots, "a) Cellule evenements").node_id == "1.2.1"
    assert match_heading(roots, "1) Ecole A").node_id == "2.1"
    assert match_heading(roots, "Divers") is None


# ── Notes and transcript ────────────────────────────────


def test_parse_timestamp():
    assert parse_timestamp("[01:02:03] hello") == (3723.0, "hello")
    assert parse_timestamp("no stamp") == (None, "no stamp")


def test_parse_notes_attaches_items_and_inherits_time():
    roots = parse_agenda(AGENDA)
    blocks = [
        (0, "[00:00:30] Note générale"),
        (2, "1) Chocolats d'hiver"),
        (0, "[00:05:00] Bénéfice de 651,91€"),
        (0, "Meilleure vente depuis 2023"),
        (3, "Point imprévu"),
        (0, ""),
    ]
    notes = parse_notes(blocks, roots)
    assert notes[0].node_id is None and notes[0].seconds == 30
    assert notes[1].node_id == "1.1" and notes[1].seconds == 300
    assert notes[2].seconds == 300  # inherited
    assert notes[3].text == "Sous-thème : Point imprévu"


def test_merge_places_notes_after_spoken_lines():
    roots = parse_agenda(AGENDA)
    lines = parse_transcript("[00:00:10] Bonjour à tous\n[00:05:10] Les chocolats ont rapporté 651 euros\n")
    notes = parse_notes([(2, "Chocolats d'hiver"), (0, "[00:05:05] Bénéfice 651,91€")], roots)
    merged = merge(lines, notes, roots).splitlines()
    assert merged[0].startswith("[00:00:10]")
    assert merged[1].startswith(">>> NOTE DU SECRÉTAIRE [00:05:05] (point 1.1")
    assert merged[2].startswith("[00:05:10]")


def test_untimed_transcript_gets_proportional_times():
    lines = [TranscriptLine(None, "a" * 10), TranscriptLine(None, "b" * 30)]
    timed = assign_missing_times(lines, 100)
    assert [t.seconds for t in timed] == [0, 25]


def test_split_for_llm_respects_lines_and_limit():
    text = "\n".join(f"ligne {i} " + "x" * 50 for i in range(100))
    chunks = split_for_llm(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")


# ── Transcriber helpers ─────────────────────────────────


def test_chunk_offsets_match_chunker(tmp_path):
    sr = 8000
    path = tmp_path / "rec.wav"
    sf.write(path, np.zeros(sr * 150, dtype="int16"), sr)  # 150 s
    pairs = AudioChunker.chunk_audio_with_offsets(str(path), max_minutes=1)
    assert [round(start) for _, start in pairs] == [0, 50, 100]
    assert [round(s) for s in AudioChunker.chunk_offsets(150, 1)] == [0, 50, 100]


def test_format_segments_adds_offset():
    class Seg:  # pylint: disable=too-few-public-methods
        def __init__(self, start, text):
            self.start, self.text = start, text

    out = _format_segments([Seg(5.2, "Bonjour"), Seg(65, "Suite")], offset=3600)
    assert out.splitlines() == ["[01:00:05] Bonjour", "[01:01:05] Suite"]


def test_strip_stt_artifacts_collapses_loops():
    text = "merci beaucoup " * 8
    assert _strip_stt_artifacts(text).count("merci beaucoup") == 2


def test_context_bias_sanitizes():
    assert build_context_bias(["Mme Martin", "ADP", "Saint-Nicolas", "l'ARO", "ADP"]) == [
        "Mme_Martin",
        "ADP",
        "Saint-Nicolas",
        "l_ARO",
    ]


# ── Pipeline parsing ────────────────────────────────────


def test_parse_facts_tolerates_fences_and_noise():
    answer = 'Voici :\n```json\n{"faits": [{"point": "1.1", "type": "VOTE", "texte": "Voté."}, {"texte": ""}]}\n```'
    facts = parse_facts(answer)
    assert facts == [
        {"point": "1.1", "type": "vote", "texte": "Voté.", "qui": "", "heure": "", "source": "transcription"}
    ]


def test_parse_facts_rejects_garbage():
    with pytest.raises(ValueError):
        parse_facts("pas de json ici")


def test_facts_with_unknown_point_go_to_other_info():
    roots = parse_agenda(AGENDA)
    grouped = facts_by_node([{"point": "9.9", "texte": "x"}], roots)
    assert list(grouped) == ["3"]


def test_clean_fragment():
    assert clean_fragment("```html\n<h3>Titre</h3><p>**Gras**</p>\n```") == (
        "<p><strong>Titre</strong></p><p><strong>Gras</strong></p>"
    )
    assert clean_fragment('""') == ""
    assert clean_fragment("Ligne 1\nLigne 2") == "<p>Ligne 1</p><p>Ligne 2</p>"


class FakeBackend:
    """Records prompts and answers stage A with JSON, stage B with HTML."""

    name = "fake"

    def __init__(self):
        self.calls = []

    def complete(self, system_prompt, user_prompt, temperature, max_tokens, json_mode=False):
        self.calls.append((json_mode, user_prompt))
        if json_mode:
            return json.dumps({"faits": [{"point": "1.1", "type": "info", "texte": "Bénéfice de 651,91€."}]})
        return "<p>Contenu rédigé.</p>"

    def close(self):
        pass


def _inputs(transcript="[00:00:05] On a fait 651 euros avec les chocolats."):
    return PvInputs(
        title="Réunion des délégués",
        date_fr="24/11/2025",
        organization="ADP",
        agenda_text=AGENDA,
        note_blocks=[(2, "Les cellules"), (0, "[00:10:00] Minimum 5 personnes par cellule")],
        transcript=transcript,
    )


def test_generate_pv_calls_only_items_with_material():
    backend = FakeBackend()
    result, cache = generate_pv(backend, _inputs(), {})
    assert sum(1 for json_mode, _ in backend.calls if json_mode) == 1
    redacted = {k for k, v in result.bodies.items() if v}
    assert redacted == {"1.1", "1.2"}  # facts for 1.1, notes for 1.2
    assert cache["facts"][0]["point"] == "1.1"


def test_generate_pv_reuses_cached_extraction_and_single_node():
    backend = FakeBackend()
    result, cache = generate_pv(backend, _inputs(), {})
    backend2 = FakeBackend()
    result2, _ = generate_pv(
        backend2, _inputs(), {}, cached_extraction=cache, only_node="1.2", previous_bodies=result.bodies
    )
    assert not any(json_mode for json_mode, _ in backend2.calls)
    assert len(backend2.calls) == 1
    assert result2.bodies["1.1"] == result.bodies["1.1"]


def test_generate_pv_notes_only():
    backend = FakeBackend()
    result, _ = generate_pv(backend, _inputs(transcript=""), {})
    assert not any(json_mode for json_mode, _ in backend.calls)
    assert result.bodies["1.2"]


# ── Assembly and export ─────────────────────────────────


def test_parse_fragment_lists_and_inline():
    blocks = parse_fragment(
        "<ul><li><strong>Sujet</strong></li></ul><p>Texte <em>important</em>.<br>Suite</p>"
        "<ul><li>Un<ul><li>Sous</li></ul></li><li>Deux</li></ul><p>-&gt; Relancer</p>"
    )
    assert [(b.list_level, b.text) for b in blocks] == [
        (1, "Sujet"),
        (0, "Texte important.\nSuite"),
        (1, "Un"),
        (2, "Sous"),
        (1, "Deux"),
        (0, "-> Relancer"),
    ]
    assert blocks[0].runs[0].bold and blocks[1].runs[1].italic


def test_preview_and_docx_export(tmp_path):
    import docx

    roots = parse_agenda(AGENDA)
    bodies = {"1.1": "<p>La vente s'est bien passée.</p>", "1.2.2": "<ul><li>Lots locaux</li></ul>"}
    preview = render_preview_html("PV Réunion du 24/11/2025", roots, bodies)
    assert "href='#pt_1_2_1'" in preview and "b) Cellule tombola" in preview

    path = export_docx(str(tmp_path / "pv.docx"), "PV Réunion du 24/11/2025", roots, bodies)
    document = docx.Document(path)
    texts = [(p.style.name, p.text) for p in document.paragraphs]
    assert texts[0] == ("Title", "PV Réunion du 24/11/2025")
    assert ("Heading 2", "2) Les cellules") in texts
    assert ("Heading 3", "b) Cellule tombola") in texts
    assert ("List Bullet", "Lots locaux") in texts
    xml = document.element.xml
    assert 'w:anchor="pt_1_2_2"' in xml and 'w:name="pt_1_2_2"' in xml


def test_meeting_roundtrip(tmp_path):
    meeting = Meeting.create("Réunion des délégués", "2025-11-24", "ADP", root=str(tmp_path))
    meeting.agenda_text = AGENDA
    meeting.add_source(os.path.join(meeting.folder, "recording.wav"))
    meeting.save()
    loaded = list_meetings(str(tmp_path))[0]
    assert loaded.pv_title == "PV Réunion des délégués du 24/11/2025"
    assert loaded.sources == ["recording.wav"] and loaded.agenda_text == AGENDA
    assert loaded.next_docx_name() == "pv_v1.docx"
