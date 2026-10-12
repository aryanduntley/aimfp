"""Tracking audit foundation (milestone 8, task 57): JSON encoding, schema 1.15,
call-graph upgrades, the audit code index and report files."""
import ast
import json
import os
import sqlite3
import subprocess

import pytest

from aimfp.database.connection import (
    clear_project_root_cache, decode_json_layers, encode_json_field,
)
from aimfp.helpers.catalog.callgraph import (
    credited_dependencies, extract_function_references, extract_import_origins,
    resolve_target,
)
from aimfp.helpers.catalog.extract import function_body_hash, python_function_hashes
from aimfp.helpers.catalog.reconcile import reconcile_paths
from aimfp.helpers.audit.code_index import (
    ScopeRequest, build_code_index, build_scope_request, classify_scope_paths,
    is_whole_project, scope_label, under_prefix,
)
from aimfp.helpers.audit.reports import (
    _effect_load_report, _effect_write_report, audit_dir, build_report,
    resolve_report_path, stale_reports, summarize_findings,
)
from aimfp.helpers.orchestrators.entry_points import aimfp_init


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


# ============================================================================
# JSON encoding exactly once
# ============================================================================

PARAMS = [{"name": "a", "type": "int"}]


@pytest.mark.parametrize("value", [
    PARAMS,
    json.dumps(PARAMS),
    json.dumps(json.dumps(PARAMS)),
    json.dumps(json.dumps(json.dumps(PARAMS))),
])
def test_encode_json_field_never_double_encodes(value):
    assert encode_json_field(value) == json.dumps(PARAMS)


def test_encode_json_field_edges():
    assert encode_json_field(None) is None
    assert encode_json_field("null") is None          # stays visible to completeness gates
    assert encode_json_field("plain text") == '"plain text"'
    assert encode_json_field({"type": "int"}) == '{"type": "int"}'


def test_decode_json_layers_stops_at_plain_text():
    assert decode_json_layers('"[1, 2]"') == [1, 2]
    assert decode_json_layers("not json") == "not json"
    assert decode_json_layers([1]) == [1]


def test_reconcile_and_catalog_write_single_encoded_json(root):
    from aimfp.helpers.catalog.register import catalog_files, catalog_functions
    _write(root, "src/m.py", "def f(a):\n    return a\n")
    file_id = catalog_files([{"path": "src/m.py", "language": "python"}], project_root=root).ids[0]
    catalog_functions([{"name": "f", "file_id": file_id, "purpose": "p",
                        "parameters": json.dumps(json.dumps(PARAMS)), "returns": '{"type": "int"}'}],
                      project_root=root)
    (params, returns), = _q(root, "SELECT parameters, returns FROM functions WHERE name = 'f'")
    assert json.loads(params) == PARAMS and json.loads(returns) == {"type": "int"}


# ============================================================================
# Schema 1.15
# ============================================================================

def test_fresh_project_db_has_1_15_columns(root):
    assert _q(root, "SELECT version FROM schema_version") == [("1.16",)]
    function_cols = {r[1] for r in _q(root, "PRAGMA table_info(functions)")}
    interaction_cols = {r[1] for r in _q(root, "PRAGMA table_info(interactions)")}
    assert "source_hash" in function_cols
    assert "is_indirect" in interaction_cols
    _write(root, "src/calc.py", "def add(a, b):\n    return a + b\n\n\ndef total(x):\n    return add(x, 1)\n")
    reconcile_paths(["src/calc.py"], project_root=root)
    assert _q(root, "SELECT is_indirect FROM interactions") == [(0,)]


# ============================================================================
# Body hashes
# ============================================================================

def test_body_hash_ignores_docstring_formatting_and_name():
    a = python_function_hashes('def f(x):\n    "doc"\n    return x+1\n')["f"]
    b = python_function_hashes('def g(x):\n    """Other."""\n\n    return x + 1  # note\n')["g"]
    assert a == b


@pytest.mark.parametrize("changed", [
    'def f(x, y=1):\n    return x+1\n',          # signature
    'def f(x):\n    return x+2\n',               # body
    '@cache\ndef f(x):\n    return x+1\n',       # decorator
    'def f(x) -> int:\n    return x+1\n',        # return annotation
    'async def f(x):\n    return x+1\n',         # async
])
def test_body_hash_sees_code_changes(changed):
    assert python_function_hashes('def f(x):\n    return x+1\n')["f"] != python_function_hashes(changed)["f"]


