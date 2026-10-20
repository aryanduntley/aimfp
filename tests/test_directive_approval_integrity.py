"""
User-directive approval integrity (task 73, user_directives schema 1.5).

Approval is a person's decision. Before this, the AI could grant it to itself
through the generic MCP write tools (approved=1 on update or insert), and it
did not even need to: status='active' on an unapproved row was dispatched by
get_due_directives, which never read approved. Source:
svamanas docs/aimfp-directive-approval-integrity.md.

The contract these protect:
- the generic write tools refuse the approval columns and status='active',
  with column names checked against the table (case and quoting can't slip by);
- approval is recorded only by aimfp.hooks.approval (a host's human path,
  or the interactive `aimfp directives approve` CLI), never an MCP tool;
- approval binds to content (trigger, action, implementation file and its
  bytes); any change voids it everywhere: dispatch, activation, health;
- pre-1.5 approvals carry no hash and need approving again.
"""
import argparse
import json
import os
import sqlite3
import tempfile

import pytest

from aimfp.database.connection import clear_project_root_cache, set_project_root
from aimfp.helpers.user_directives.cli import EXIT_DONE, EXIT_REFUSED, build_parser, run_command
from aimfp.helpers.user_directives.crud import add_user_custom_entry, update_user_custom_entry
from aimfp.helpers.user_directives.management import activate_user_directive
from aimfp.helpers.user_directives.monitoring import (
    HEALTH_NEEDS_REAPPROVAL,
    HEALTH_UNAPPROVED,
    check_directive_health,
)
from aimfp.hooks.approval import (
    APPROVAL_APPROVED,
    APPROVAL_NEEDS_REAPPROVAL,
    APPROVAL_UNAPPROVED,
    approval_verdict,
    approve_user_directive,
    directive_content_hash,
    get_directive_approval,
    revoke_user_directive_approval,
)
from aimfp.hooks.directives import get_due_directives
from aimfp.mcp_server.registry import TOOL_REGISTRY

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)
HANDLER = "src/handlers/backup.py"
DIRECTIVE = {
    "name": "nightly_backup", "source_file": "d.yaml", "source_format": "yaml",
    "raw_content": "back up nightly", "validated_content": "{}",
    "trigger_type": "time", "trigger_config": '{"kind": "interval", "seconds": 900}',
    "action_type": "command", "action_config": '{"command": "backup.sh"}',
    "implementation_file_path": HANDLER,
}


@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


def _db(root: str) -> str:
    return os.path.join(root, ".aimfp-project", "user_directives.db")


def _project(schema_sql: str = None) -> str:
    root = tempfile.mkdtemp(prefix="aimfp_approval_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    os.makedirs(os.path.join(root, "src", "handlers"))
    with open(os.path.join(root, HANDLER), "w") as handle:
        handle.write("def run():\n    return 'backup'\n")
    if schema_sql is None:
        with open(SCHEMA_PATH) as f:
            schema_sql = f.read()
    conn = sqlite3.connect(_db(root))
    conn.executescript(schema_sql)
    conn.close()
    set_project_root(root)
    return root


def _added(root: str) -> int:
    result = add_user_custom_entry("user_directives", dict(DIRECTIVE))
    assert result.success, result.error
    return result.id


def _row(root: str, directive_id: int) -> dict:
    conn = sqlite3.connect(_db(root))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute(
        "SELECT * FROM user_directives WHERE id = ?", (directive_id,)).fetchone())
    conn.close()
    return row


def _raw_sql(root: str, sql: str, params=()) -> None:
    conn = sqlite3.connect(_db(root))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _health(root: str, directive_id: int):
    result = check_directive_health(project_root=root)
    assert result.success, result.error
    return next(d for d in result.directives if d.directive_id == directive_id)


def _approved_and_active(root: str) -> int:
    directive_id = _added(root)
    assert approve_user_directive(directive_id, "alice", root).success
    assert activate_user_directive(directive_id).success
    return directive_id


# ============================================================================
# The generic write tools never grant approval
# ============================================================================

