"""
Deletion trail for user_directives.db (schema 1.4).

svamanas keeps a cursor over entry_deletion notes to deactivate its links to
deleted AIMFP rows (docs/svamanas/scope-and-deletion-feedback-2026-09-22.md,
item B). project.db delete helpers always wrote one; delete_user_custom_entry
wrote nothing. It now writes one note per delete, in the same transaction, and
names every row the delete's ON DELETE CASCADE removed so a consumer never
needs the foreign-key graph.
"""
import json
import os
import sqlite3
import tempfile

import pytest

from aimfp.database.connection import (
    _get_table_sql,
    _parse_check_constraint,
    clear_project_root_cache,
    set_project_root,
)
from aimfp.helpers.user_directives._common import (
    VALID_NOTE_TYPES,
    _collect_cascade_ids,
    _merge_id_maps,
)
from aimfp.helpers.user_directives.crud import (
    _deletion_note,
    delete_user_custom_entry,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)


def _schema() -> str:
    with open(SCHEMA_PATH) as f:
        return f.read()


def _schema_1_3() -> str:
    """The current schema with the 1.4 note type removed: a 1.3 notes CHECK."""
    sql = _schema()
    start = sql.index("        -- Deletion trail, written by delete_user_custom_entry")
    end = sql.index("    )),", start)
    return (sql[:start].replace("'obsolete',        --", "'obsolete'         --")
            + sql[end:]).replace("VALUES (1, '1.4')", "VALUES (1, '1.3')")


@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


def _project(schema_sql: str) -> str:
    root = tempfile.mkdtemp(prefix="aimfp_deltrail_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    conn = sqlite3.connect(_db(root))
    conn.executescript(schema_sql)
    conn.close()
    set_project_root(root)
    return root


def _db(root: str) -> str:
    return os.path.join(root, ".aimfp-project", "user_directives.db")


def _directive(conn: sqlite3.Connection, name: str) -> int:
    return conn.execute(
        """
        INSERT INTO user_directives
            (name, source_file, source_format, raw_content, validated_content,
             trigger_type, trigger_config, action_type, action_config, status)
        VALUES (?, 'd.yaml', 'yaml', 'raw', '{}', 'manual', '{}', 'api_call',
                '{"endpoint": "/x"}', 'active')
        """,
        (name,),
    ).lastrowid


def _seed_with_children(root: str):
    """Directive A with an execution row and a relationship to directive B."""
    conn = sqlite3.connect(_db(root))
    a = _directive(conn, "backup_nightly")
    b = _directive(conn, "notify_admin")
    execution = conn.execute(
        "INSERT INTO directive_executions (directive_id) VALUES (?)", (a,)
    ).lastrowid
    relationship = conn.execute(
        "INSERT INTO directive_relationships "
        "(source_directive_id, target_directive_id, relationship_type) "
        "VALUES (?, ?, 'triggers')", (a, b)
    ).lastrowid
    conn.commit()
    conn.close()
    return a, b, execution, relationship


def _notes(root: str, note_type: str = "entry_deletion"):
    conn = sqlite3.connect(_db(root))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM notes WHERE note_type = ? ORDER BY id", (note_type,))]
    conn.close()
    return rows


# ============================================================================
# Schema
# ============================================================================

def test_schema_notes_check_matches_valid_note_types():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_schema())
    allowed = _parse_check_constraint(_get_table_sql(conn, "notes"), "note_type")
    conn.close()
    assert allowed is not None
    assert set(allowed) == VALID_NOTE_TYPES
    assert "entry_deletion" in allowed


def test_check_parser_ignores_parentheses_in_sql_comments():
    sql = (
        "CREATE TABLE t (kind TEXT CHECK (kind IN (\n"
        "    'a',  -- first (with parens)\n"
        "    'b'   -- second\n"
        ")))"
    )
    assert _parse_check_constraint(sql, "kind") == ("a", "b")


# ============================================================================
# Pure
# ============================================================================

def test_merge_id_maps_unions_and_sorts():
    merged = _merge_id_maps(({"x": (3, 1)}, {"x": (1, 2), "y": (5,)}))
    assert merged == {"x": (1, 2, 3), "y": (5,)}


def test_deletion_note_without_reason_still_describes_the_delete():
    content, metadata_json = _deletion_note(
        "user_directives", 7, "backup_nightly", None, "user",
        {"directive_executions": (4,)})
    assert "user_directives id=7 (backup_nightly)" in content
    assert "no reason given" in content
    assert "1 directive_executions" in content
    assert json.loads(metadata_json) == {
        "source": "user", "cascade": {"directive_executions": [4]}}


# ============================================================================
# The helper
# ============================================================================

