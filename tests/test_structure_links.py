"""
Structure link tests (project.db schema v1.13).

Covers:
- add_flow requires theme_ids and writes flow + flow_themes together
- add/remove_flow_themes, refusing to leave a flow theme-less
- add/remove_path_themes and add/remove_milestone_flows
- theme_ids on add_completion_path, flow_ids on add_milestone
- get_themes_for_path / get_flows_for_milestone
"""
import os
import shutil
import sqlite3
import tempfile

import pytest

from aimfp.helpers.utils import set_project_root, clear_project_root_cache
from aimfp.helpers.project.themes_flows_1 import add_flow
from aimfp.helpers.project.themes_flows_2 import add_completion_path
from aimfp.helpers.project.tasks import add_milestone
from aimfp.helpers.project.structure_links import (
    move_files_to_flow,
    move_flows_to_theme,
    add_flow_themes,
    remove_flow_themes,
    add_path_themes,
    remove_path_themes,
    add_milestone_flows,
    remove_milestone_flows,
    get_themes_for_path,
    get_flows_for_milestone,
    unique_ids,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)


@pytest.fixture
def project():
    """Temp project with two themes, one path, one milestone, one flow linked to theme 1."""
    root = tempfile.mkdtemp(prefix="aimfp_sl_test_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    db = os.path.join(root, ".aimfp-project", "project.db")
    conn = sqlite3.connect(db)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO themes (name) VALUES ('Core'), ('Tooling')")
    conn.execute("INSERT INTO completion_path (name, order_index) VALUES ('main', 1)")
    conn.execute("INSERT INTO milestones (completion_path_id, name) VALUES (1, 'M1')")
    conn.execute("INSERT INTO flows (name) VALUES ('Existing Flow')")
    conn.execute("INSERT INTO flow_themes (flow_id, theme_id) VALUES (1, 1)")
    conn.commit()
    conn.close()
    clear_project_root_cache()
    set_project_root(root)
    yield root, db
    clear_project_root_cache()
    shutil.rmtree(root, ignore_errors=True)


def _count(db, sql, params=()):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, params).fetchone()[0]
    finally:
        c.close()


def test_unique_ids_drops_none_and_repeats():
    assert unique_ids([3, None, 1, 3]) == (3, 1)
    assert unique_ids(None) == ()


def test_add_flow_requires_a_theme(project):
    _, db = project
    res = add_flow("Orphan", theme_ids=[])
    assert not res.success and "theme_ids is required" in res.error
    assert _count(db, "SELECT COUNT(*) FROM flows WHERE name='Orphan'") == 0


def test_add_flow_unknown_theme_writes_nothing(project):
    _, db = project
    res = add_flow("Ghost", theme_ids=[99])
    assert not res.success and "99" in res.error
    assert _count(db, "SELECT COUNT(*) FROM flows WHERE name='Ghost'") == 0


def test_add_flow_links_themes(project):
    _, db = project
    res = add_flow("New Flow", theme_ids=[1, 2, 2], description="d")
    assert res.success and res.theme_ids == (1, 2)
    assert _count(db, "SELECT COUNT(*) FROM flow_themes WHERE flow_id=?", (res.id,)) == 2


NOTE = dict(note_reason="regrouping", note_severity="info", note_source="ai")


def test_flow_themes_batch_add_remove_and_last_theme_guard(project):
    _, db = project
    added = add_flow_themes([[1, 2], [1, 1]])
    assert added.success and added.added_count == 1 and added.skipped_count == 1
    assert added.linked == ({"owner_id": 1, "target_ids": (1, 2)},)

    assert "note_reason is required" in remove_flow_themes([[1, 1]], "", "info", "ai").error
    assert "Invalid note_source" in remove_flow_themes([[1, 1]], "x", "info", "bot").error

    removed = remove_flow_themes([[1, 1]], **NOTE)
    assert removed.success and removed.linked == ({"owner_id": 1, "target_ids": (2,)},)
    assert _count(db, "SELECT COUNT(*) FROM notes WHERE note_type='entry_deletion' "
                      "AND reference_table='flows' AND reference_id=1") == 1

    refused = remove_flow_themes([[1, 2]], **NOTE)
    assert not refused.success and "at least 1 theme" in refused.error
    assert _count(db, "SELECT COUNT(*) FROM flow_themes WHERE flow_id=1") == 1