@pytest.mark.parametrize("key", ["approved", "APPROVED", "Approved_At", "approved_by", "approval_hash"])
def test_update_refuses_approval_columns_in_any_case(key):
    root = _project()
    directive_id = _added(root)

    result = update_user_custom_entry("user_directives", directive_id, {key: 1})

    assert result.success is False
    assert "approve_user_directive" in result.error and "not an MCP tool" in result.error
    assert _row(root, directive_id)["approved"] == 0


def test_quoted_column_name_is_refused_as_unknown():
    root = _project()
    directive_id = _added(root)

    result = update_user_custom_entry("user_directives", directive_id, {'"approved"': 1})

    assert result.success is False
    assert "Unknown column" in result.error
    assert _row(root, directive_id)["approved"] == 0


def test_insert_refuses_preapproved_row():
    root = _project()

    result = add_user_custom_entry("user_directives", {**DIRECTIVE, "approved": 1})

    assert result.success is False
    assert "approve_user_directive" in result.error
    conn = sqlite3.connect(_db(root))
    assert conn.execute("SELECT COUNT(*) FROM user_directives").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("write", ["add", "update"])
def test_status_active_is_refused_through_generic_tools(write):
    root = _project()
    if write == "add":
        result = add_user_custom_entry("user_directives", {**DIRECTIVE, "status": "active"})
    else:
        result = update_user_custom_entry("user_directives", _added(root), {"status": "active"})

    assert result.success is False
    assert "activate_user_directive" in result.error


def test_unknown_column_is_refused_on_any_table():
    root = _project()
    directive_id = _added(root)

    result = update_user_custom_entry("user_directives", directive_id, {"no_such": 1})

    assert result.success is False and "no_such" in result.error


def test_ordinary_updates_still_work():
    root = _project()
    directive_id = _added(root)

    result = update_user_custom_entry("user_directives", directive_id, {"status": "implemented"})

    assert result.success, result.error
    assert _row(root, directive_id)["status"] == "implemented"


# ============================================================================
# Dispatch, activation and health refuse what a person did not approve
# ============================================================================

def test_active_but_unapproved_row_is_withheld_from_dispatch():
    """The bypass the svamanas doc missed: status set directly, approval never touched."""
    root = _project()
    directive_id = _added(root)
    _raw_sql(root, "UPDATE user_directives SET status = 'active' WHERE id = ?", (directive_id,))

    due = get_due_directives(root, now="2099-01-01T00:00:00")

    assert due.success and due.directives == ()
    assert [(w.directive_id, w.state) for w in due.withheld] == [(directive_id, APPROVAL_UNAPPROVED)]
    assert _health(root, directive_id).health == HEALTH_UNAPPROVED


def test_flag_set_without_approval_path_is_not_approval():
    """approved=1 written around the tools (raw SQL) has no hash: not approved."""
    root = _project()
    directive_id = _added(root)
    _raw_sql(root, "UPDATE user_directives SET approved = 1, status = 'active' WHERE id = ?",
             (directive_id,))

    assert activate_user_directive(directive_id).success is False
    due = get_due_directives(root, now="2099-01-01T00:00:00")
    assert due.directives == ()
    assert due.withheld[0].state == APPROVAL_NEEDS_REAPPROVAL
    assert _health(root, directive_id).health == HEALTH_NEEDS_REAPPROVAL


def test_activate_refuses_unapproved_and_names_the_human_path():
    root = _project()
    directive_id = _added(root)

    result = activate_user_directive(directive_id)

    assert result.success is False
    assert "user_directive_approve" in result.error and "not by an MCP tool" in result.error


def test_approve_records_who_when_and_hash_then_activation_and_dispatch_work():
    root = _project()
    directive_id = _added(root)

    assert approve_user_directive(directive_id, "alice", root).success
    row = _row(root, directive_id)
    assert row["approved"] == 1 and row["approved_by"] == "alice"
    assert row["approved_at"] and len(row["approval_hash"]) == 64

    assert activate_user_directive(directive_id).success
    due = get_due_directives(root, now="2099-01-01T00:00:00")
    assert [d.directive_id for d in due.directives] == [directive_id]
    assert due.withheld == ()
    assert _health(root, directive_id).approval == APPROVAL_APPROVED

    conn = sqlite3.connect(_db(root))
    notes = conn.execute(
        "SELECT note_type, reference_id, metadata_json FROM notes").fetchall()
    conn.close()
    assert [(n[0], n[1]) for n in notes] == [("lifecycle", directive_id)]
    assert json.loads(notes[0][2])["event"] == "approved"


