"""Audit exemptions (task 67, project schema 1.16): reviewed findings that are not drift."""
import json
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache, set_project_root
from aimfp.helpers.audit.apply_fixes import apply_audit_fixes, exemption_problem
from aimfp.helpers.audit.exemptions import apply_exemptions, finding_entity, is_lapsed
from aimfp.helpers.audit.functions_audit import audit_functions
from aimfp.helpers.audit.tracking_audit import audit_tracking
from aimfp.helpers.catalog.reconcile import reconcile_paths
from aimfp.helpers.orchestrators.entry_points import aimfp_init
from aimfp.helpers.orchestrators.migration import migrate_databases


@pytest.fixture
def root(tmp_path):
    clear_project_root_cache()
    assert aimfp_init(str(tmp_path), init_git=False).success
    (tmp_path / "src").mkdir()
    yield str(tmp_path)
    clear_project_root_cache()


def _db(root):
    return os.path.join(root, ".aimfp-project", "project.db")


def _q(root, sql, params=()):
    conn = sqlite3.connect(_db(root))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.fetchall()
    finally:
        conn.close()


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _fn(root, name):
    return _q(root, "SELECT id FROM functions WHERE name = ?", (name,))[0][0]


# Defined inside an `if` at module level: the audit only sees top-level defs
CONDITIONAL = ("import sys\n\nif sys.version_info >= (3, 11):\n    def compat(x):\n        return x\n"
               "else:\n    def compat(x):\n        return x\n\n\ndef run(x) -> int:\n    \"\"\"Run.\"\"\"\n    return x\n")


def _stale_setup(root):
    _write(root, "src/app.py", CONDITIONAL)
    reconcile_paths(["src/app.py"], project_root=root)
    file_id = _q(root, "SELECT id FROM files WHERE path = 'src/app.py'")[0][0]
    _q(root, "INSERT INTO functions (name, file_id, purpose, parameters, returns, id_in_name) "
             "VALUES ('compat', ?, 'p', '[]', '{\"type\": \"None\"}', 0)", (file_id,))
    return _fn(root, "compat")


# -- schema -------------------------------------------------------------------

def test_schema_1_16_table_and_constraints(root):
    assert _q(root, "SELECT version FROM schema_version") == [("1.16",)]
    compat = _stale_setup(root)
    _q(root, "INSERT INTO audit_exemptions (category, function_id, reason) VALUES ('stale_functions', ?, 'r')", (compat,))
    with pytest.raises(sqlite3.IntegrityError):    # one per (category, entity)
        _q(root, "INSERT INTO audit_exemptions (category, function_id, reason) VALUES ('stale_functions', ?, 'r')", (compat,))
    with pytest.raises(sqlite3.IntegrityError):    # exactly one entity
        _q(root, "INSERT INTO audit_exemptions (category, reason) VALUES ('stale_functions', 'r')")
    with pytest.raises(sqlite3.IntegrityError):    # only exemptable categories
        _q(root, "INSERT INTO audit_exemptions (category, function_id, reason) VALUES ('missing_interactions', ?, 'r')", (compat,))
    _q(root, "DELETE FROM functions WHERE id = ?", (compat,))
    assert _q(root, "SELECT COUNT(*) FROM audit_exemptions") == [(0,)]    # cascades with the function


