"""
Lean session and status payloads (task 52).

- Return statements ship only with the data they talk about ([when:...] gates),
  and a bundle carries each present section's owning-tool statements.
- aimfp_status tiers: quick (default) is position only; summary is the reload.
- Structure is attached to the work and files shown, never listed whole.
- A new project starts with every shipped notice acknowledged and is not
  asked to back up an empty database.
"""
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.orchestrators.entry_points import aimfp_init, aimfp_run, aimfp_status
from aimfp.helpers.orchestrators.backup import check_scheduled_backup_due
from aimfp.helpers.orchestrators.notices import get_pending_notices
from aimfp.helpers.orchestrators.status_payload import (
    compact_infrastructure,
    compact_migration,
    compact_user_settings,
    drop_empty,
    focus_scope,
)
from aimfp.helpers.project.metadata import create_project
from aimfp.helpers.project.structure_health import (
    StructureRows,
    WorkFocus,
    compute_structure_gaps,
    focus_structure_gaps,
    structure_of_files,
    structure_of_flows,
)
from aimfp.helpers.shared.return_gates import (
    gate_return_statements,
    parse_gate,
    section_return_statements,
)


# ----------------------------------------------------------------------------
# return gates
# ----------------------------------------------------------------------------

def test_parse_gate_splits_tag_and_text():
    assert parse_gate("[when:a.b] do x") == ("a.b", None, "do x")
    assert parse_gate("[when:tier=quick] do y") == ("tier", "quick", "do y")
    assert parse_gate("plain") == (None, None, "plain")


def test_gates_follow_the_payload():
    stmts = ("always", "[when:notices] n", "[when:backup.due] b",
             "[when:initialized=false] i", "[when:count] c")
    assert gate_return_statements(stmts, {}) == ("always",)
    assert gate_return_statements(
        stmts, {"notices": [1], "backup": {"due": True}, "initialized": False, "count": 0}
    ) == ("always", "n", "b", "i")


def test_section_statements_only_for_present_sections():
    owners = (("notices", "get_pending_notices", ("deliver",)),
              ("status", "aimfp_status", ("[when:tier=quick] reload hint", "[when:tier=summary] s")))
    assert section_return_statements({}, owners) == ()
    data = {"notices": [{"k": 1}], "status": {"tier": "summary"}}
    assert section_return_statements(data, owners) == ("deliver", "s")


# ----------------------------------------------------------------------------
# structure scoping (pure)
# ----------------------------------------------------------------------------

ROWS = StructureRows(
    themes=({"id": 1, "name": "Core"},),
    flows=({"id": 1, "name": "Run", "description": ""}, {"id": 2, "name": "Loose", "description": ""}),
    flow_themes=({"flow_id": 1, "theme_id": 1},),
    file_flow_counts=({"flow_id": 1, "file_count": 1},),
    files=({"id": 10, "path": "src/m/a.py", "no_flow_reason": None, "flow_count": 1},
           {"id": 11, "path": "src/m/b.py", "no_flow_reason": None, "flow_count": 0}),
    modules=({"id": 5, "name": "m", "path": "src/m/"},),
    module_file_ids=(10,),
    file_flows=({"file_id": 10, "flow_id": 1},),
    module_files=({"file_id": 10, "module_id": 5},),
    paths=({"id": 1, "name": "P", "status": "in_progress"},),
    path_themes=(),
    milestones=({"id": 3, "name": "M", "status": "in_progress", "completion_path_id": 1},
                {"id": 4, "name": "Other", "status": "pending", "completion_path_id": 1}),
    milestone_flows=({"milestone_id": 3, "flow_id": 1},),
)


def test_files_carry_their_flows_themes_and_module():
    s = structure_of_files(ROWS, (10, 11))
    assert s[10] == {"flows": ({"id": 1, "name": "Run"},), "themes": ("Core",), "module": "m"}
    assert s[11] == {"flows": (), "themes": (), "module": None}
    assert structure_of_flows(ROWS, (1, 99)) == ({"id": 1, "name": "Run", "themes": ("Core",)},)