def test_changing_action_after_approval_voids_it_everywhere():
    root = _project()
    directive_id = _approved_and_active(root)

    assert update_user_custom_entry(
        "user_directives", directive_id,
        {"action_config": '{"command": "rm -rf /"}'}).success

    due = get_due_directives(root, now="2099-01-01T00:00:00")
    assert due.directives == ()
    assert due.withheld[0].state == APPROVAL_NEEDS_REAPPROVAL
    assert "changed since it was approved" in due.withheld[0].reason
    assert _health(root, directive_id).health == HEALTH_NEEDS_REAPPROVAL
    _raw_sql(root, "UPDATE user_directives SET status = 'paused' WHERE id = ?", (directive_id,))
    assert activate_user_directive(directive_id).success is False


def test_editing_implementation_file_after_approval_voids_it():
    root = _project()
    directive_id = _approved_and_active(root)

    with open(os.path.join(root, HANDLER), "a") as handle:
        handle.write("import shutil; shutil.rmtree('/')\n")

    assert get_due_directives(root, now="2099-01-01T00:00:00").directives == ()
    assert get_directive_approval(directive_id, root).state == APPROVAL_NEEDS_REAPPROVAL


def test_deleting_implementation_file_after_approval_voids_it():
    root = _project()
    directive_id = _approved_and_active(root)

    os.remove(os.path.join(root, HANDLER))

    assert get_directive_approval(directive_id, root).state == APPROVAL_NEEDS_REAPPROVAL


def test_reserializing_the_same_config_keeps_approval():
    root = _project()
    directive_id = _approved_and_active(root)

    assert update_user_custom_entry(
        "user_directives", directive_id,
        {"trigger_config": json.dumps({"seconds": 900, "kind": "interval"}, indent=4)}).success

    assert get_directive_approval(directive_id, root).state == APPROVAL_APPROVED


def test_reapproval_after_change_restores_dispatch():
    root = _project()
    directive_id = _approved_and_active(root)
    update_user_custom_entry("user_directives", directive_id,
                             {"action_config": '{"command": "backup2.sh"}'})

    assert approve_user_directive(directive_id, "alice", root).success

    due = get_due_directives(root, now="2099-01-01T00:00:00")
    assert [d.directive_id for d in due.directives] == [directive_id]


def test_revoke_clears_approval_pauses_and_notes():
    root = _project()
    directive_id = _approved_and_active(root)

    assert revoke_user_directive_approval(directive_id, root, "no longer needed").success

    row = _row(root, directive_id)
    assert (row["approved"], row["approved_by"], row["approval_hash"], row["status"]) == (
        0, None, None, "paused")
    conn = sqlite3.connect(_db(root))
    events = [json.loads(m)["event"] for (m,) in conn.execute(
        "SELECT metadata_json FROM notes ORDER BY id")]
    conn.close()
    assert events == ["approved", "approval_revoked"]


def test_approve_requires_a_named_approver():
    root = _project()
    directive_id = _added(root)

    result = approve_user_directive(directive_id, "  ", root)

    assert result.success is False and "approved_by" in result.error
    assert _row(root, directive_id)["approved"] == 0


def test_approve_missing_directive_is_a_clean_failure():
    root = _project()

    assert approve_user_directive(99, "alice", root).success is False


# ============================================================================
# Pre-1.5 databases
# ============================================================================

def _schema_1_4() -> str:
    with open(SCHEMA_PATH) as f:
        sql = f.read()
    sql = sql.replace("VALUES (1, '1.5')", "VALUES (1, '1.4')")
    return "\n".join(line for line in sql.split("\n")
                     if not line.lstrip().startswith(("approved_by ", "approval_hash ")))


def test_unmigrated_database_refuses_approval_with_migrate_hint():
    root = _project(_schema_1_4())
    directive_id = _added(root)

    result = approve_user_directive(directive_id, "alice", root)

    assert result.success is False and "migrate_databases" in result.error