def test_link_batches_are_all_or_nothing(project):
    _, db = project
    assert not add_flow_themes([]).success
    assert "Flow ID(s) not found: [42]" in add_flow_themes([[1, 2], [42, 1]]).error
    assert "Theme ID(s) not found: [7]" in add_flow_themes([[1, 2], [1, 7]]).error
    assert _count(db, "SELECT COUNT(*) FROM flow_themes") == 1


def test_path_themes_round_trip(project):
    assert add_path_themes([[1, 1], [1, 2]]).linked[0]["target_ids"] == (1, 2)
    names = [r["name"] for r in get_themes_for_path(1).rows]
    assert names == ["Core", "Tooling"]
    res = remove_path_themes([[1, 1], [1, 2]], **NOTE)
    assert res.unlinked_owner_ids == (1,)  # paths may end up theme-less
    assert not get_themes_for_path(5).success


def test_milestone_flows_round_trip_and_open_task_guard(project):
    _, db = project
    assert add_milestone_flows([[1, 1]]).added_count == 1
    assert [r["name"] for r in get_flows_for_milestone(1).rows] == ["Existing Flow"]

    c = sqlite3.connect(db)
    c.execute("INSERT INTO tasks (milestone_id, name, status, flow_ids) VALUES (1, 'T', 'in_progress', '[1]')")
    c.commit()
    blocked = remove_milestone_flows([[1, 1]], **NOTE)
    assert not blocked.success and "task:T" in blocked.error

    c.execute("UPDATE tasks SET status='completed'")
    c.commit()
    c.close()
    assert remove_milestone_flows([[1, 1]], **NOTE).removed_count == 1
    assert get_flows_for_milestone(1).rows == ()


def test_move_files_and_flows(project):
    _, db = project
    c = sqlite3.connect(db)
    c.execute("INSERT INTO flows (name) VALUES ('Split Off')")
    c.executemany("INSERT INTO files (path, name) VALUES (?, ?)", [("a.py", "a"), ("b.py", "b"), ("c.py", "c")])
    c.executemany("INSERT INTO file_flows (file_id, flow_id) VALUES (?, 1)", [(1,), (2,)])
    c.commit()
    c.close()
    moved = move_files_to_flow([1, 2, 3], 1, 2)
    assert moved.success and moved.removed_count == 2 and moved.skipped_count == 1
    assert _count(db, "SELECT COUNT(*) FROM file_flows WHERE flow_id=2") == 2
    assert _count(db, "SELECT COUNT(*) FROM files") == 3  # links only, files untouched
    assert not move_files_to_flow([1], 2, 2).success
    assert "Flow ID(s) not found" in move_files_to_flow([1], 2, 99).error

    themed = move_flows_to_theme([1], 1, 2)
    assert themed.success and themed.linked == ({"owner_id": 1, "target_ids": (2,)},)


def test_deletes_blocked_by_new_associations(project):
    from aimfp.helpers.project.themes_flows_1 import delete_theme, delete_flow
    from aimfp.helpers.project.themes_flows_2 import delete_completion_path
    from aimfp.helpers.project.tasks import delete_milestone
    c = sqlite3.connect(sqlite3_path(project))
    c.execute("INSERT INTO completion_path (name, order_index) VALUES ('spare', 2)")
    c.commit()
    c.close()
    add_path_themes([[2, 2]])
    add_milestone_flows([[1, 1]])

    theme = delete_theme(2, "x", "info", "ai")
    assert not theme.success and theme.error == "paths_linked" and theme.paths == ("spare",)
    path = delete_completion_path(2, "x", "info", "ai")
    assert not path.success and path.error == "themes_linked" and path.themes == ("Tooling",)
    flow = delete_flow(1, "x", "info", "ai")
    assert not flow.success and flow.milestones == ("M1",)
    ms = delete_milestone(1, "x", "info", "ai")
    assert not ms.success and "remove_milestone_flows first" in ms.error