def test_focus_gaps_keep_only_active_work_and_count_everything():
    gaps = compute_structure_gaps(ROWS)
    focused = focus_structure_gaps(gaps, WorkFocus(path_id=1, milestone_id=3, flow_ids=(1,), file_ids=(11,)))
    total = sum(len(v) for v in gaps.values())
    assert focused["total_gaps"] == total
    active = focused["active_gaps"]
    assert active["open_paths_without_themes"] == ({"id": 1, "name": "P"},)
    assert [e["id"] for e in active["files_without_flow"]] == [11]
    assert "open_milestones_without_flows" not in active      # milestone 4 is not the active one
    assert "flows_without_theme" not in active                # flow 2 is not active work


def test_focus_scope_inherits_from_parent_task():
    task = {"item_type": "task", "milestone_id": 3, "flow_ids": "[1, 2]"}
    assert focus_scope(task, None) == (3, (1, 2))
    sub = {"item_type": "subtask", "parent_task_id": 7}
    assert focus_scope(sub, {"milestone_id": 3, "flow_ids": "[1]"}) == (3, (1,))
    sq = {"item_type": "sidequest", "flow_ids": "[2]"}
    assert focus_scope(sq, {"milestone_id": 4, "flow_ids": "[1]"}) == (4, (2,))
    assert focus_scope(None, None) == (None, ())


def test_compactors():
    assert drop_empty({"a": None, "b": [], "c": 0, "d": "x", "e": {}}) == {"c": 0, "d": "x"}
    infra = compact_infrastructure((
        {"type": "project_root", "value": "/p", "description": "long"},
        {"type": "build_tool", "value": "", "description": "fill me"},
    ))
    assert infra == {"values": {"project_root": "/p"},
                     "unset": ({"type": "build_tool", "description": "fill me"},)}
    assert compact_user_settings(({"setting_key": "k", "setting_value": "v", "description": "d"},)) == {"k": "v"}
    mig = compact_migration({
        "pending": [], "up_to_date": [{"db_name": "x"}],
        "skipped": [{"db_name": "user_directives", "reason": "Database file does not exist"},
                    {"db_name": "project", "reason": "Schema version unreadable, migration status unknown: locked"}],
    })
    assert mig == {"unreadable": ({"db_name": "project",
                                   "reason": "Schema version unreadable, migration status unknown: locked"},)}


# ----------------------------------------------------------------------------
# end to end on a fresh project
# ----------------------------------------------------------------------------

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    clear_project_root_cache()
    root = tmp_path / "calc"
    root.mkdir()
    assert aimfp_init(str(root), init_git=False).success
    monkeypatch.chdir(root)
    assert create_project("Calc", "A calculator", ["add"], project_root=str(root)).success
    yield str(root)
    clear_project_root_cache()


def test_new_project_has_no_pending_notices(fresh):
    assert get_pending_notices(fresh).notices == ()
    prefs = os.path.join(fresh, ".aimfp-project", "user_preferences.db")
    outcomes = {r[0] for r in sqlite3.connect(prefs).execute("SELECT outcome FROM acknowledged_notices")}
    assert outcomes <= {"new_project"}


def test_new_project_is_not_due_a_backup(fresh):
    assert check_scheduled_backup_due(fresh).data["due"] is False


def test_status_defaults_to_quick_and_summary_reloads(fresh):
    quick = aimfp_status()
    assert quick.data["tier"] == "quick"
    assert "supportive_context" not in quick.data
    assert quick.data["discovery_pending"] is True
    assert any("project_discovery" in s for s in quick.return_statements)

    summary = aimfp_status(type="summary")
    assert summary.data["tier"] == "summary"
    assert summary.data["supportive_context"]
    assert "infrastructure" in summary.data
    for gone in ("structure_summary", "modules_summary", "modules_guidance", "structure_guidance"):
        assert gone not in summary.data


