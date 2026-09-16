"""
The action_config envelope (src/aimfp/hooks/actions.py) and its enforcement.

The same gap as trigger_config, one line down in the schema, deliberately
given a different answer: AIMFP specifies the envelope, and for function_call
and command it stops there, because the target named exists in the caller's
project and not in AIMFP.
"""

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
from aimfp.helpers.user_directives.validation import validate_action_config
from aimfp.hooks.actions import (
    ACTION_TYPES,
    CALLER_RESOLVED_ACTIONS,
    action_config_grammar,
)
from aimfp.hooks.actions import validate_action_config as validate_hook

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas",
    "user_directives.sql"
)


@pytest.fixture
def uc2_root(monkeypatch):
    clear_project_root_cache()
    root = tempfile.mkdtemp(prefix="aimfp_actions_")
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


def _row(**data) -> dict:
    base = {
        "name": "lights_off",
        "source_file": "directives/home.yaml",
        "source_format": "yaml",
        "raw_content": "turn off lights at 5pm",
        "validated_content": "{}",
        "trigger_type": "time",
        "trigger_config": '{"kind": "daily", "at": "17:00"}',
        "action_type": "api_call",
        "action_config": '{"endpoint": "/lights/off"}',
    }
    base.update(data)
    return base


# ============================================================================
# Conforming envelopes
# ============================================================================

@pytest.mark.parametrize("action_type,config", [
    ("api_call", {"endpoint": "/lights/off"}),
    ("api_call", {"api": "homeassistant", "endpoint": "/lights/off", "method": "POST"}),
    ("api_call", {"endpoint": "/x", "headers": {"X-Key": "1"}, "body": {"on": False},
                  "timeout_seconds": 30}),
    ("api_call", {"endpoint": "/x", "body": [1, 2, 3]}),
    ("script_execution", {"script": "scripts/backup.sh"}),
    ("script_execution", {"script": "s.sh", "args": ["--full"], "cwd": "/srv",
                          "env": {"TZ": "UTC"}, "timeout_seconds": 600}),
    ("function_call", {"function": "handlers.lights.turn_off"}),
    ("function_call", {"function": "f", "module": "m", "args": [1, "two", None],
                       "kwargs": {"force": True}}),
    ("command", {"command": "systemctl restart nginx"}),
    ("command", {"command": ["systemctl", "restart", "nginx"], "cwd": "/"}),
    ("notification", {"message": "Backup finished"}),
    ("notification", {"message": "m", "channel": "ops", "title": "t",
                      "priority": "high"}),
])
def test_valid_envelopes(action_type, config):
    result = validate_hook(action_type, config)
    assert result.valid, result.errors


def test_the_schema_comments_action_example_still_conforms():
    """
    Unlike the trigger comment, the action_config example in
    user_directives.sql is compatible with the envelope - {"api": ...,
    "endpoint": ...} was a reasonable shape and is now the specified one.
    """
    result = validate_hook("api_call", {"api": "homeassistant",
                                        "endpoint": "/lights/off"})
    assert result.valid, result.errors


def test_every_documented_example_validates():
    """Documentation and enforcement read from the same table."""
    for action_type, spec in action_config_grammar().items():
        result = validate_hook(action_type, spec["example"])
        assert result.valid, f"{action_type}: {result.errors}"


# ============================================================================
# The line AIMFP will not cross
# ============================================================================

@pytest.mark.parametrize("action_type", CALLER_RESOLVED_ACTIONS)
def test_project_bound_actions_are_marked_caller_resolved(action_type):
    """
    A function or an executable exists in the caller's project. AIMFP can say
    the envelope is right; only the caller can say the target resolves.
    """
    configs = {
        "function_call": {"function": "handlers.lights.turn_off"},
        "command": {"command": "true"},
    }
    result = validate_hook(action_type, configs[action_type])
    assert result.valid
    assert result.caller_resolved is True


@pytest.mark.parametrize("action_type", ["api_call", "script_execution", "notification"])
def test_generic_actions_are_not_caller_resolved(action_type):
    configs = {
        "api_call": {"endpoint": "/x"},
        "script_execution": {"script": "s.sh"},
        "notification": {"message": "m"},
    }
    assert validate_hook(action_type, configs[action_type]).caller_resolved is False


def test_a_nonexistent_function_still_validates():
    """
    AIMFP must not pretend to know. A well-formed name for something that
    does not exist is a valid envelope and a caller's problem.
    """
    result = validate_hook("function_call", {"function": "nothing.here.at.all"})
    assert result.valid
    assert result.caller_resolved


# ============================================================================
# Field rules
# ============================================================================

def test_unknown_key_is_rejected():
    result = validate_hook("api_call", {"endpoint": "/x", "retries": 3})
    assert not result.valid
    assert any("action_config.retries" in e for e in result.errors)


def test_missing_required_key_is_rejected():
    result = validate_hook("notification", {"channel": "ops"})
    assert not result.valid
    assert any("action_config.message" in e and "required" in e
               for e in result.errors)


