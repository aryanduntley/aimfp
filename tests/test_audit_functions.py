"""audit_functions, source_hash recording, PARAM CHECK (milestone 8, task 59)."""
import json
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.audit.functions_audit import (
    audit_functions, json_encoding_problem, suggest_role,
)
from aimfp.helpers.audit.apply_fixes import apply_audit_fixes
from aimfp.helpers.audit.reports import _effect_load_report
from aimfp.helpers.catalog.extract import (
    extract_python, parameter_mismatches, python_function_hashes, recorded_parameter_facts,
)
from aimfp.helpers.catalog.reconcile import reconcile_paths
from aimfp.helpers.catalog.register import catalog_files, catalog_functions
from aimfp.helpers.orchestrators.entry_points import aimfp_init
from aimfp.helpers.project.files_1 import reserve_file, finalize_file
from aimfp.helpers.project.functions_1 import finalize_function, finalize_functions, reserve_function
from aimfp.helpers.project.functions_2 import update_function, update_functions_for_file


@pytest.fixture
def root(tmp_path):
    clear_project_root_cache()
    assert aimfp_init(str(tmp_path), init_git=False).success
    (tmp_path / "src").mkdir()
    yield str(tmp_path)
    clear_project_root_cache()


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _q(root, sql, params=()):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec(root, sql, params=()):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


MOD = '''from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    """A point."""
    x: int


def make(x: int) -> Point:
    """Build a point."""
    return Point(x)


def shift(p: Point, by: int = 1) -> Point:
    """Move a point."""
    return Point(p.x + by)


def _helper():
    return 1
'''


@pytest.fixture
def project(root):
    _write(root, "src/geo.py", MOD)
    reconcile_paths(["src/geo.py"], include_private=False, project_root=root)
    return root


def _report(root, result):
    return _effect_load_report(root, result.report_path)[0]


# ============================================================================
# Parameter comparison
# ============================================================================

SIG = extract_python("def f(a, b=1, *rest, c, **kw):\n    pass\n").functions[0].parameters


@pytest.mark.parametrize("recorded", [
    [{"name": "a", "required": True}, {"name": "b", "required": False}, {"name": "*rest"},
     {"name": "c", "required": True}, {"name": "**kw"}],
    [{"name": "a", "default": None, "kind": "positional"}, {"name": "b", "default": "1", "kind": "positional"},
     {"name": "*rest", "kind": "var_positional"}, {"name": "c", "default": None, "kind": "keyword_only"},
     {"name": "**kw", "kind": "var_keyword"}],
    ["a: int", "b: int = 1", "*rest", "c", "**kw"],
    [{"name": "a"}, {"name": "b", "default": None}, {"name": "rest"}, {"name": "c"}, {"name": "kw"}],
])
def test_recorded_formats_that_agree(recorded):
    assert parameter_mismatches(recorded, SIG) == ()


def test_mismatch_messages():
    problems = parameter_mismatches([{"name": "b", "required": True}, {"name": "a"}, {"name": "zz"}], SIG)
    assert "not recorded: rest, c, kw" in problems
    assert "recorded but not in the code: zz" in problems
    assert "order differs among shared parameters" in problems
    assert any("'b' recorded as required" in p for p in problems)
    assert parameter_mismatches([{"name": "b"}, {"name": "a"}, {"name": "rest"}, {"name": "c"}, {"name": "kw"}],
                                SIG)[0].startswith("order differs: code (a, b, rest, c, kw)")
    assert parameter_mismatches("nope", SIG) == ("recorded parameters are not a list of parameters",)
    assert recorded_parameter_facts([1]) is None


def test_json_encoding_problem():
    assert json_encoding_problem('[]', 'list') is None
    assert json_encoding_problem(json.dumps(json.dumps([])), 'list') == ('double-encoded', True)
    assert json_encoding_problem('"int"', 'object') == ('stored as a JSON string, expected a JSON object', False)
    assert json_encoding_problem('{', 'list') == ('invalid JSON', False)
    assert json_encoding_problem('{}', 'list') == ('not a JSON list', False)
    assert json_encoding_problem(None, 'list') is None


def test_suggest_role():
    assert suggest_role(False, True, "Point") == "factory"
    assert suggest_role(True, True, "Point") == "transformer"
    assert suggest_role(True, False, "bool") == "validator"
    assert suggest_role(True, False, "int") == "accessor"


# ============================================================================
# audit_functions
# ============================================================================

def test_reconciled_file_has_only_type_links_to_add(project):
    r = audit_functions(project_root=project)
    assert r.success and not r.blocking
    links = {(l["function"], l["role"]) for l in _report(project, r)["findings"]["missing_type_links"]}
    assert links == {("make", "factory"), ("shift", "transformer")}
    assert "private_tracked" not in r.counts              # _helper was not cataloged, never reported


