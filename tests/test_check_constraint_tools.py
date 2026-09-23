"""
Every *_allowed_check_constraints tool against every CHECK field AIMFP ships.

Regression for project note 72: three private copies of the CHECK parser
stopped at the first ')' and failed on any CHECK list whose SQL comments held
parentheses (sqlite_master keeps comments verbatim). All four tools now share
database/connection.py _parse_check_constraint, and this sweep keeps it that
way: a new CHECK whose comments trip the parser fails here, not in an AI session.
"""
import os
import re
import sqlite3
import tempfile

import pytest

from aimfp.database.connection import clear_project_root_cache, set_project_root
from aimfp.helpers.core.validation import core_allowed_check_constraints
from aimfp.helpers.project.validation import project_allowed_check_constraints
from aimfp.helpers.user_directives.validation import (
    user_directives_allowed_check_constraints,
)
from aimfp.helpers.user_preferences.validation import (
    user_preferences_allowed_check_constraints,
)

SCHEMA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas")

TOOLS = {
    "project": project_allowed_check_constraints,
    "user_preferences": user_preferences_allowed_check_constraints,
    "user_directives": user_directives_allowed_check_constraints,
    "aimfp_core": core_allowed_check_constraints,
}


def _schema(db: str) -> str:
    with open(os.path.join(SCHEMA_DIR, f"{db}.sql")) as f:
        return f.read()


def _check_fields(db: str):
    """(db, table, field) for every CHECK (field IN (...)) in a shipped schema."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_schema(db))
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    return [
        (db, table, field)
        for table, sql in tables
        for field in sorted(set(re.findall(r"CHECK\s*\(\s*(\w+)\s+IN", sql or "", re.I)))
    ]


CASES = [case for db in TOOLS for case in _check_fields(db)]


@pytest.fixture(scope="module")
def project_root():
    root = tempfile.mkdtemp(prefix="aimfp_checks_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    for db in ("project", "user_preferences", "user_directives"):
        conn = sqlite3.connect(os.path.join(root, ".aimfp-project", f"{db}.db"))
        conn.executescript(_schema(db))
        conn.close()
    return root


@pytest.fixture(autouse=True)
def _bound(project_root):
    clear_project_root_cache()
    set_project_root(project_root)
    yield
    clear_project_root_cache()


def test_sweep_found_the_fields_that_used_to_fail():
    assert ("project", "notes", "note_type") in CASES
    assert ("user_directives", "notes", "note_type") in CASES
    assert ("user_directives", "directive_dependencies", "dependency_type") in CASES
    assert ("aimfp_core", "directive_flow", "flow_type") in CASES


@pytest.mark.parametrize("db,table,field", CASES)
def test_tool_lists_every_check_field(db, table, field):
    result = TOOLS[db](table, field)

    assert result.success, f"{db}.{table}.{field}: {result.error}"
    assert result.values, f"{db}.{table}.{field}: empty value list"
    schema = _schema(db)
    for value in result.values:
        assert f"'{value}'" in schema, f"{db}.{table}.{field}: invented {value!r}"


def test_null_is_not_offered_as_a_value():
    """NULL in an IN list never matches, and a NULL column passes CHECK anyway.
    Listing the string 'NULL' would invite a literal 'NULL' the CHECK rejects."""
    result = project_allowed_check_constraints("project", "user_directives_status")
    assert result.success, result.error
    assert "NULL" not in result.values
    assert "active" in result.values