def test_delete_writes_one_note_naming_row_and_cascade():
    root = _project(_schema())
    a, b, execution, relationship = _seed_with_children(root)

    result = delete_user_custom_entry(
        "user_directives", a, note_reason="Removed from source file",
        note_source="user")

    assert result.success, result.error
    assert "entry_deletion note written" in result.message
    notes = _notes(root)
    assert len(notes) == 1
    note = notes[0]
    assert note["reference_type"] == "user_directives"
    assert note["reference_id"] == a
    assert note["reference_name"] == "backup_nightly"
    assert "Removed from source file" in note["content"]
    metadata = json.loads(note["metadata_json"])
    assert metadata["source"] == "user"
    assert metadata["cascade"] == {
        "directive_executions": [execution],
        "directive_relationships": [relationship],
    }

    # The cascade really happened, and the unrelated directive survived.
    conn = sqlite3.connect(_db(root))
    assert conn.execute("SELECT COUNT(*) FROM directive_executions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM directive_relationships").fetchone()[0] == 0
    assert conn.execute("SELECT id FROM user_directives").fetchall() == [(b,)]
    conn.close()


def test_delete_without_reason_still_writes_the_trail():
    root = _project(_schema())
    conn = sqlite3.connect(_db(root))
    a = _directive(conn, "d")
    conn.commit()
    conn.close()

    result = delete_user_custom_entry("user_directives", a)

    assert result.success, result.error
    notes = _notes(root)
    assert len(notes) == 1
    assert "no reason given" in notes[0]["content"]
    assert json.loads(notes[0]["metadata_json"]) == {"source": "ai", "cascade": {}}


def test_delete_of_a_nameless_row_leaves_reference_name_null():
    root = _project(_schema())
    a, _, execution, _ = _seed_with_children(root)

    result = delete_user_custom_entry("directive_executions", execution)

    assert result.success, result.error
    note = _notes(root)[0]
    assert note["reference_type"] == "directive_executions"
    assert note["reference_id"] == execution
    assert note["reference_name"] is None


def test_invalid_note_source_is_refused_and_nothing_is_deleted():
    root = _project(_schema())
    conn = sqlite3.connect(_db(root))
    a = _directive(conn, "d")
    conn.commit()
    conn.close()

    result = delete_user_custom_entry("user_directives", a, note_source="robot")

    assert result.success is False
    assert "note_source" in result.error
    assert _notes(root) == []
    conn = sqlite3.connect(_db(root))
    assert conn.execute("SELECT COUNT(*) FROM user_directives").fetchone()[0] == 1
    conn.close()


def test_missing_record_writes_no_note():
    root = _project(_schema())
    result = delete_user_custom_entry("user_directives", 999)
    assert result.success is False
    assert _notes(root) == []


def test_collect_cascade_ids_is_read_only_and_excludes_the_parent():
    root = _project(_schema())
    a, b, execution, relationship = _seed_with_children(root)
    conn = sqlite3.connect(_db(root))
    conn.execute("PRAGMA foreign_keys = ON")
    cascade = _collect_cascade_ids(conn, "user_directives", (a,))
    count = conn.execute("SELECT COUNT(*) FROM directive_executions").fetchone()[0]
    conn.close()
    assert cascade == {
        "directive_executions": (execution,),
        "directive_relationships": (relationship,),
    }
    assert "user_directives" not in cascade
    assert count == 1


# ============================================================================
# Unmigrated 1.3 database
# ============================================================================

def test_schema_1_3_database_still_deletes_and_says_to_migrate():
    root = _project(_schema_1_3())
    conn = sqlite3.connect(_db(root))
    a = _directive(conn, "d")
    conn.commit()
    conn.close()

    result = delete_user_custom_entry("user_directives", a, note_reason="x")

    assert result.success, result.error
    assert "migrate_databases" in result.message
    conn = sqlite3.connect(_db(root))
    assert conn.execute("SELECT COUNT(*) FROM user_directives").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    conn.close()


def test_schema_1_3_database_migrates_to_1_4_with_notes_intact():
    from aimfp.helpers.orchestrators.migration import migrate_databases

    root = _project(_schema_1_3())
    conn = sqlite3.connect(_db(root))
    conn.execute(
        "INSERT INTO notes (content, note_type) VALUES ('kept', 'lifecycle')")
    conn.commit()
    conn.close()

    result = migrate_databases()

    assert result.success, result.error
    migrated = [m for m in result.data["migrated"] if m["db_name"] == "user_directives"]
    assert len(migrated) == 1
    entry = migrated[0]
    assert (entry["old_version"], entry["new_version"]) == ("1.3", "1.4")
    conn = sqlite3.connect(entry["new_db_temp_path"])
    kept = conn.execute("SELECT content FROM notes").fetchall()
    conn.execute(
        "INSERT INTO notes (content, note_type) VALUES ('trail', 'entry_deletion')")
    conn.close()
    for path in (entry["new_db_temp_path"], entry["backup_temp_path"]):
        os.remove(path)
    assert kept == [("kept",)]