def test_session_bundle_carries_only_relevant_statements(fresh):
    run = aimfp_run(is_new_session=True, start_watchdog=False)
    assert run.success, run.error
    data = run.data
    assert "notices" not in data and "scheduled_backup" not in data
    assert "supportive_context_coding" not in data          # no milestone yet
    assert data["status"]["tier"] == "summary"
    text = " ".join(run.return_statements)
    for absent in ("RELEASE NOTICES", "Deliver each notice", "MIGRATION", "WATCHDOG", "AUTOSTART"):
        assert absent not in text
    assert "project_discovery" in text


def test_checkpoint_without_reminders_is_empty(fresh):
    aimfp_run(is_new_session=True, start_watchdog=False)
    cp = aimfp_run(is_new_session=False)
    assert cp.data == {}
    assert cp.return_statements == ()


# ----------------------------------------------------------------------------
# detail_level on list/context tools (task 53)
# ----------------------------------------------------------------------------

from aimfp.helpers.orchestrators.state import get_current_progress, progress_order_clause
from aimfp.helpers.orchestrators.status import lean_task_context
from aimfp.helpers.project.tasks import get_incomplete_tasks
from aimfp.helpers.shared.detail_level import normalize_detail_level, pick_fields


def test_normalize_detail_level():
    assert normalize_detail_level(None) == ("lean", None)
    assert normalize_detail_level("standard") == ("lean", None)
    assert normalize_detail_level("full") == ("full", None)
    assert normalize_detail_level("minimal")[0] is None
    assert normalize_detail_level("minimal", ("minimal", "lean", "full")) == ("minimal", None)


def test_lean_task_context_shape():
    full = {
        "task_item": {"id": 1, "description": "keep me"},
        "task_type": "task",
        "items": ({"id": 1, "name": "done", "status": "completed", "description": "old"},
                  {"id": 2, "name": "todo", "status": "pending", "description": "spec"}),
        "flows": ({"id": 3, "name": "F", "description": "x" * 5000},),
        "files": ({"id": 9, "path": "a.py", "language": "python"},),
        "functions": ({"id": 5, "name": "f", "file_id": 9, "purpose": "p" * 300},),
        "modules": ({"id": 7, "name": "m", "path": "src/", "purpose": "pp"},),
        "notes": ({"id": 1},),
    }
    lean = lean_task_context(full)
    assert lean["task_item"]["description"] == "keep me"
    assert lean["items"] == ({"id": 1, "name": "done", "status": "completed"},
                             {"id": 2, "name": "todo", "status": "pending", "description": "spec"})
    assert lean["flows"] == ({"id": 3, "name": "F"},)
    assert lean["files"] == ({"id": 9, "path": "a.py", "functions": ({"id": 5, "name": "f"},)},)
    assert "functions" not in lean and lean["notes"] == ({"id": 1},)
    assert pick_fields({"a": 1}, ("a", "b")) == {"a": 1}


def test_progress_and_incomplete_tasks_are_lean_by_default(fresh):
    db = os.path.join(fresh, ".aimfp-project", "project.db")
    c = sqlite3.connect(db)
    c.execute("INSERT INTO completion_path (name, order_index) VALUES ('p', 1)")
    c.execute("INSERT INTO milestones (completion_path_id, name) VALUES (1, 'm')")
    c.execute("INSERT INTO tasks (milestone_id, name, status, description) VALUES (1, 'old', 'completed', 'long')")
    c.execute("INSERT INTO tasks (milestone_id, name, status, description) VALUES (1, 'new', 'pending', 'long')")
    c.commit()
    c.close()

    lean = get_current_progress(scope="tasks")
    assert lean.success, lean.error
    assert [r["name"] for r in lean.data["records"]] == ["new", "old"]   # open first
    assert set(lean.data["records"][0]) == {"id", "name", "status", "priority", "milestone_id"}
    full = get_current_progress(scope="tasks", detail_level="full")
    assert full.data["records"][0]["description"] == "long"
    assert get_current_progress(scope="tasks", detail_level="standard").success
    assert "status" in progress_order_clause("tasks") and progress_order_clause("files") == " ORDER BY id"

    tasks = get_incomplete_tasks(project_root=fresh)
    assert [t.description for t in tasks.tasks] == [None]
    assert get_incomplete_tasks(project_root=fresh, detail_level="full").tasks[0].description == "long"