def test_pre_1_5_approval_needs_approving_again_after_migration():
    from aimfp.helpers.orchestrators.migration import migrate_databases

    root = _project(_schema_1_4())
    directive_id = _added(root)
    _raw_sql(root, "UPDATE user_directives SET approved = 1, status = 'active' WHERE id = ?",
             (directive_id,))
    # Even before migrating, an approval without a hash does not dispatch.
    assert get_due_directives(root, now="2099-01-01T00:00:00").directives == ()

    result = migrate_databases()

    assert result.success, result.error
    migrated = [m for m in result.data["migrated"] if m["db_name"] == "user_directives"]
    assert (migrated[0]["old_version"], migrated[0]["new_version"]) == ("1.4", "1.5")
    # migrate_databases rebuilds in place; the row keeps approved=1 but has no hash.
    assert _row(root, directive_id)["approval_hash"] is None
    assert get_directive_approval(directive_id, root).state == APPROVAL_NEEDS_REAPPROVAL
    assert _health(root, directive_id).health == HEALTH_NEEDS_REAPPROVAL
    assert approve_user_directive(directive_id, "alice", root).success
    assert get_due_directives(root, now="2099-01-01T00:00:00").directives != ()


# ============================================================================
# Pure pieces and the tool surface
# ============================================================================

def test_content_hash_covers_implementation_digest():
    row = {**DIRECTIVE}
    assert directive_content_hash(row, "a" * 64) != directive_content_hash(row, "b" * 64)
    assert directive_content_hash(row, None) != directive_content_hash(row, "missing")


def test_verdict_states():
    assert approval_verdict(0, None, "h")[0] == APPROVAL_UNAPPROVED
    assert approval_verdict(1, None, "h")[0] == APPROVAL_NEEDS_REAPPROVAL
    assert approval_verdict(1, "x", "h")[0] == APPROVAL_NEEDS_REAPPROVAL
    assert approval_verdict(1, "h", "h")[0] == APPROVAL_APPROVED


@pytest.mark.parametrize("name", [
    "approve_user_directive", "revoke_user_directive_approval", "get_directive_approval"])
def test_approval_hooks_are_not_mcp_tools(name):
    assert name not in TOOL_REGISTRY


# ============================================================================
# `aimfp directives` CLI - the human path for a plain MCP setup
# ============================================================================

def _cli(root, argv, is_tty, typed=""):
    out = []
    code = run_command(build_parser().parse_args(argv), root, is_tty, lambda _: typed, out.append)
    return code, "\n".join(out)


def test_cli_approve_refuses_non_interactive_stdin():
    root = _project()
    directive_id = _added(root)

    code, out = _cli(root, ["approve", "nightly_backup"], is_tty=False, typed="nightly_backup")

    assert code == EXIT_REFUSED and "interactive terminal" in out
    assert _row(root, directive_id)["approved"] == 0


def test_cli_approve_needs_the_name_typed_back():
    root = _project()
    directive_id = _added(root)

    code, out = _cli(root, ["approve", "nightly_backup"], is_tty=True, typed="yes")

    assert code == EXIT_REFUSED and "not approved" in out
    assert _row(root, directive_id)["approved"] == 0


def test_cli_approve_shows_directive_and_records_approval():
    root = _project()
    directive_id = _added(root)

    code, out = _cli(root, ["approve", str(directive_id), "--by", "alice"],
                     is_tty=True, typed="nightly_backup")

    assert code == EXIT_DONE
    assert "backup.sh" in out and HANDLER in out
    assert _row(root, directive_id)["approved_by"] == "alice"


def test_cli_list_and_revoke():
    root = _project()
    directive_id = _approved_and_active(root)

    code, out = _cli(root, ["list"], is_tty=False)
    assert code == EXIT_DONE and "nightly_backup" in out and "approved" in out

    code, _ = _cli(root, ["revoke", "nightly_backup", "--reason", "done"], is_tty=False)
    assert code == EXIT_DONE
    assert _row(root, directive_id)["approved"] == 0


def test_cli_parser_has_no_noninteractive_escape():
    approve = build_parser()._subparsers._group_actions[0].choices["approve"]
    flags = {option for action in approve._actions for option in action.option_strings}
    assert flags <= {"-h", "--help", "--by"}
    assert isinstance(build_parser(), argparse.ArgumentParser)
