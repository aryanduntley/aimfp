"""
Note reference tests (project.db schema v1.14).

Covers:
- pure helpers: reference table filtering, unknown-table error with hint, gated statements
- query_note_refs: batch lookup, excluded note types, empty input
- single-entity getters carry note refs; the [when:notes] statement follows them
- get_task_context: refs by default, full history with include_history
- add_note / update_note refuse unknown reference tables
"""
import os
import shutil
import sqlite3
import tempfile

import pytest

from aimfp.helpers.utils import set_project_root, clear_project_root_cache
from aimfp.helpers.project.note_refs import (
    NoteRef,
    note_reference_tables,
    reference_table_error,
    note_ref_return_statements,
    query_note_refs,
)
from aimfp.helpers.project.files_1 import get_file_by_name, get_file_by_path
from aimfp.helpers.project.functions_1 import get_function_by_name
from aimfp.helpers.project.types_1 import get_type_by_name
from aimfp.helpers.project.themes_flows_1 import get_theme_by_name, get_flow_by_name
from aimfp.helpers.project.modules import get_module_by_name, get_module_by_path
from aimfp.helpers.project.items_notes import add_note, update_note
from aimfp.helpers.orchestrators.status import get_task_context

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)

NOTES_STATEMENT_PREFIX = "notes lists the notes attached"