def test_function_body_hash_does_not_mutate_node():
    tree = ast.parse('def f():\n    "doc"\n    return 1\n')
    before = ast.dump(tree)
    function_body_hash(tree.body[0])
    assert ast.dump(tree) == before


def test_python_function_hashes_on_syntax_error():
    assert python_function_hashes("def broken(:\n") == {}


# ============================================================================
# Call graph upgrades
# ============================================================================

def test_import_origins_maps_aliases_only():
    tree = ast.parse("from .a import f as g, h\nimport os as o\n")
    assert extract_import_origins(tree) == {"g": "f"}


def test_aliased_import_resolves_to_original_name():
    by_file = {"src/hooks.py": {"validate": 7}, "src/caller.py": {"validate": 9}}
    by_name = {"validate": [7, 9]}
    found = resolve_target(
        "_hook_validate", None, "src/caller.py", {"_hook_validate": "src/hooks"},
        by_file, by_name, origins={"_hook_validate": "validate"},
    )
    assert found == (7, "import")


def test_aliased_reexport_chain_resolves():
    by_file = {"src/impl.py": {"real": 3}}
    index = {"src/pkg/__init__.py": {"alias": "src/impl"}}
    origins_index = {"src/pkg/__init__.py": {"alias": "real"}}
    found = resolve_target(
        "alias", None, "src/caller.py", {"alias": "src/pkg"}, by_file, {"real": [3]},
        bindings_index=index, origins_index=origins_index,
    )
    assert found == (3, "reexport")


def test_untracked_local_name_never_guessed_project_wide():
    by_file = {"src/other.py": {"_helper": 5}, "src/here.py": {}}
    found = resolve_target("_helper", None, "src/here.py", {}, by_file, {"_helper": [5]},
                           defined_here=frozenset({"_helper"}))
    assert found == (None, "untracked_local")


def test_value_references_skip_unique_name_fallback():
    found = resolve_target("fn", None, "src/a.py", {}, {"src/b.py": {"fn": 2}}, {"fn": [2]},
                           allow_unique_name=False)
    assert found == (None, "ambiguous")


REFS = '''from functools import partial
from .lib import step, other as renamed
import pkg.mod as mod


def run(items, mapper=None):
    table = {"a": step, "b": mod.fn}
    bound = partial(renamed, 1)
    local = items
    return list(map(step, local)), table, bound, mapper
'''


def test_extract_function_references_collects_values_not_calls_or_locals():
    refs = dict(extract_function_references(ast.parse(REFS)))["run"]
    names = {n for n, _ in refs}
    assert ("step", None) in refs and ("renamed", None) in refs and ("fn", "mod") in refs
    assert not {"items", "mapper", "table", "bound", "local"} & names   # bound locally
    assert "partial" not in names and "map" not in names and "list" not in names  # called


HELPERS = '''def run(x):
    return _step(x)


def _step(x):
    return compute(x) + _deeper(x)


def _deeper(x):
    return [convert(v) for v in x]


def compute(x):
    return x


def convert(v):
    return v
'''


def test_credited_dependencies_fold_untracked_helpers():
    credited = credited_dependencies(ast.parse(HELPERS), frozenset({"run", "compute", "convert"}))
    run = {(n, v) for n, _, v in credited["run"]}
    assert ("compute", "call via _step") in run
    assert ("convert", "call via _deeper") in run
    assert ("_step", "call") in run
    assert set(credited) == {"run", "compute", "convert"}


def test_credited_dependencies_tracked_helper_keeps_its_own_edges():
    credited = credited_dependencies(ast.parse(HELPERS), frozenset({"run", "_step", "compute", "convert"}))
    run = {n for n, _, _ in credited["run"]}
    assert "compute" not in run and "_step" in run


def test_reconcile_records_reference_and_helper_edges(root):
    _write(root, "src/lib.py", "def step(v):\n    return v\n\n\ndef compute(x):\n    return x\n")
    _write(root, "src/app.py", (
        "from .lib import step, compute\n\n\n"
        "def run(items):\n    return list(map(step, items)) + _inner(items)\n\n\n"
        "def _inner(items):\n    return compute(items)\n"
    ))
    reconcile_paths(["src/lib.py", "src/app.py"], include_private=False, project_root=root)
    edges = _q(root, "SELECT s.name, t.name, i.description FROM interactions i "
                     "JOIN functions s ON s.id = i.source_function_id "
                     "JOIN functions t ON t.id = i.target_function_id ORDER BY t.name")
    assert [(s, t) for s, t, _ in edges] == [("run", "compute"), ("run", "step")]
    assert edges[0][2].endswith("via _inner")
    assert "references step" in edges[1][2]


