"""
Note retrieval tests: get_notes_comprehensive(note_id=...), and result capping
plus content previews on search_notes / get_notes_comprehensive.
"""
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.project.items_notes import (
    get_notes_comprehensive,
    search_notes,
    shape_note_results,
    NoteRecord,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)

LONG = "hash " + ("x" * 1000)


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "proj"
    (root / ".aimfp-project").mkdir(parents=True)
    conn = sqlite3.connect(root / ".aimfp-project" / "project.db")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.executemany(
        "INSERT INTO notes (content, note_type) VALUES (?, ?)",
        [(LONG, "decision")] * 25 + [("short hash note", "completed")],
    )
    conn.commit()
    conn.close()
    yield str(root)
    clear_project_root_cache()


def _note(content: str) -> NoteRecord:
    return NoteRecord(
        id=1, content=content, note_type="info", reference_table=None, reference_id=None,
        source="ai", directive_name=None, severity="info", send_with_directive=False,
        created_at="", updated_at="",
    )


class TestShapeNoteResults:
    def test_preview_marks_truncated_only_when_shortened(self):
        long_note, short_note = shape_note_results((_note("abcdef"), _note("ab")), None, 3)
        assert long_note.content == "abc…" and long_note.content_truncated
        assert short_note.content == "ab" and not short_note.content_truncated

    def test_zero_or_none_preview_is_full_content(self):
        notes = (_note("abcdef"),)
        assert shape_note_results(notes, None, 0) == notes
        assert shape_note_results(notes, None, None) == notes

    def test_limit(self):
        notes = tuple(_note(str(i)) for i in range(5))
        assert len(shape_note_results(notes, 2, None)) == 2


class TestGetNotesComprehensive:
    def test_note_id_returns_full_content(self, project_root):
        r = get_notes_comprehensive(note_id=3, project_root=project_root)
        assert r.success, r.error
        assert [n.id for n in r.notes] == [3]
        assert r.notes[0].content == LONG and not r.notes[0].content_truncated

    def test_missing_note_id_is_error(self, project_root):
        r = get_notes_comprehensive(note_id=999, project_root=project_root)
        assert not r.success and "999" in r.error

    def test_note_id_combined_with_excluding_filter(self, project_root):
        r = get_notes_comprehensive(note_id=26, exclude_note_types=["completed"], project_root=project_root)
        assert not r.success

    def test_defaults_unchanged_full_and_unlimited(self, project_root):
        r = get_notes_comprehensive(project_root=project_root)
        assert r.success and len(r.notes) == 26 == r.total_count
        assert not any(n.content_truncated for n in r.notes)

    def test_limit_and_preview(self, project_root):
        r = get_notes_comprehensive(note_type="decision", limit=5, preview_chars=10, project_root=project_root)
        assert r.success and len(r.notes) == 5 and r.total_count == 25
        assert all(n.content_truncated and len(n.content) == 11 for n in r.notes)


class TestSearchNotes:
    def test_default_caps_and_previews(self, project_root):
        r = search_notes("hash", project_root=project_root)
        assert r.success, r.error
        assert len(r.notes) == 20 and r.total_count == 26
        long_hits = [n for n in r.notes if n.note_type == "decision"]
        assert long_hits and all(n.content_truncated and len(n.content) == 301 for n in long_hits)

    def test_full_content_and_no_limit_on_request(self, project_root):
        r = search_notes("hash", limit=None, preview_chars=0, project_root=project_root)
        assert r.success and len(r.notes) == 26
        assert not any(n.content_truncated for n in r.notes)

    def test_filters_still_apply(self, project_root):
        r = search_notes("hash", exclude_note_types=["decision"], project_root=project_root)
        assert [n.content for n in r.notes] == ["short hash note"]

    @pytest.mark.parametrize("kwargs", [{"limit": 0}, {"preview_chars": -1}])
    def test_invalid_shaping_rejected(self, project_root, kwargs):
        r = search_notes("hash", project_root=project_root, **kwargs)
        assert not r.success
