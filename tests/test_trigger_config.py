"""
Tests for the trigger_config grammar (src/aimfp/hooks/triggers.py).

The column has always been JSON NOT NULL with no specification beyond a SQL
comment, so these tests are the specification's enforcement: what conforms,
what does not, and - the point of the whole milestone - that a config which
does not conform is rejected loudly rather than silently becoming {}.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from aimfp.hooks.triggers import (
    TIME_KINDS,
    TRIGGER_TYPES,
    WEEKDAY_TOKENS,
    trigger_config_grammar,
    validate_trigger_config,
)


# ============================================================================
# Conforming configs
# ============================================================================

@pytest.mark.parametrize("config", [
    {"kind": "interval", "seconds": 900},
    {"kind": "interval", "seconds": 1},
    {"kind": "daily", "at": "17:00"},
    {"kind": "daily", "at": "00:00", "timezone": "America/New_York"},
    {"kind": "daily", "at": "23:59", "timezone": "UTC"},
    {"kind": "weekly", "at": "17:00", "weekdays": ["mon", "wed", "fri"]},
    {"kind": "weekly", "at": "06:30", "weekdays": ["sun"], "timezone": "Europe/Berlin"},
    {"kind": "monthly", "at": "09:00", "days": [1, 15]},
    {"kind": "monthly", "at": "09:00", "days": [31]},
])
def test_valid_time_configs(config):
    result = validate_trigger_config("time", config)
    assert result.valid, result.errors
    assert result.kind == config["kind"]
    assert result.errors == ()


@pytest.mark.parametrize("trigger_type,config", [
    ("event", {"event": "stove_on"}),
    ("event", {"event": "stove_on", "source": "home_assistant"}),
    ("condition", {"expression": "cpu_percent > 90"}),
    ("condition", {"expression": "cpu > 90", "evaluate_every_seconds": 60}),
    ("manual", {}),
])
def test_valid_non_time_configs(trigger_type, config):
    result = validate_trigger_config(trigger_type, config)
    assert result.valid, result.errors
    assert result.kind is None


def test_every_documented_example_validates():
    """
    The grammar's own examples must pass its own validator.

    This is the check that keeps documentation and enforcement from drifting,
    which is the entire reason AIMFP owns this schema rather than leaving it
    to each project.
    """
    grammar = trigger_config_grammar()
    for kind, spec in grammar["time"]["kinds"].items():
        result = validate_trigger_config("time", spec["example"])
        assert result.valid, f"time/{kind}: {result.errors}"
    for trigger_type in ("event", "condition", "manual"):
        result = validate_trigger_config(trigger_type, grammar[trigger_type]["example"])
        assert result.valid, f"{trigger_type}: {result.errors}"


# ============================================================================
# The discriminator
# ============================================================================

def test_missing_kind_is_rejected():
    result = validate_trigger_config("time", {"at": "17:00"})
    assert not result.valid
    assert result.kind is None
    assert any("trigger_config.kind" in e and "required" in e for e in result.errors)


def test_unknown_kind_is_rejected_and_lists_the_valid_ones():
    result = validate_trigger_config("time", {"kind": "hourly", "seconds": 3600})
    assert not result.valid
    assert result.kind is None
    joined = " ".join(result.errors)
    assert "'hourly'" in joined
    for kind in TIME_KINDS:
        assert kind in joined


def test_bad_trigger_type_is_rejected_before_the_config():
    result = validate_trigger_config("cron", {"kind": "interval", "seconds": 60})
    assert not result.valid
    assert result.trigger_type is None
    assert any("trigger_type" in e for e in result.errors)


# ============================================================================
# Unknown keys are errors, not noise
# ============================================================================

def test_unknown_key_is_rejected():
    """
    A typo'd schedule key is the silent failure this module exists to
    surface: ignoring it means the config validates and then never fires.
    """
    result = validate_trigger_config("time", {"kind": "daily", "at": "17:00", "tz": "UTC"})
    assert not result.valid
    assert any("trigger_config.tz" in e and "not a recognised key" in e
               for e in result.errors)


def test_interval_rejects_timezone():
    """An interval counts elapsed seconds; a timezone would imply otherwise."""
    result = validate_trigger_config(
        "time", {"kind": "interval", "seconds": 900, "timezone": "UTC"})
    assert not result.valid
    assert any("trigger_config.timezone" in e for e in result.errors)


def test_manual_rejects_any_configuration():
    result = validate_trigger_config("manual", {"at": "17:00"})
    assert not result.valid
    assert any("takes no configuration" in e for e in result.errors)


def test_weekly_keys_are_not_valid_on_daily():
    result = validate_trigger_config(
        "time", {"kind": "daily", "at": "17:00", "weekdays": ["mon"]})
    assert not result.valid
    assert any("trigger_config.weekdays" in e for e in result.errors)


# ============================================================================
# Field-level rules
# ============================================================================

@pytest.mark.parametrize("at", ["9:00", "17:60", "24:00", "5pm", "17:00:00", "", "1700"])
def test_rejects_malformed_time_of_day(at):
    result = validate_trigger_config("time", {"kind": "daily", "at": at})
    assert not result.valid
    assert any("trigger_config.at" in e for e in result.errors)


@pytest.mark.parametrize("seconds", [0, -1, 1.5, "900", True, None])
def test_rejects_bad_interval_seconds(seconds):
    result = validate_trigger_config("time", {"kind": "interval", "seconds": seconds})
    assert not result.valid
    assert any("trigger_config.seconds" in e for e in result.errors)


def test_rejects_absurd_interval_with_a_pointer_to_monthly():
    result = validate_trigger_config(
        "time", {"kind": "interval", "seconds": 40_000_000})
    assert not result.valid
    assert any("monthly" in e for e in result.errors)


def test_true_is_not_an_integer():
    """bool subclasses int; a schedule of 'every True seconds' is nonsense."""
    result = validate_trigger_config(
        "condition", {"expression": "x", "evaluate_every_seconds": True})
    assert not result.valid


@pytest.mark.parametrize("weekdays", [
    [],
    ["monday"],
    ["mon", "mon"],
    ["Mon"],
    [1],
    "mon",
])
def test_rejects_bad_weekdays(weekdays):
    result = validate_trigger_config(
        "time", {"kind": "weekly", "at": "17:00", "weekdays": weekdays})
    assert not result.valid
    assert any("trigger_config.weekdays" in e for e in result.errors)


@pytest.mark.parametrize("days", [[], [0], [32], [1, 1], ["1"], 15])
def test_rejects_bad_days(days):
    result = validate_trigger_config("time", {"kind": "monthly", "at": "09:00", "days": days})
    assert not result.valid
    assert any("trigger_config.days" in e for e in result.errors)


@pytest.mark.parametrize("timezone", ["America/New York", "+05:00", "", 5, "/UTC", "UTC/"])
def test_rejects_malformed_timezone(timezone):
    result = validate_trigger_config(
        "time", {"kind": "daily", "at": "17:00", "timezone": timezone})
    assert not result.valid
    assert any("trigger_config.timezone" in e for e in result.errors)


def test_timezone_is_shape_checked_not_resolved():
    """
    A pure validator must give the same verdict on every machine.

    A well-formed IANA name that this machine's tz database happens not to
    carry is still a well-formed config; whether it resolves is
    next_fire_time's problem, at the moment it actually matters.
    """
    result = validate_trigger_config(
        "time", {"kind": "daily", "at": "17:00", "timezone": "Mars/Olympus_Mons"})
    assert result.valid, result.errors


@pytest.mark.parametrize("expression", ["", "   ", 42, None])
def test_rejects_empty_condition_expression(expression):
    result = validate_trigger_config("condition", {"expression": expression})
    assert not result.valid
    assert any("trigger_config.expression" in e for e in result.errors)


def test_rejects_missing_event_name():
    result = validate_trigger_config("event", {"source": "home_assistant"})
    assert not result.valid
    assert any("trigger_config.event" in e and "required" in e for e in result.errors)


# ============================================================================
# Malformed payloads
# ============================================================================

@pytest.mark.parametrize("config", [None, "not json at all", "[1, 2, 3]", "42", [], 7])
def test_rejects_non_object_payloads(config):
    """
    _parse_json_object in directives.py turns these into {} and hands the
    runner an empty dict with no error anywhere. That silence is the bug.
    """
    result = validate_trigger_config("time", config)
    assert not result.valid
    assert result.errors


def test_json_string_and_dict_agree():
    """
    Insert-time validation sees a dict or JSON text; the runner reads JSON
    text from the column. The two must never reach different verdicts.
    """
    import json
    for config in ({"kind": "daily", "at": "17:00"}, {"kind": "daily", "at": "9:00"}):
        as_dict = validate_trigger_config("time", config)
        as_text = validate_trigger_config("time", json.dumps(config))
        assert as_dict.valid == as_text.valid
        assert as_dict.errors == as_text.errors
        assert as_dict.kind == as_text.kind


# ============================================================================
# Error reporting
# ============================================================================

def test_all_errors_are_reported_not_just_the_first():
    """One call should be enough to fix the whole config."""
    result = validate_trigger_config(
        "time", {"kind": "weekly", "at": "25:00", "weekdays": ["funday"], "tz": "UTC"})
    assert not result.valid
    joined = " ".join(result.errors)
    assert "trigger_config.at" in joined
    assert "trigger_config.weekdays" in joined
    assert "trigger_config.tz" in joined


def test_every_error_names_a_key():
    result = validate_trigger_config("time", {"kind": "monthly", "at": "9", "days": [0]})
    assert not result.valid
    assert all("trigger_config" in e for e in result.errors)


def test_result_is_immutable():
    result = validate_trigger_config("manual", {})
    with pytest.raises(Exception):
        result.valid = False
    assert isinstance(result.errors, tuple)


# ============================================================================
# Grammar surface
# ============================================================================

def test_grammar_covers_every_trigger_type():
    grammar = trigger_config_grammar()
    assert set(grammar) == set(TRIGGER_TYPES)
    assert set(grammar["time"]["kinds"]) == set(TIME_KINDS)
    assert grammar["time"]["discriminator"] == "kind"
    assert grammar["time"]["weekday_tokens"] == list(WEEKDAY_TOKENS)


def test_grammar_can_be_narrowed():
    assert set(trigger_config_grammar("event")) == {"event"}
    assert trigger_config_grammar("nonsense") == {}


def test_grammar_lists_required_and_optional_keys():
    weekly = trigger_config_grammar("time")["time"]["kinds"]["weekly"]
    assert weekly["required"] == ["at", "kind", "weekdays"]
    assert weekly["optional"] == ["timezone"]


# ============================================================================
# Purity and import weight
# ============================================================================

def test_validator_touches_no_clock_or_database(monkeypatch):
    """
    Purity is the contract: the same config must validate identically at
    insert time in an MCP session and at 3am inside a runner.

    Checked structurally rather than by patching, because datetime.datetime
    is immutable and cannot be monkeypatched: the module must not hold a
    clock or a connection in its namespace at all.
    """
    import sqlite3

    from aimfp.hooks import triggers

    for forbidden in ("datetime", "sqlite3", "time", "os", "random"):
        assert not hasattr(triggers, forbidden), \
            f"triggers.py must stay pure; it imported {forbidden}"

    def explode(*args, **kwargs):
        raise AssertionError("validate_trigger_config must not open a database")

    monkeypatch.setattr(sqlite3, "connect", explode)
    assert validate_trigger_config("time", {"kind": "daily", "at": "17:00"}).valid
    assert not validate_trigger_config("time", {"kind": "daily", "at": "nope"}).valid


def test_same_config_validates_identically_on_repeat_calls():
    """No hidden state: a validator that drifts between calls is not pure."""
    config = {"kind": "weekly", "at": "17:00", "weekdays": ["mon", "fri"]}
    first = validate_trigger_config("time", config)
    second = validate_trigger_config("time", config)
    assert first == second
    assert config == {"kind": "weekly", "at": "17:00", "weekdays": ["mon", "fri"]}


def test_grammar_result_is_a_copy():
    """A caller mutating the returned dict must not corrupt the spec."""
    first = trigger_config_grammar()
    first["time"]["kinds"]["daily"]["required"].append("sabotage")
    second = trigger_config_grammar()
    assert "sabotage" not in second["time"]["kinds"]["daily"]["required"]


def test_triggers_module_does_not_import_watchdog():
    """
    Import weight is part of the hooks contract (see
    test_hooks_package_does_not_import_watchdog). A grammar module is exactly
    where a convenience dependency would sneak in.
    """
    import subprocess
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import aimfp.hooks.triggers; "
         "bad = [m for m in ('watchdog', 'croniter', 'dateutil') if m in sys.modules]; "
         "sys.exit(1 if bad else 0)"],
        capture_output=True,
        env={**os.environ, "PYTHONPATH": src},
    )
    assert result.returncode == 0, result.stderr.decode()