def test_missing_stale_and_private(project):
    _exec(project, "INSERT INTO functions (name, file_id, purpose, parameters, returns) "
                   "VALUES ('_secret', 1, 'p', '[]', '{}')")
    _write(project, "src/geo.py", MOD.replace("def make(", "def build(").replace("_helper", "_helper2"))
    r = audit_functions(paths=["src/geo.py"], project_root=project)
    f = _report(project, r)["findings"]
    assert [m["name"] for m in f["missing_functions"]] == ["build"]        # _helper2 never reported
    assert {s["name"] for s in f["stale_functions"]} == {"make", "_secret"}
    assert {"missing_functions", "stale_functions"} <= set(r.blocking)


def test_tracked_private_function_is_allowed_and_not_listed(project):
    _write(project, "src/geo.py", MOD)
    reconcile_paths(["src/geo.py"], include_private=True, project_root=project)
    r = audit_functions(paths=["src/geo.py"], project_root=project)
    assert "private_tracked" not in r.counts
    # a tracked private function gets every check a public one gets
    assert [i["name"] for i in _report(project, r)["findings"]["incomplete_functions"]] == ["_helper"]


def test_incomplete_malformed_reserved_param(project):
    ids = dict(_q(project, "SELECT name, id FROM functions"))
    _exec(project, "UPDATE functions SET returns = NULL, is_reserved = 1 WHERE id = ?", (ids["make"],))
    _exec(project, "UPDATE functions SET parameters = ? WHERE id = ?",
          (json.dumps(json.dumps([{"name": "p"}])), ids["shift"]))
    r = audit_functions(paths=["src/geo.py"], project_root=project)
    f = _report(project, r)["findings"]
    assert f["incomplete_functions"][0]["fields"] == ["returns"]
    assert f["reserved_functions"][0]["name"] == "make"
    assert f["malformed_json"][0]["problem"] == "double-encoded" and f["malformed_json"][0]["fixable"]
    assert f["param_mismatch"][0]["problems"] == ["not recorded: by"]

    fixed = apply_audit_fixes("sync", report=r.report_path, apply=["malformed_json", "param_mismatch"],
                              project_root=project)
    # The refresh re-encodes parameters cleanly, so normalizing them is then a no-op
    assert fixed.applied == {"refresh_signatures": 1}
    assert fixed.skipped[0]["reason"] == "already well-formed"
    again = audit_functions(paths=["src/geo.py"], project_root=project)
    assert "malformed_json" not in again.counts and "param_mismatch" not in again.counts


def test_types_missing_stale_incomplete(project):
    _exec(project, "UPDATE types SET description = NULL WHERE name = 'Point'")
    _exec(project, "INSERT INTO types (name, file_id, definition_json, description) VALUES ('Gone', 1, '{}', 'd')")
    _exec(project, "INSERT INTO types (name, file_id, definition_json, description) VALUES ('Alias', 1, '{}', 'd')")
    _write(project, "src/geo.py", MOD + "\nAlias = int\n\n\n@dataclass\nclass Box:\n    w: int\n")
    f = _report(project, audit_functions(paths=["src/geo.py"], project_root=project))["findings"]
    assert [t["name"] for t in f["missing_types"]] == ["Box"]
    assert [t["name"] for t in f["stale_types"]] == ["Gone"]               # Alias is bound: not stale
    assert f["incomplete_types"][0]["fields"] == ["description"]


def test_files_untracked_missing_and_without_functions(project):
    _write(project, "src/new.py", "def f():\n    return 1\n")
    _write(project, "src/consts.py", "X = 1\n")
    reconcile_paths(["src/consts.py"], project_root=project)
    _write(project, "src/gone.py", "def g():\n    return 2\n")
    reconcile_paths(["src/gone.py"], project_root=project)
    os.remove(os.path.join(project, "src/gone.py"))
    r = audit_functions(project_root=project)
    report = _report(project, r)
    f = report["findings"]
    assert [u["path"] for u in f["untracked_files"]] == ["src/new.py"]
    assert [m["path"] for m in f["missing_files"]] == ["src/gone.py"]
    assert [w["path"] for w in f["files_without_functions"]] == ["src/consts.py"]
    assert report["reconcile_paths"] == ["src/new.py"]


def test_names_only_files_only_warn(project):
    _write(project, "src/web.ts", "export function shown() { return 1 }\n")
    catalog_files([{"path": "src/web.ts", "language": "typescript"}], project_root=project)
    r = audit_functions(paths=["src/web.ts"], project_root=project)
    assert r.counts == {"names_only_missing": 1, "unchecked_files": 1}
    assert r.warnings == ("names_only_missing",) and not r.blocking


# ============================================================================
# source_hash recording and PARAM CHECK
# ============================================================================

def _hash(root, name):
    return _q(root, "SELECT source_hash FROM functions WHERE name = ?", (name,))[0][0]


def test_reconcile_records_hash_only_with_a_docstring(root):
    _write(root, "src/m.py", 'def doc():\n    """Has prose."""\n    return 1\n\n\ndef bare():\n    return 2\n')
    reconcile_paths(["src/m.py"], project_root=root)
    assert _hash(root, "doc") == python_function_hashes(open(os.path.join(root, "src/m.py")).read())["doc"]
    assert _hash(root, "bare") is None


