"""
Insert-time enforcement of the trigger_config grammar.

The grammar only means something if a non-conforming config cannot be
stored. A bad action fails loudly with a recorded error; a bad schedule fails
silently by never firing, which is why this is checked before the write
rather than at dispatch.

add_user_custom_entry is the only path user directive rows are written
through - there is no typed insert - so the gate lives there.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from aimfp.database.connection import clear_project_root_cache, set_project_root
from aimfp.helpers.user_directives.crud import (
    add_user_custom_entry,
    update_user_custom_entry,
)
from aimfp.helpers.user_directives.validation import validate_trigger_config

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)

CONFORMING = '{"kind": "daily", "at": "17:00", "timezone": "America/New_York"}'
LEGACY = '{"time": "17:00", "timezone": "America/New_York"}'


@pytest.fixture
def uc2_root(monkeypatch):
    """A UC2 project, with the process pointed at it the way an MCP session is."""
    clear_project_root_cache()
    root = tempfile.mkdtemp(prefix="aimfp_enforce_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    monkeypatch.chdir(root)
    set_project_root(root)
    yield root
    clear_project_root_cache()


def _row(data: dict) -> dict:
    base = {
        "name": "lights_off",
        "source_file": "directives/home.yaml",
        "source_format": "yaml",
        "raw_content": "turn off lights at 5pm",
        "validated_content": "{}",
        "trigger_type": "time",
        "trigger_config": CONFORMING,
        "action_type": "api_call",
        "action_config": '{"endpoint": "/off"}',
    }
    base.update(data)
    return base


def _count(root: str) -> int:
    conn = sqlite3.connect(
        os.path.join(root, ".aimfp-project", "user_directives.db"))
    n = conn.execute("SELECT COUNT(*) FROM user_directives").fetchone()[0]
    conn.close()
    return n


# ============================================================================
# The gate
# ============================================================================

def test_conforming_config_is_accepted(uc2_root):
    result = add_user_custom_entry("user_directives", _row({}))
    assert result.success, result.error
    assert _count(uc2_root) == 1


def test_non_conforming_config_is_refused(uc2_root):
    result = add_user_custom_entry(
        "user_directives", _row({"trigger_config": '{"kind": "daily", "at": "5pm"}'}))
    assert not result.success
    assert "trigger_config.at" in result.error
    assert _count(uc2_root) == 0


def test_the_schema_comments_own_example_is_refused(uc2_root):
    """
    The shape the SQL comment taught for as long as the column existed does
    not conform. Nothing downstream ever used it - UC2 had no runtime until
    2026-09-15 - but AIMFP's own fixtures did, and they are corrected in this
    milestone.
    """
    result = add_user_custom_entry(
        "user_directives", _row({"trigger_config": LEGACY}))
    assert not result.success
    assert "trigger_config.kind" in result.error


def test_rejection_names_every_broken_key(uc2_root):
    result = add_user_custom_entry("user_directives", _row({
        "trigger_config": '{"kind": "weekly", "at": "25:00", "weekdays": ["funday"]}',
    }))
    assert not result.success
    assert "trigger_config.at" in result.error
    assert "trigger_config.weekdays" in result.error


def test_unparseable_json_is_refused(uc2_root):
    result = add_user_custom_entry(
        "user_directives", _row({"trigger_config": "not json"}))
    assert not result.success
    assert "not valid JSON" in result.error


@pytest.mark.parametrize("trigger_type,config", [
    ("event", '{"event": "stove_on"}'),
    ("condition", '{"expression": "cpu > 90"}'),
    ("manual", "{}"),
])
def test_non_time_triggers_are_gated_too(uc2_root, trigger_type, config):
    ok = add_user_custom_entry("user_directives", _row({
        "name": f"d_{trigger_type}",
        "trigger_type": trigger_type,
        "trigger_config": config,
    }))
    assert ok.success, ok.error

    bad = add_user_custom_entry("user_directives", _row({
        "name": f"bad_{trigger_type}",
        "trigger_type": trigger_type,
        "trigger_config": '{"nonsense": 1}',
    }))
    assert not bad.success


def test_other_tables_are_untouched(uc2_root):
    """
    The gate is one table-aware branch, not a new rule for every write. A
    notes row carrying a field called trigger_config would be nobody's
    business but the notes table's.
    """
    add_user_custom_entry("user_directives", _row({}))
    result = add_user_custom_entry("notes", {
        "note_type": "implementation",
        "content": "trigger_config is fine here",
    })
    assert result.success, result.error


# ============================================================================
# The update path
# ============================================================================

def test_update_to_a_broken_config_is_refused(uc2_root):
    created = add_user_custom_entry("user_directives", _row({}))
    result = update_user_custom_entry(
        "user_directives", created.id,
        {"trigger_config": '{"kind": "interval", "seconds": 0}'})
    assert not result.success
    assert "trigger_config.seconds" in result.error


def test_partial_update_uses_the_stored_trigger_type(uc2_root):
    """
    An update carrying trigger_config without trigger_type must be checked
    against the type already in the row, not waved through for lack of one.
    """
    created = add_user_custom_entry("user_directives", _row({
        "trigger_type": "event", "trigger_config": '{"event": "stove_on"}'}))

    # A perfectly good TIME config is wrong for an event trigger.
    result = update_user_custom_entry(
        "user_directives", created.id, {"trigger_config": CONFORMING})
    assert not result.success
    assert "event trigger" in result.error


def test_update_can_change_type_and_config_together(uc2_root):
    created = add_user_custom_entry("user_directives", _row({
        "trigger_type": "event", "trigger_config": '{"event": "stove_on"}'}))
    result = update_user_custom_entry("user_directives", created.id, {
        "trigger_type": "time", "trigger_config": CONFORMING})
    assert result.success, result.error


def test_update_not_touching_the_config_is_unaffected(uc2_root):
    created = add_user_custom_entry("user_directives", _row({}))
    result = update_user_custom_entry(
        "user_directives", created.id, {"status": "validated"})
    assert result.success, result.error


# ============================================================================
# The MCP tool
# ============================================================================

def test_tool_reports_valid_without_a_grammar(uc2_root):
    result = validate_trigger_config("time", {"kind": "interval", "seconds": 900})
    assert result.success and result.valid
    assert result.kind == "interval"
    assert result.errors == ()
    assert result.grammar is None


def test_tool_hands_back_the_grammar_on_failure(uc2_root):
    """
    Telling the AI what IS expected, at the moment it got it wrong, is the
    point of AIMFP owning this schema rather than leaving each project to
    invent one.
    """
    result = validate_trigger_config("time", json.loads(LEGACY))
    assert result.success and not result.valid
    assert result.errors
    assert sorted(result.grammar["time"]["kinds"]) == [
        "daily", "interval", "monthly", "weekly"]


def test_tool_carries_return_statements(uc2_root):
    result = validate_trigger_config("manual", {})
    assert result.return_statements


def test_tool_and_gate_agree(uc2_root):
    """
    One implementation behind both surfaces. If the tool says a config is
    fine, the insert must not then refuse it.
    """
    for config in (CONFORMING, LEGACY, '{"kind": "interval", "seconds": 60}',
                   '{"kind": "monthly", "at": "09:00", "days": [0]}'):
        tool_says = validate_trigger_config("time", config).valid
        insert = add_user_custom_entry("user_directives", _row({
            "name": f"d_{abs(hash(config))}", "trigger_config": config}))
        assert insert.success == tool_says, config