def test_http_method_must_be_a_known_verb_in_uppercase():
    assert not validate_hook("api_call", {"endpoint": "/x", "method": "post"}).valid
    assert not validate_hook("api_call", {"endpoint": "/x", "method": "FETCH"}).valid
    assert validate_hook("api_call", {"endpoint": "/x", "method": "DELETE"}).valid


def test_command_accepts_a_string_or_an_argv_array():
    """
    Both are common and the difference is semantic - a string goes through a
    shell, an array does not - so neither is coerced into the other.
    """
    assert validate_hook("command", {"command": "ls -la"}).valid
    assert validate_hook("command", {"command": ["ls", "-la"]}).valid
    assert not validate_hook("command", {"command": ["ls", 7]}).valid
    assert not validate_hook("command", {"command": 7}).valid


def test_script_args_must_be_strings_but_function_args_need_not_be():
    """
    A script receives argv; a function receives Python values. The same key
    name means different things in different envelopes.
    """
    assert not validate_hook(
        "script_execution", {"script": "s.sh", "args": [1, 2]}).valid
    assert validate_hook(
        "function_call", {"function": "f", "args": [1, 2]}).valid


def test_request_body_is_unconstrained():
    """A payload's shape is the caller's API's business, not AIMFP's."""
    for body in ({"a": 1}, [1, 2], "raw", 42, None, True):
        assert validate_hook("api_call", {"endpoint": "/x", "body": body}).valid


def test_timeout_must_be_a_positive_integer():
    assert not validate_hook(
        "api_call", {"endpoint": "/x", "timeout_seconds": 0}).valid
    assert not validate_hook(
        "api_call", {"endpoint": "/x", "timeout_seconds": True}).valid


def test_bad_action_type_is_rejected():
    result = validate_hook("webhook", {"endpoint": "/x"})
    assert not result.valid
    assert any("action_type" in e for e in result.errors)


@pytest.mark.parametrize("config", [None, "not json", "[1,2]", 7])
def test_non_object_payloads_are_rejected(config):
    assert not validate_hook("api_call", config).valid


def test_json_text_and_dict_agree():
    import json
    config = {"endpoint": "/x", "method": "post"}
    assert (validate_hook("api_call", config).errors
            == validate_hook("api_call", json.dumps(config)).errors)


# ============================================================================
# Enforcement
# ============================================================================

def test_bad_action_config_is_refused_at_insert(uc2_root):
    result = add_user_custom_entry(
        "user_directives", _row(action_config='{"endpoint": "/x", "method": "post"}'))
    assert not result.success
    assert "action_config.method" in result.error


def test_good_action_config_is_accepted(uc2_root):
    result = add_user_custom_entry("user_directives", _row())
    assert result.success, result.error


def test_bad_action_config_is_refused_at_update(uc2_root):
    created = add_user_custom_entry("user_directives", _row())
    result = update_user_custom_entry(
        "user_directives", created.id, {"action_config": '{"nope": 1}'})
    assert not result.success
    assert "action_config" in result.error


def test_partial_update_uses_the_stored_action_type(uc2_root):
    """
    Changing only the config must be checked against the action_type already
    on the row - otherwise the easiest hole stays open.
    """
    created = add_user_custom_entry("user_directives", _row(
        action_type="notification", action_config='{"message": "hi"}'))
    result = update_user_custom_entry(
        "user_directives", created.id, {"action_config": '{"endpoint": "/x"}'})
    assert not result.success


def test_a_broken_trigger_is_reported_before_a_broken_action(uc2_root):
    """
    Both are wrong; the silent failure is the one worth naming first.
    """
    result = add_user_custom_entry("user_directives", _row(
        trigger_config='{"kind": "daily", "at": "5pm"}',
        action_config='{"nope": 1}'))
    assert not result.success
    assert "trigger_config" in result.error


# ============================================================================
# The MCP tool
# ============================================================================

def test_tool_result_names_actions_not_triggers(uc2_root):
    """
    A separate result type from the trigger tool's. Reusing it would have put
    an action_type inside a field called trigger_type.
    """
    result = validate_action_config("api_call", {"endpoint": "/x"})
    assert result.success and result.valid
    assert result.action_type == "api_call"
    assert not hasattr(result, "trigger_type")


def test_tool_hands_back_the_envelope_on_failure(uc2_root):
    result = validate_action_config("script_execution", {"args": ["--full"]})
    assert not result.valid
    assert result.grammar["script_execution"]["required"] == ["script"]


def test_tool_grammar_explains_what_the_caller_must_check(uc2_root):
    grammar = action_config_grammar("function_call")["function_call"]
    assert grammar["caller_resolved"] is True
    assert "caller" in grammar["note"]


def test_tool_carries_return_statements(uc2_root):
    assert validate_action_config("notification", {"message": "m"}).return_statements


def test_grammar_covers_every_action_type():
    assert set(action_config_grammar()) == set(ACTION_TYPES)
    assert action_config_grammar("nonsense") == {}