def test_catalog_and_update_paths_record_hash(root):
    _write(root, "src/m.py", "def a(x):\n    return x\n\n\ndef b(y):\n    return y\n")
    hashes = python_function_hashes(open(os.path.join(root, "src/m.py")).read())
    file_id = catalog_files([{"path": "src/m.py", "language": "python"}], project_root=root).ids[0]
    ids = catalog_functions([{"name": "a", "file_id": file_id, "purpose": "p"},
                             {"name": "b", "file_id": file_id}], project_root=root).ids
    assert _hash(root, "a") == hashes["a"] and _hash(root, "b") is None
    assert update_function(ids[1], purpose="now described", project_root=root).success
    assert _hash(root, "b") == hashes["b"]
    _exec(root, "UPDATE functions SET source_hash = NULL")
    # Only entries that rewrite purpose/parameters/returns are re-hashed
    assert update_functions_for_file(file_id, [{"function_id": ids[0], "purpose": "x"},
                                               {"function_id": ids[1], "name": "b"}], project_root=root).success
    assert _hash(root, "a") == hashes["a"] and _hash(root, "b") is None


def test_finalize_records_hash_and_warns_on_param_mismatch(root):
    reserved = reserve_file("calc.py", "src/calc.py", "python", flow_ids=None, no_flow_reason="test",
                            skip_id_naming=True, project_root=root)
    file_id = reserved.id
    _write(root, "src/calc.py", "def add(a, b=0):\n    return a + b\n\n\ndef neg(a):\n    return -a\n")
    finalize_file(file_id, "calc.py", "src/calc.py", "python", skip_id_naming=True, project_root=root)
    add_id = reserve_function("add", file_id, skip_id_naming=True, project_root=root).id
    result = finalize_function(add_id, "add", file_id, purpose="Add", returns={"type": "int"},
                               parameters=[{"name": "a", "required": True}, {"name": "b", "required": True}],
                               skip_id_naming=True, project_root=root)
    assert result.success
    assert any(w.startswith("PARAM CHECK 'add'") and "'b' recorded as required" in w for w in result.warnings)
    assert _hash(root, "add") == python_function_hashes(open(os.path.join(root, "src/calc.py")).read())["add"]

    neg_id = reserve_function("neg", file_id, skip_id_naming=True, project_root=root).id
    batch = finalize_functions([{"function_id": neg_id, "name": "neg", "file_id": file_id, "purpose": "Negate",
                                 "parameters": [{"name": "a", "required": True}], "returns": {"type": "int"},
                                 "skip_id_naming": True}], project_root=root)
    assert batch.success and batch.warnings == ()
    assert _hash(root, "neg") is not None
    # NAME/PARAM CHECK guidance ships only with warnings to act on
    assert any(s.startswith("PARAM CHECK") for s in result.return_statements)
    assert not any("CHECK" in s or s.startswith("[when:") for s in batch.return_statements)


# ============================================================================
# reserve_function(s) by file_path (CIAMstack: a wrong but existing file_id passed silently)
# ============================================================================

def test_reserve_by_path_and_echoed_file(root):
    from aimfp.helpers.project.functions_1 import reserve_functions
    a = reserve_file("a.py", "src/a.py", "python", flow_ids=None, no_flow_reason="test",
                     skip_id_naming=True, project_root=root).id
    b = reserve_file("b.py", "src/b.py", "python", flow_ids=None, no_flow_reason="test",
                     skip_id_naming=True, project_root=root).id

    one = reserve_function("f", file_path="./src/a.py", skip_id_naming=True, project_root=root)
    assert one.success and (one.file_id, one.file_path) == (a, "src/a.py")
    by_id = reserve_function("g", b, skip_id_naming=True, project_root=root)
    assert (by_id.file_id, by_id.file_path) == (b, "src/b.py")          # the path shows a wrong id at once

    batch = reserve_functions([{"name": "h", "file_path": "src/b.py"}, {"name": "i", "file_id": a}],
                              project_root=root)
    assert [(f["name"], f["file_id"], f["file_path"]) for f in batch.functions] == [
        ("h", b, "src/b.py"), ("i", a, "src/a.py")]
    assert [f["id"] for f in batch.functions] == list(batch.ids)

    assert "not 'src/a.py'" in reserve_function("x", b, file_path="src/a.py", project_root=root).error
    assert "No tracked file" in reserve_function("x", file_path="src/nope.py", project_root=root).error
    assert "file_id or file_path" in reserve_function("x", project_root=root).error
    refused = reserve_functions([{"name": "ok", "file_id": a}, {"name": "bad", "file_path": "src/zz.py"}],
                                project_root=root)
    assert not refused.success and refused.error.startswith("Function 'bad':")
    assert _q(root, "SELECT COUNT(*) FROM functions WHERE name IN ('ok', 'bad')")[0][0] == 0