@pytest.fixture
def project():
    """Temp project with one of each entity; notes attached to several of them."""
    root = tempfile.mkdtemp(prefix="aimfp_nr_test_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    db = os.path.join(root, ".aimfp-project", "project.db")
    conn = sqlite3.connect(db)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO themes (name) VALUES ('Core')")
    conn.execute("INSERT INTO flows (name) VALUES ('Main Flow')")
    conn.execute("INSERT INTO completion_path (name, order_index) VALUES ('main', 1)")
    conn.execute("INSERT INTO milestones (completion_path_id, name) VALUES (1, 'M1')")
    conn.execute("INSERT INTO tasks (milestone_id, name) VALUES (1, 'T1')")
    conn.execute("INSERT INTO files (name, path, language) VALUES ('calc', 'src/calc.py', 'python')")
    conn.execute("INSERT INTO files (name, path, language) VALUES ('calc', 'lib/calc.py', 'python')")
    conn.execute("INSERT INTO functions (name, file_id) VALUES ('add', 1)")
    conn.execute("INSERT INTO types (name, file_id, definition_json) VALUES ('Money', 1, '{}')")
    conn.execute("INSERT INTO modules (name, path) VALUES ('math', 'src/')")
    notes = [
        ("stub left", "deferred", "files", 1, "warning"),
        ("old fix", "completed", "files", 1, "info"),
        ("removed thing", "entry_deletion", "files", 1, "info"),
        ("lib note", "decision", "files", 2, "info"),
        ("fn note", "analysis", "functions", 1, "info"),
        ("type note", "decision", "types", 1, "info"),
        ("theme note", "evolution", "themes", 1, "info"),
        ("flow note", "evolution", "flows", 1, "info"),
        ("module note", "decision", "modules", 1, "info"),
        ("task note", "decision", "tasks", 1, "info"),
    ]
    conn.executemany(
        "INSERT INTO notes (content, note_type, reference_table, reference_id, severity) "
        "VALUES (?, ?, ?, ?, ?)",
        notes,
    )
    conn.commit()
    conn.close()
    clear_project_root_cache()
    set_project_root(root)
    yield root, db
    clear_project_root_cache()
    shutil.rmtree(root, ignore_errors=True)


def _has_notes_statement(statements):
    return any(NOTES_STATEMENT_PREFIX in s for s in statements)


def _no_raw_tags(statements):
    return not any(s.lstrip().startswith("[when:") for s in statements)


# ----------------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------------

def test_note_reference_tables_drops_internal_tables():
    names = ("files", "notes_fts", "notes_fts_data", "sqlite_sequence", "schema_version", "tasks")
    assert note_reference_tables(names) == frozenset({"files", "tasks"})


def test_reference_table_error_accepts_valid_and_none():
    valid = frozenset({"files", "completion_path"})
    assert reference_table_error(None, valid) is None
    assert reference_table_error("completion_path", valid) is None


def test_reference_table_error_hints_plural():
    err = reference_table_error("file", frozenset({"files", "tasks"}))
    assert "Unknown reference_table 'file'" in err and "Did you mean 'files'" in err


def test_reference_table_error_unknown_lists_valid_names():
    err = reference_table_error("completion_paths", frozenset({"completion_path", "files"}))
    assert "Did you mean" not in err
    assert "completion_path, files" in err


def test_note_ref_return_statements_gates_on_refs():
    statements = ("always", "[when:notes] attached")
    ref = (NoteRef(id=1, type="deferred", severity="info", reference_id=4),)
    assert note_ref_return_statements(statements, ()) == ("always",)
    assert note_ref_return_statements(statements, ref) == ("always", "attached")


# ----------------------------------------------------------------------------
# query_note_refs
# ----------------------------------------------------------------------------

def test_query_note_refs_batches_and_excludes_resolved(project):
    _, db = project
    conn = sqlite3.connect(db)
    try:
        refs = query_note_refs(conn, "files", [1, 2, None, 1])
    finally:
        conn.close()
    assert [(r.reference_id, r.type, r.severity) for r in refs] == [
        (1, "deferred", "warning"),
        (2, "decision", "info"),
    ]


def test_query_note_refs_empty_ids(project):
    _, db = project
    conn = sqlite3.connect(db)
    try:
        assert query_note_refs(conn, "files", []) == ()
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Getters
# ----------------------------------------------------------------------------

def test_get_file_by_name_refs_for_every_match(project):
    res = get_file_by_name("calc")
    assert res.success
    assert sorted(r.reference_id for r in res.notes) == [1, 2]
    assert _has_notes_statement(res.return_statements) and _no_raw_tags(res.return_statements)


def test_get_file_by_path_refs(project):
    res = get_file_by_path("src/calc.py")
    assert [(r.type, r.reference_id) for r in res.notes] == [("deferred", 1)]


def test_get_file_by_path_not_found_has_no_raw_tag(project):
    res = get_file_by_path("nope.py")
    assert res.file is None and res.notes == ()
    assert not _has_notes_statement(res.return_statements)
    assert _no_raw_tags(res.return_statements)


@pytest.mark.parametrize("getter, arg, expected_type", [
    (get_function_by_name, "add", "analysis"),
    (get_type_by_name, "Money", "decision"),
    (get_theme_by_name, "Core", "evolution"),
    (get_flow_by_name, "Main Flow", "evolution"),
    (get_module_by_name, "math", "decision"),
    (get_module_by_path, "src/", "decision"),
])
def test_entity_getters_carry_refs(project, getter, arg, expected_type):
    res = getter(arg)
    assert res.success
    assert [r.type for r in res.notes] == [expected_type]
    assert _has_notes_statement(res.return_statements) and _no_raw_tags(res.return_statements)


def test_getter_without_notes_omits_statement(project):
    _, db = project
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM notes WHERE reference_table = 'themes'")
    conn.commit()
    conn.close()
    res = get_theme_by_name("Core")
    assert res.notes == ()
    assert not _has_notes_statement(res.return_statements)


def test_task_context_refs_by_default(project):
    res = get_task_context(1, task_type="task")
    assert res.success
    assert [(n["type"], n["reference_id"]) for n in res.data["notes"]] == [("decision", 1)]
    assert _has_notes_statement(res.return_statements)


def test_task_context_history_returns_full_notes(project):
    res = get_task_context(1, task_type="task", include_history=True)
    assert res.data["notes"][0]["content"] == "task note"


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------

def test_add_note_refuses_unknown_table(project):
    res = add_note("x", "decision", reference_table="completion_paths", reference_id=1)
    assert not res.success and "Unknown reference_table" in res.error


def test_add_note_accepts_known_table_and_none(project):
    assert add_note("x", "decision", reference_table="completion_path", reference_id=1).success
    assert add_note("y", "decision").success


def test_update_note_refuses_unknown_table(project):
    res = update_note(1, reference_table="file")
    assert not res.success and "Did you mean 'files'" in res.error


@pytest.mark.parametrize("action, target_type, table", [
    ("start_path", "completion_path", "completion_path"),
    ("start_milestone", "milestone", "milestones"),
    ("start_task", "task", "tasks"),
])
def test_state_action_note_references_real_table(project, action, target_type, table):
    """update_project_state(create_note=True) once wrote 'completion_paths'."""
    from aimfp.helpers.orchestrators.state import update_project_state
    _, db = project
    res = update_project_state(action, target_type, 1, create_note=True)
    assert res.success, res.error
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT reference_table FROM notes WHERE id = ?", (res.data["note_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == table