def sqlite3_path(project):
    return project[1]


def test_add_completion_path_and_milestone_accept_links(project):
    _, db = project
    path = add_completion_path("second", order_index=2, theme_ids=[2])
    assert path.success
    assert _count(db, "SELECT theme_id FROM completion_path_themes WHERE completion_path_id=?", (path.id,)) == 2

    bad = add_milestone(path.id, "M2", flow_ids=[50])
    assert not bad.success
    assert _count(db, "SELECT COUNT(*) FROM milestones WHERE name='M2'") == 0

    ms = add_milestone(path.id, "M2", flow_ids=[1])
    assert ms.success
    assert _count(db, "SELECT COUNT(*) FROM milestone_flows WHERE milestone_id=?", (ms.id,)) == 1


def test_cascades_clean_up_junctions(project):
    _, db = project
    add_path_themes([[1, 1]])
    add_milestone_flows([[1, 1]])
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("DELETE FROM milestones WHERE id=1")
    c.execute("DELETE FROM completion_path WHERE id=1")
    c.commit()
    c.close()
    assert _count(db, "SELECT COUNT(*) FROM milestone_flows") == 0
    assert _count(db, "SELECT COUNT(*) FROM completion_path_themes") == 0


# ----------------------------------------------------------------------------
# reserve_file(s) flow gate and update_file no_flow_reason
# ----------------------------------------------------------------------------

from aimfp.helpers.project.files_1 import reserve_file, reserve_files
from aimfp.helpers.project.files_2 import update_file
from aimfp.helpers.project.structure_links import (
    DEFAULT_NO_FLOW_REASON,
    flow_choice_error,
    no_flow_reason_for,
)


def test_flow_choice_error_rules():
    assert flow_choice_error([1], None) is None
    assert flow_choice_error(None, None) is None
    assert flow_choice_error(None, "config") is None
    assert "flow_ids is required" in flow_choice_error([], None)
    assert "flow_ids is required" in flow_choice_error("<flow_ids not declared>", None)
    assert "only valid with flow_ids=null" in flow_choice_error([1], "config")
    assert no_flow_reason_for(None, "  ") == DEFAULT_NO_FLOW_REASON
    assert no_flow_reason_for(None, "config") == "config"
    assert no_flow_reason_for([1], "ignored") is None


def test_reserve_file_links_declared_flows(project):
    _, db = project
    res = reserve_file("calc", "src/calc.py", "python", [1])
    assert res.success and res.flow_ids == (1,)
    assert _count(db, "SELECT COUNT(*) FROM file_flows WHERE file_id=?", (res.id,)) == 1
    assert _count(db, "SELECT no_flow_reason IS NULL FROM files WHERE id=?", (res.id,)) == 1


def test_reserve_file_refuses_missing_or_unknown_flows(project):
    _, db = project
    assert "flow_ids is required" in reserve_file("a", "src/a.py", "python", []).error
    assert "Flow ID(s) not found" in reserve_file("b", "src/b.py", "python", [9]).error
    assert _count(db, "SELECT COUNT(*) FROM files") == 0


def test_reserve_file_explicit_null_records_reason(project):
    _, db = project
    res = reserve_file("cfg", "pyproject.toml", "toml", None, no_flow_reason="build config")
    assert res.success and res.flow_ids == ()
    c = sqlite3.connect(db)
    assert c.execute("SELECT no_flow_reason FROM files WHERE id=?", (res.id,)).fetchone()[0] == "build config"
    c.close()
    bare = reserve_file("data", "data.json", "json", None)
    assert bare.success
    assert _count(db, "SELECT no_flow_reason = ? FROM files WHERE id=?",
                  (DEFAULT_NO_FLOW_REASON, bare.id)) == 1