def test_migration_from_1_15_adds_the_table_and_keeps_rows(root):
    _stale_setup(root)
    _q(root, "DROP INDEX idx_audit_exemptions_function")
    _q(root, "DROP INDEX idx_audit_exemptions_type")
    _q(root, "DROP TABLE audit_exemptions")
    _q(root, "UPDATE schema_version SET version = '1.15'")
    set_project_root(root)
    result = migrate_databases()
    assert result.success, result.error
    migrated = next(m for m in result.data["migrated"] if m["db_name"] == "project")
    assert migrated["old_version"] == "1.15" and migrated["new_version"] == "1.16"
    conn = sqlite3.connect(migrated["original_db_path"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit_exemptions").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM functions WHERE name = 'compat'").fetchone() == (1,)
    finally:
        conn.close()


# -- audits honour exemptions ---------------------------------------------------

def test_exempted_stale_function_moves_to_exempted_and_stops_blocking(root):
    compat = _stale_setup(root)
    assert "stale_functions" in audit_functions(paths=["src/app.py"], project_root=root).blocking
    r = apply_audit_fixes("defined inside an if at module level", project_root=root,
                          exempt=[{"category": "stale_functions", "function_id": compat}])
    assert r.success and r.applied == {"add_exemptions": 1}
    audit = audit_tracking(paths=["src/app.py"], project_root=root)
    assert "stale_functions" not in audit.counts and not audit.blocking
    exempted = audit.findings["exempted"]["sample"][0]
    assert exempted["function_id"] == compat and exempted["reason"] == "defined inside an if at module level"


def test_function_exemption_lapses_when_its_code_changes(root):
    _write(root, "src/lib.py", "def f(a, b):\n    return a\n")
    reconcile_paths(["src/lib.py"], project_root=root)
    f = _fn(root, "f")
    _q(root, "UPDATE functions SET parameters = ? WHERE id = ?",
       (json.dumps([{"name": "a", "required": True}]), f))     # recorded params drift from the code
    assert "param_mismatch" in audit_functions(paths=["src/lib.py"], project_root=root).counts
    apply_audit_fixes("signature generated at import", project_root=root,
                      exempt=[{"category": "param_mismatch", "function_id": f}])
    assert "param_mismatch" not in audit_functions(paths=["src/lib.py"], project_root=root).counts
    _write(root, "src/lib.py", "def f(a, b):\n    return b\n")      # code changed: review again
    r = audit_functions(paths=["src/lib.py"], project_root=root)
    assert r.counts.get("param_mismatch") == 1
    assert r.findings["param_mismatch"]["sample"][0]["exemption_lapsed"]


def test_unexempt_reports_the_finding_again_and_manifests_the_row(root):
    compat = _stale_setup(root)
    apply_audit_fixes("reviewed", project_root=root, exempt=[{"category": "stale_functions", "function_id": compat}])
    exemption_id = _q(root, "SELECT id FROM audit_exemptions")[0][0]
    r = apply_audit_fixes("code is gone after all", project_root=root, unexempt_ids=[exemption_id])
    assert r.applied == {"delete_exemptions": 1}
    note = _q(root, "SELECT content FROM notes WHERE id = ?", (r.manifest_note_id,))[0][0]
    assert '"deleted_exemptions":[{' in note
    assert "stale_functions" in audit_functions(paths=["src/app.py"], project_root=root).counts


def test_deleting_an_exempted_function_records_the_cascade(root):
    compat = _stale_setup(root)
    apply_audit_fixes("reviewed", project_root=root, exempt=[{"category": "stale_functions", "function_id": compat}])
    r = apply_audit_fixes("really gone", project_root=root, delete_function_ids=[compat])
    note = _q(root, "SELECT content FROM notes WHERE id = ?", (r.manifest_note_id,))[0][0]
    assert '"deleted_exemptions":[{' in note and _q(root, "SELECT COUNT(*) FROM audit_exemptions") == [(0,)]


def test_exempt_skips_duplicates_and_wrong_entities(root):
    compat = _stale_setup(root)
    apply_audit_fixes("reviewed", project_root=root, exempt=[{"category": "stale_functions", "function_id": compat}])
    r = apply_audit_fixes("again", project_root=root, exempt=[
        {"category": "stale_functions", "function_id": compat},
        {"category": "stale_types", "function_id": compat},
        {"category": "stale_functions", "function_id": 99999}])
    reasons = sorted(s["reason"] for s in r.skipped)
    assert reasons[0].startswith("already exempted") and "function not found" in reasons
    assert any("about types" in x for x in reasons)


def test_exempt_on_a_pre_1_16_database_is_skipped_with_a_reason(root):
    compat = _stale_setup(root)
    _q(root, "DROP TABLE audit_exemptions")
    r = apply_audit_fixes("reviewed", project_root=root, exempt=[{"category": "stale_functions", "function_id": compat}])
    assert r.skipped[0]["reason"].startswith("database predates schema 1.16")
    audit = audit_functions(paths=["src/app.py"], project_root=root)
    assert "stale_functions" in audit.counts and audit.scope["schema_1_16"] is False


# -- pure helpers -------------------------------------------------------------

def test_exemption_problem():
    assert exemption_problem({"category": "stale_functions", "function_id": 1, "reason": "r"}) is None
    assert "cannot be exempted" in exemption_problem({"category": "missing_interactions", "function_id": 1, "reason": "r"})
    assert "exactly one" in exemption_problem({"category": "malformed_json", "function_id": 1, "type_id": 2, "reason": "r"})
    assert "reason" in exemption_problem({"category": "stale_types", "type_id": 1, "reason": ""})


def test_finding_entity_and_lapse():
    assert finding_entity("malformed_json", {"table": "types", "id": 4}) == ("type", 4)
    assert finding_entity("dangling_mentions", {"type_id": 2, "name": "T"}) == ("type", 2)
    assert finding_entity("missing_functions", {"name": "x"}) is None
    assert not is_lapsed({"code_hash": None}, "abc") and is_lapsed({"code_hash": "a"}, "b")
    findings = {"stale_types": ({"type_id": 2, "name": "T"},), "skipped_paths": ()}
    out = apply_exemptions(findings, ({"id": 9, "category": "stale_types", "function_id": None, "type_id": 2,
                                       "reason": "alias", "code_hash": None},), {})
    assert out["stale_types"] == () and out["exempted"][0]["exemption_id"] == 9
