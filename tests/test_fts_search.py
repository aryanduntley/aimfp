"""
FTS5 search query-building tests.

Covers the shared builder (helpers/shared/fts_query.py) and every search helper
that uses it: multi-word queries match on ANY word, punctuation never falls
through to a whole-phrase LIKE, and rows matching more words rank first.
"""
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.shared.fts_query import (
    tokenize_search_terms,
    build_fts_match_expression,
    build_like_clause,
)
from aimfp.helpers.project.functions_1 import search_functions
from aimfp.helpers.project.types_1 import search_types
from aimfp.helpers.project.modules import search_modules
from aimfp.helpers.project.items_notes import search_notes
from aimfp.helpers.core.directives_1 import search_directives

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    """A temp project on the current schema with a few searchable rows."""
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "proj"
    (root / ".aimfp-project").mkdir(parents=True)
    conn = sqlite3.connect(root / ".aimfp-project" / "project.db")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO files (id, name, path, language) VALUES (1, 'disk', 'src/disk.py', 'python')")
    conn.executemany(
        "INSERT INTO functions (name, file_id, purpose) VALUES (?, 1, ?)",
        [
            ("get_free_disk_space", "Return bytes available on the volume"),
            ("hash_file_contents", "Compute a sha256 digest of a file"),
            ("disk_usage_report", "Summarize usage per directory"),
        ],
    )
    conn.execute(
        "INSERT INTO types (name, file_id, definition_json, description) VALUES ('DiskStats', 1, '{}', 'Free and used disk space')"
    )
    conn.execute(
        "INSERT INTO modules (name, path, purpose) VALUES ('storage', 'src/storage/', 'Disk space accounting')"
    )
    conn.executemany(
        "INSERT INTO notes (content, note_type) VALUES (?, 'decision')",
        [
            ("We hash files with sha256 for change detection.", ),
            ("Digest comparison replaces mtime checks.", ),
            ("Unrelated note about themes.", ),
        ],
    )
    conn.commit()
    conn.close()
    yield str(root)
    clear_project_root_cache()


# ---------------------------------------------------------------------------
# Pure builder
# ---------------------------------------------------------------------------

class TestBuilder:
    def test_splits_words_and_punctuation(self):
        assert tokenize_search_terms("disk-space free") == ("disk", "space", "free")

    def test_splits_snake_case_like_fts_tokenizer(self):
        assert tokenize_search_terms("get_free_disk") == ("get", "free", "disk")

    def test_drops_uppercase_operators_and_dedupes(self):
        assert tokenize_search_terms("hash OR digest OR Hash") == ("hash", "digest")

    def test_drops_single_char_fragments_unless_alone(self):
        assert tokenize_search_terms("what's") == ("what",)
        assert tokenize_search_terms("x") == ("x",)

    def test_empty_and_symbol_only(self):
        assert tokenize_search_terms("") == ()
        assert tokenize_search_terms("--- ''") == ()

    def test_match_expression_quotes_prefix_terms(self):
        assert build_fts_match_expression(("disk", "space")) == '"disk"* OR "space"*'

    def test_like_clause_any_term_any_column(self):
        sql, params = build_like_clause(("a", "b"), ("x", "y"))
        assert sql == "((a LIKE ? OR b LIKE ?) OR (a LIKE ? OR b LIKE ?))"
        assert params == ("%x%", "%x%", "%y%", "%y%")

    def test_like_clause_no_terms_matches_nothing(self):
        assert build_like_clause(("a",), ()) == ("(0)", ())


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

class TestSearchHelpers:
    def test_functions_multi_word_with_unmatched_word(self, project_root):
        r = search_functions("disk space free check", project_root=project_root)
        assert r.success, r.error
        names = [f.name for f in r.functions]
        assert names[0] == "get_free_disk_space"
        assert "disk_usage_report" in names

    def test_functions_punctuation_does_not_break(self, project_root):
        r = search_functions("disk-space", project_root=project_root)
        assert r.success, r.error
        assert "get_free_disk_space" in [f.name for f in r.functions]

    def test_functions_prefix_match(self, project_root):
        r = search_functions("hashing", project_root=project_root)
        assert r.success and r.functions == ()
        r = search_functions("has", project_root=project_root)
        assert [f.name for f in r.functions] == ["hash_file_contents"]

    def test_functions_symbol_only_returns_empty(self, project_root):
        r = search_functions("???", project_root=project_root)
        assert r.success and r.functions == ()

    def test_types_multi_word(self, project_root):
        r = search_types("free space missing", project_root=project_root)
        assert r.success, r.error
        assert [t.name for t in r.types] == ["DiskStats"]

    def test_modules_multi_word(self, project_root):
        r = search_modules("disk quota", project_root=project_root)
        assert r.success, r.error
        assert [m.name for m in r.modules] == ["storage"]

    def test_notes_or_semantics(self, project_root):
        r = search_notes("hash digest", project_root=project_root)
        assert r.success, r.error
        assert len(r.notes) == 2

    def test_like_fallback_is_per_term(self, project_root):
        conn = sqlite3.connect(os.path.join(project_root, ".aimfp-project", "project.db"))
        conn.executescript(
            "DROP TRIGGER functions_fts_insert; DROP TRIGGER functions_fts_delete; "
            "DROP TRIGGER functions_fts_update; DROP TABLE functions_fts;"
        )
        conn.close()
        r = search_functions("volume nonexistent", project_root=project_root)
        assert r.success, r.error
        assert [f.name for f in r.functions] == ["get_free_disk_space"]


class TestSearchDirectives:
    def test_multi_word_ranks_exact_directive_first(self):
        r = search_directives(keyword="task-decomposition")
        assert r.success, r.error
        assert r.directives[0].name == "project_task_decomposition"

    def test_filters_still_apply(self):
        r = search_directives(keyword="error", type="fp")
        assert r.success, r.error
        assert r.directives and all(d.type == "fp" for d in r.directives)

    def test_symbol_only_keyword_returns_empty(self):
        r = search_directives(keyword="!!")
        assert r.success and r.directives == ()