def test_reserve_files_requires_declaration_per_file(project):
    _, db = project
    bad = reserve_files([{"name": "a", "path": "src/a.py", "language": "python", "flow_ids": [1]},
                         {"name": "b", "path": "src/b.py", "language": "python"}])
    assert not bad.success and "src/b.py" in bad.error
    assert _count(db, "SELECT COUNT(*) FROM files") == 0

    ok = reserve_files([{"name": "a", "path": "src/a.py", "language": "python", "flow_ids": [1]},
                        {"name": "i", "path": "src/__init__.py", "language": "python",
                         "flow_ids": None, "no_flow_reason": "package marker"},
                        ("t", "src/t.py", "python", True, [1])])
    assert ok.success and len(ok.ids) == 3
    assert _count(db, "SELECT COUNT(*) FROM file_flows") == 2


def test_update_file_sets_and_clears_no_flow_reason(project):
    _, db = project
    res = reserve_file("calc", "src/calc.py", "python", [1])
    assert update_file(res.id, no_flow_reason="generated").success
    assert _count(db, "SELECT no_flow_reason = 'generated' FROM files WHERE id=?", (res.id,)) == 1
    assert update_file(res.id, no_flow_reason="").success
    assert _count(db, "SELECT no_flow_reason IS NULL FROM files WHERE id=?", (res.id,)) == 1


def test_mcp_schema_requires_flow_ids_but_accepts_explicit_null():
    from aimfp.mcp_server.arguments import validate_arguments
    flow_any = {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]}
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "flow_ids": flow_any},
              "required": ["name", "flow_ids"]}
    assert validate_arguments(schema, {"name": "x"}) == ("flow_ids: required",)
    assert validate_arguments(schema, {"name": "x", "flow_ids": None}) == ()
    assert validate_arguments(schema, {"name": None, "flow_ids": [1]}) == ("name: required",)


# ----------------------------------------------------------------------------
# structure health
# ----------------------------------------------------------------------------

from aimfp.helpers.project.structure_health import (
    build_structure_gaps,
    get_structure_health,
    owning_module_path,
    query_structure_rows,
)


def test_owning_module_path_prefers_most_specific():
    paths = ("src/", "src/core/", "src/core/db")
    assert owning_module_path("src/core/db/x.py", paths) == "src/core/db"
    assert owning_module_path("src/core/y.py", paths) == "src/core/"
    assert owning_module_path("tests/t.py", paths) is None
    assert owning_module_path("src/coreish.py", ("src/core",)) is None


def test_structure_health_reports_each_gap(project):
    _, db = project
    c = sqlite3.connect(db)
    c.execute("INSERT INTO flows (name, description) VALUES ('Huge', ?)", ("x" * 2000,))
    c.execute("INSERT INTO modules (name, path) VALUES ('core', 'src/core/')")
    c.executemany("INSERT INTO files (path, name, is_reserved, no_flow_reason) VALUES (?, ?, 0, ?)",
                  [("src/core/a.py", "a", None), ("cfg.toml", "cfg", "config"), ("src/core/b.py", "b", None)])
    c.execute("INSERT INTO file_flows (file_id, flow_id) VALUES (3, 1)")
    c.execute("INSERT INTO module_files (module_id, file_id) VALUES (1, 3)")
    c.commit()
    c.close()

    res = get_structure_health()
    assert res.success
    h = res.health
    assert not h["ok"]
    assert [f["name"] for f in h["flows_without_theme"]["items"]] == ["Huge"]
    assert [t["name"] for t in h["themes_without_flows"]["items"]] == ["Tooling"]
    assert [f["path"] for f in h["files_without_flow"]["items"]] == ["src/core/a.py"]  # cfg.toml exempt
    assert [f["path"] for f in h["files_outside_module"]["items"]] == ["src/core/a.py"]
    assert h["open_paths_without_themes"]["total"] == 1
    assert h["open_milestones_without_flows"]["total"] == 1
    assert h["oversized_flows"]["items"][0]["chars"] == 2000
    assert h["total_gaps"] == sum(v["total"] for k, v in h.items() if isinstance(v, dict))

    summary = res.summary
    core = next(t for t in summary["themes"] if t["name"] == "Core")
    assert core["flows"] == ({"id": 1, "name": "Existing Flow", "files": 1},)
    assert [f["name"] for f in summary["unthemed_flows"]] == ["Huge"]