# ============================================================================
# Scope resolution and the code index
# ============================================================================

def test_scope_request_and_label():
    assert is_whole_project(ScopeRequest())
    assert scope_label(ScopeRequest()) == "whole project"
    request = build_scope_request(task_id=3, since_commit=" HEAD ", module_path="/src/x/")
    assert not is_whole_project(request)
    assert request.since_commit == "HEAD" and request.module_path == "src/x"
    assert scope_label(request) == "task 3, since HEAD, module src/x"


def test_under_prefix():
    assert under_prefix("src/a/b.py", "src/a")
    assert not under_prefix("src/ab/b.py", "src/a")
    assert under_prefix("x.py", None) and under_prefix("x.py", ".")


def test_classify_scope_paths():
    audited, skipped = classify_scope_paths(
        ("src/a.py", "build/x.py", "notes.txt", "README.xyz", "src/gone.py"),
        frozenset({"README.xyz"}),
        lambda p: p.startswith("build/"),
        lambda p: p != "src/gone.py",
    )
    assert audited == ("src/a.py",)
    reasons = {s["path"]: s["reason"] for s in skipped}
    assert reasons == {
        "build/x.py": "excluded by watchdog exclusions",
        "README.xyz": "unsupported language",
        "src/gone.py": "not tracked and not on disk",
    }


def test_code_index_whole_project_and_selectors(root):
    _write(root, "src/a.py", "def f():\n    return 1\n")
    _write(root, "src/b.py", "def g():\n    return 2\n")
    reconcile_paths(["src/a.py"], project_root=root)
    index, error = build_code_index(root, ScopeRequest())
    assert error is None
    assert index.scope.paths == ("src/a.py", "src/b.py")      # untracked b.py is in scope too
    facts = {f.path: f for f in index.files}
    assert facts["src/a.py"].file_id is not None and facts["src/b.py"].file_id is None
    assert facts["src/a.py"].fidelity == "full"
    assert facts["src/a.py"].entities.functions[0].body_hash
    assert set(index.trees) == {"src/a.py"}                   # tracked Python files only
    assert index.snapshot.has_source_hash and index.snapshot.has_is_indirect

    index, _ = build_code_index(root, build_scope_request(paths=["src/b.py", "../x.py"]))
    assert index.scope.paths == ("src/b.py",)
    assert index.scope.skipped[0]["reason"].startswith("leaves the project")


def test_code_index_missing_file_and_bad_selectors(root):
    _write(root, "src/a.py", "def f():\n    return 1\n")
    reconcile_paths(["src/a.py"], project_root=root)
    os.remove(os.path.join(root, "src/a.py"))
    index, _ = build_code_index(root, build_scope_request(paths=["src/a.py"]))
    assert index.files[0].fidelity == "missing"
    assert build_code_index(root, build_scope_request(task_id=999))[1] == "task 999 not found"
    assert "Invalid task_type" in build_code_index(root, build_scope_request(task_id=1, task_type="epic"))[1]