def test_structure_health_ok_when_layer_complete(project):
    add_flow_themes([[1, 1]])
    add_path_themes([[1, 1], [1, 2]])
    add_milestone_flows([[1, 1]])
    c = sqlite3.connect(sqlite3_path(project))
    c.execute("INSERT INTO flows (name) VALUES ('Tool Flow')")
    c.execute("INSERT INTO flow_themes (flow_id, theme_id) VALUES (2, 2)")
    c.commit()
    c.close()
    assert get_structure_health(include_summary=False).health == {"ok": True, "total_gaps": 0}


def test_structure_rows_tolerate_pre_v113_database(tmp_path):
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE themes (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE flows (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
        CREATE TABLE flow_themes (flow_id INTEGER, theme_id INTEGER);
        CREATE TABLE file_flows (file_id INTEGER, flow_id INTEGER);
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, is_reserved INTEGER DEFAULT 0);
        CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT, path TEXT);
        CREATE TABLE module_files (module_id INTEGER, file_id INTEGER);
        CREATE TABLE completion_path (id INTEGER PRIMARY KEY, name TEXT, status TEXT, order_index INTEGER);
        CREATE TABLE milestones (id INTEGER PRIMARY KEY, name TEXT, status TEXT);
        INSERT INTO files (path) VALUES ('src/a.py');
    """)
    rows = query_structure_rows(c)
    assert rows.path_themes == () and rows.milestone_flows == ()
    assert rows.files[0]["no_flow_reason"] is None
    assert build_structure_gaps(rows)["files_without_flow"]["total"] == 1
    c.close()


# ----------------------------------------------------------------------------
# watchdog structural reminders
# ----------------------------------------------------------------------------

from aimfp.watchdog.config import get_reminders_path
from aimfp.watchdog.reconciliation import refresh_structure_reminders
from aimfp.watchdog.reminders import (
    _effect_append_reminders,
    _effect_read_reminders,
    build_structure_reminders,
    create_reminder,
    replace_reminders_by_prefix,
)


def test_build_structure_reminders_caps_and_formats():
    health = {
        "ok": False,
        "files_without_flow": {"total": 3, "items": ({"id": 1, "path": "a.py"}, {"id": 2, "path": "b.py"})},
        "flows_without_theme": {"total": 1, "items": ({"id": 4, "name": "F"},)},
    }
    rs = build_structure_reminders(health)
    assert [r["type"] for r in rs] == ["structure_file_without_flow"] * 3 + ["structure_flow_without_theme"]
    assert rs[0]["file"] == "a.py" and "add_file_flows" in rs[0]["message"]
    assert "1 more files without flow" in rs[2]["message"]
    assert "Flow 'F' (id 4)" in rs[3]["message"]


def test_replace_reminders_by_prefix_keeps_other_reminders():
    keep = create_reminder("file_deleted", "warning", "x.py", "gone")
    old = create_reminder("structure_file_without_flow", "warning", "a.py", "old")
    new = create_reminder("structure_flow_without_theme", "warning", "", "new")
    assert replace_reminders_by_prefix((keep, old), "structure_", (new,)) == (keep, new)


def test_refresh_structure_reminders_recomputes_from_db(project):
    root, db = project
    os.makedirs(os.path.dirname(get_reminders_path(root)), exist_ok=True)
    path = get_reminders_path(root)
    _effect_append_reminders(path, (create_reminder("file_deleted", "warning", "x.py", "gone"),))
    c = sqlite3.connect(db)
    c.execute("INSERT INTO files (path, name, is_reserved) VALUES ('src/new.py', 'new', 0)")
    c.commit()
    c.close()

    assert refresh_structure_reminders(root) > 0
    types = [r["type"] for r in _effect_read_reminders(path)]
    assert "file_deleted" in types and "structure_file_without_flow" in types

    # fixing the gap removes its reminder on the next refresh; others survive
    c = sqlite3.connect(db)
    c.execute("UPDATE files SET no_flow_reason = 'fixture' WHERE path = 'src/new.py'")
    c.commit()
    c.close()
    refresh_structure_reminders(root)
    types = [r["type"] for r in _effect_read_reminders(path)]
    assert "file_deleted" in types and "structure_file_without_flow" not in types