def test_code_index_since_commit(tmp_path):
    clear_project_root_cache()
    root = str(tmp_path)
    assert aimfp_init(root, init_git=False).success
    git = lambda *a: subprocess.run(("git",) + a, cwd=root, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    _write(root, "src/a.py", "def f():\n    return 1\n")
    _write(root, "src/b.py", "def g():\n    return 2\n")
    git("add", "src")
    git("commit", "-qm", "init")
    _write(root, "src/a.py", "def f():\n    return 3\n")
    _write(root, "src/c.py", "def h():\n    return 4\n")
    index, error = build_code_index(root, build_scope_request(since_commit="HEAD"))
    assert error is None
    assert index.scope.paths == ("src/a.py", "src/c.py")
    assert "unknown revision" in build_code_index(root, build_scope_request(since_commit="nope"))[1]
    clear_project_root_cache()


# ============================================================================
# Report files
# ============================================================================

def test_summarize_and_build_report():
    findings = {"stale": ({"id": 1}, {"id": 2}, {"id": 3}), "missing": ()}
    assert summarize_findings(findings, 2) == {"stale": {"count": 3, "sample": [{"id": 1}, {"id": 2}]}}
    report = build_report("audit_x", {"label": "whole project"}, findings, {"extra": 1})
    assert report["counts"] == {"stale": 3, "missing": 0} and report["extra"] == 1


def test_stale_reports_keeps_newest_per_tool():
    names = ("a-1.json", "a-3.json", "a-2.json", "b-1.json", ".gitignore")
    assert stale_reports(names, "a", 2) == ("a-1.json",)
    assert stale_reports(names, "b", 2) == ()


def test_write_load_prune_report(root):
    paths = [_effect_write_report(root, "audit_x", build_report("audit_x", {}, {"k": ({"id": i},)}), keep=2)
             for i in range(3)]
    folder = audit_dir(root)
    assert open(os.path.join(folder, ".gitignore")).read().strip().endswith("*")
    assert sorted(n for n in os.listdir(folder) if n.endswith(".json")) == sorted(os.path.basename(p) for p in paths[1:])
    report, error = _effect_load_report(root, paths[-1])
    assert error is None and report["findings"]["k"] == [{"id": 2}]
    assert _effect_load_report(root, os.path.basename(paths[-1]))[0] == report


def test_report_paths_stay_inside_audit_folder(root):
    assert resolve_report_path(root, "../project.db")[0] is None
    assert resolve_report_path(root, ".aimfp-project/project.db")[0] is None
    assert resolve_report_path(root, "")[0] is None
    assert _effect_load_report(root, "missing.json")[1].startswith("report not found")


# ============================================================================
# Call graph precision (task 65): no false edges that would block completion
# ============================================================================

def test_bare_calls_to_locals_and_params_are_not_project_calls():
    from aimfp.helpers.catalog.callgraph import extract_function_calls
    tree = ast.parse("def f(callback, xs):\n    helper = make()\n    return helper(xs) + callback(1) + len(xs)\n")
    assert extract_function_calls(tree)["f"] == (("make", None), ("len", None))


def test_builtins_never_resolve_to_project_functions():
    assert resolve_target("len", None, "src/a.py", {}, {"src/b.py": {"len": 7}}, {"len": [7]}) == (None, "external")
    # ...unless the file defines or imports that name itself
    assert resolve_target("len", None, "src/a.py", {}, {"src/a.py": {"len": 3}}, {"len": [3]}) == (3, "local")


def test_unique_name_fallback_only_with_star_import(root):
    _write(root, "src/lib.py", "def helper():\n    return 1\n")
    _write(root, "src/a.py", "def f():\n    return helper()\n")                       # NameError in Python: no edge
    _write(root, "src/b.py", "from src.lib import *\n\n\ndef g():\n    return helper()\n")
    reconcile_paths(["src/lib.py", "src/a.py", "src/b.py"], project_root=root)
    edges = _q(root, "SELECT s.name, t.name FROM interactions i JOIN functions s ON s.id = i.source_function_id "
                     "JOIN functions t ON t.id = i.target_function_id")
    assert edges == [("g", "helper")]


def test_absolute_imports_resolve_from_any_package_root(root):
    from aimfp.helpers.catalog.callgraph import module_suffix_index
    index = module_suffix_index(("src/pkg/util.py", "src/pkg/__init__.py", "tools/util.py"))
    assert index["pkg/util"] == ("src/pkg/util.py",) and index["pkg"] == ("src/pkg/__init__.py",)
    assert set(index["util"]) == {"src/pkg/util.py", "tools/util.py"}                    # ambiguous: never used
    _write(root, "src/pkg/__init__.py", "")
    _write(root, "src/pkg/util.py", "def norm(x):\n    return x\n")
    _write(root, "src/app.py", "from pkg.util import norm\nimport pkg.util as u\n\n\n"
                               "def run(x):\n    return norm(x) + u.norm(x)\n")
    reconcile_paths(["src/pkg/__init__.py", "src/pkg/util.py", "src/app.py"], project_root=root)
    assert _q(root, "SELECT COUNT(*) FROM interactions i JOIN functions s ON s.id = i.source_function_id "
                    "WHERE s.name = 'run'") == [(1,)]
