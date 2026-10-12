"""audit_interactions + apply_audit_fixes (milestone 8, task 58)."""
import ast
import json
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.audit.apply_fixes import (
    FixPlan, apply_audit_fixes, merge_plans, parameters_from_source, plan_from_report,
)
from aimfp.helpers.audit.interactions_audit import audit_interactions, pair_repoints
from aimfp.helpers.audit.reports import _effect_load_report
from aimfp.helpers.catalog.callgraph import extract_function_calls, resolve_target, NON_NAME_RECEIVER
from aimfp.helpers.catalog.extract import extract_python
from aimfp.helpers.catalog.reconcile import reconcile_paths
from aimfp.helpers.catalog.register import catalog_functions
from aimfp.helpers.orchestrators.entry_points import aimfp_init
from aimfp.helpers.project.interactions import (
    add_interaction, add_interactions, get_interactions_by_file, update_interaction,
)


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


def _ids(root):
    return dict(_q(root, "SELECT name, id FROM functions"))


def _edges(root):
    return sorted(_q(root, "SELECT s.name, t.name FROM interactions i "
                           "JOIN functions s ON s.id = i.source_function_id "
                           "JOIN functions t ON t.id = i.target_function_id"))


LIB = "def add(a, b):\n    return a + b\n\n\ndef scale(x):\n    return x * 2\n"
APP = "from .lib import add\n\n\ndef run(x):\n    return add(x, 1)\n"


@pytest.fixture
def project(root):
    _write(root, "src/lib.py", LIB)
    _write(root, "src/app.py", APP)
    reconcile_paths(["src/lib.py", "src/app.py"], project_root=root)
    return root


# ============================================================================
# Audit
# ============================================================================

def test_reconciled_project_is_clean(project):
    r = audit_interactions(project_root=project)
    assert r.success and r.clean and r.counts == {}
    assert r.report_path is None          # nothing to report, nothing written


def test_missing_and_stale(project):
    _write(project, "src/app.py", "from .lib import scale\n\n\ndef run(x):\n    return scale(x)\n")
    r = audit_interactions(paths=["src/app.py"], project_root=project)
    assert r.counts["missing_interactions"] == 1 and r.counts["stale_interactions"] == 1
    assert set(r.blocking) == {"missing_interactions", "stale_interactions"}
    assert r.findings["stale_interactions"]["sample"][0]["target"] == "add"
    assert r.findings["missing_interactions"]["sample"][0]["target"] == "scale"
    assert os.path.isfile(os.path.join(project, r.report_path))


def test_call_moved_into_sibling_is_one_repoint(project):
    # run -> add becomes run -> step -> add; step is tracked without edges
    _write(project, "src/app.py", (
        "from .lib import add\n\n\ndef run(x):\n    return step(x)\n\n\n"
        "def step(x):\n    return add(x, 1)\n"))
    file_id = _q(project, "SELECT id FROM files WHERE path = 'src/app.py'")[0][0]
    catalog_functions([{"name": "step", "file_id": file_id, "purpose": "p",
                        "parameters": [], "returns": {}}], project_root=project)
    r = audit_interactions(paths=["src/app.py"], project_root=project)
    assert r.counts == {"repoint_interactions": 1, "missing_interactions": 1}
    repoint = r.findings["repoint_interactions"]["sample"][0]
    assert (repoint["change"], repoint["source"], repoint["new_source"]) == ("source", "run", "step")
    assert r.findings["missing_interactions"]["sample"][0]["target"] == "step"


def test_target_moved_files_is_a_target_repoint(project):
    _write(project, "src/math2.py", "def add(a, b):\n    return a + b\n")
    reconcile_paths(["src/math2.py"], project_root=project)
    _write(project, "src/app.py", "from .math2 import add\n\n\ndef run(x):\n    return add(x, 1)\n")
    r = audit_interactions(paths=["src/app.py"], project_root=project)
    repoint = r.findings["repoint_interactions"]["sample"][0]
    assert repoint["change"] == "target" and repoint["path"] == "src/math2.py"
    assert "missing_interactions" not in r.counts and "stale_interactions" not in r.counts


def test_stale_when_name_resolves_to_a_local_function(project):
    # The edge points at lib.add, but run now calls a local add of its own:
    # every use of the name resolves elsewhere, so the edge is provably gone
    _write(project, "src/app.py", (
        "def run(x):\n    return add(x, 1)\n\n\ndef add(a, b):\n    return a - b\n"))
    reconcile_paths(["src/app.py"], project_root=project)
    r = audit_interactions(paths=["src/app.py"], project_root=project)
    assert r.counts.get("stale_interactions") == 1 and "unconfirmed_interactions" not in r.counts


def test_unconfirmed_when_name_only_in_a_string(project):
    # A registry lookup by name: the call cannot be proven, nor ruled out
    _write(project, "src/app.py", 'OPS = {}\n\n\ndef run(x):\n    return OPS["add"](x, 1)\n')
    reconcile_paths(["src/app.py"], project_root=project)
    r = audit_interactions(paths=["src/app.py"], project_root=project)
    assert r.counts.get("unconfirmed_interactions") == 1
    assert r.warnings == ("unconfirmed_interactions",)


def test_indirect_and_non_call_edges_never_flagged(project):
    ids = _ids(project)
    add_interactions([(ids["run"], ids["scale"], "call", "dispatch table", True),
                      (ids["run"], ids["scale"], "chain", "data flows into scale")], project_root=project)
    r = audit_interactions(paths=["src/app.py"], project_root=project)
    assert "stale_interactions" not in r.counts
    assert r.counts["indirect_interactions"] == 1 and r.counts["non_call_interactions"] == 1


def test_unchecked_and_skipped_files_are_listed(project):
    _write(project, "src/new.py", "def f():\n    return 1\n")
    _write(project, "src/web.js", "function g() { return 1 }\n")
    r = audit_interactions(paths=["src/new.py", "src/web.js", "/abs.py"], project_root=project)
    unchecked = {u["path"]: u for u in _effect_load_report(project, r.report_path)[0]["findings"]["unchecked_files"]}
    assert unchecked["src/new.py"]["reason"] == "not tracked"
    assert unchecked["src/web.js"]["fidelity"] == "names_only"
    assert r.counts["skipped_paths"] == 1


def test_bad_scope_is_an_error(project):
    r = audit_interactions(task_id=404, project_root=project)
    assert not r.success and "not found" in r.error


def test_pair_repoints_each_edge_once():
    stale = ({"interaction_id": 1, "source_function_id": 10, "target_function_id": 20,
              "source": "a", "target": "t", "source_path": "f.py"},
             {"interaction_id": 2, "source_function_id": 11, "target_function_id": 20,
              "source": "b", "target": "t", "source_path": "f.py"})
    missing = ({"source_function_id": 12, "target_function_id": 20, "source": "c", "target": "t",
                "source_path": "f.py", "target_path": "g.py", "description": "c calls t"},)
    repoints, left_stale, left_missing = pair_repoints(stale, missing)
    assert [r["interaction_id"] for r in repoints] == [1]
    assert [s["interaction_id"] for s in left_stale] == [2] and left_missing == ()


# ============================================================================
# Resolver: method calls on values never guess project-wide
# ============================================================================

def test_method_calls_on_values_are_not_project_calls():
    tree = ast.parse("def f(rows):\n    return ', '.join(rows) + rows[0].get('x')\n")
    calls = extract_function_calls(tree)["f"]
    assert ("join", NON_NAME_RECEIVER) in calls
    by_file = {"src/util.py": {"join": 1}}
    assert resolve_target("join", NON_NAME_RECEIVER, "src/a.py", {}, by_file, {"join": [1]}) == (None, "external")
    assert resolve_target("get", "rows", "src/a.py", {}, {"x.py": {"get": 2}}, {"get": [2]}) == (None, "external")


def test_package_relative_submodule_import_resolves():
    tree = ast.parse("from . import util\n\n\ndef f():\n    return util.helper()\n")
    from aimfp.helpers.catalog.callgraph import extract_import_bindings
    bindings = extract_import_bindings(tree, "src/pkg/a.py")
    assert bindings["util"] == "src/pkg/util"
    found = resolve_target("helper", "util", "src/pkg/a.py", bindings,
                           {"src/pkg/util.py": {"helper": 4}}, {"helper": [4]})
    assert found == (4, "module_alias")


# ============================================================================
# is_indirect on the interaction tools
# ============================================================================

def test_interaction_tools_carry_is_indirect(project):
    added = add_interaction("run", "scale", "call", description="via table", is_indirect=True, project_root=project)
    assert added.success
    assert _q(project, "SELECT is_indirect, description FROM interactions WHERE id = ?", (added.id,)) == [(1, "via table")]
    assert update_interaction(added.id, is_indirect=False, project_root=project).success
    records = {r.id: r for r in get_interactions_by_file(
        _q(project, "SELECT id FROM files WHERE path = 'src/app.py'")[0][0], project_root=project).interactions}
    assert records[added.id].is_indirect is False
    assert not add_interactions([(1, 2)], project_root=project).success       # wrong arity refused


# ============================================================================
# apply_audit_fixes
# ============================================================================

def test_apply_from_report_reproduces_code_graph_with_manifest(project):
    _write(project, "src/app.py", (
        "from .lib import add, scale\n\n\ndef run(x):\n    return step(x)\n\n\n"
        "def step(x):\n    return add(scale(x), 1)\n"))
    file_id = _q(project, "SELECT id FROM files WHERE path = 'src/app.py'")[0][0]
    catalog_functions([{"name": "step", "file_id": file_id, "purpose": "p", "parameters": [], "returns": {}}],
                      project_root=project)
    audit = audit_interactions(paths=["src/app.py"], project_root=project)
    before = _edges(project)

    dry = apply_audit_fixes("test", report=audit.report_path,
                            apply=["repoint_interactions", "missing_interactions", "stale_interactions"],
                            dry_run=True, project_root=project)
    assert dry.success and dry.dry_run and dry.manifest_note_id is None
    assert _edges(project) == before                              # rolled back

    done = apply_audit_fixes("test", report=audit.report_path,
                             apply=["repoint_interactions", "missing_interactions", "stale_interactions"],
                             cleanup=True, project_root=project)
    assert done.success and done.applied == {"repoint_interactions": 1, "add_interactions": 2}
    assert ("step", "add") in _edges(project) and ("run", "add") not in _edges(project)
    assert audit_interactions(paths=["src/app.py"], project_root=project).clean
    assert done.report_deleted and not os.path.exists(os.path.join(project, audit.report_path))
    content, = _q(project, "SELECT content FROM notes WHERE id = ?", (done.manifest_note_id,))[0]
    manifest = json.loads(content.split("\n", 1)[1])
    assert manifest["changed_interactions"][0]["old_values"]["source_function_id"] == _ids(project)["run"]


def test_apply_skips_rows_that_changed_since_the_audit(project):
    _write(project, "src/app.py", "def run(x):\n    return x\n")
    audit = audit_interactions(paths=["src/app.py"], project_root=project)
    edge_id = audit.findings["stale_interactions"]["sample"][0]["interaction_id"]
    update_interaction(edge_id, target_function_id=_ids(project)["scale"], project_root=project)
    r = apply_audit_fixes("t", report=audit.report_path, apply=["stale_interactions"], project_root=project)
    assert r.success and r.applied == {} and r.skipped[0]["reason"] == "edge changed since the audit"


def test_report_deletes_keep_edges_marked_indirect(project):
    _write(project, "src/app.py", "def run(x):\n    return x\n")
    audit = audit_interactions(paths=["src/app.py"], project_root=project)
    edge_id = audit.findings["stale_interactions"]["sample"][0]["interaction_id"]
    r = apply_audit_fixes("keep the conceptual link", report=audit.report_path,
                          apply=["stale_interactions"], mark_indirect_ids=[edge_id], project_root=project)
    assert r.applied == {"set_indirect": 1}
    assert r.skipped[0]["reason"] == "marked indirect: deliberate, kept"
    assert _q(project, "SELECT is_indirect FROM interactions WHERE id = ?", (edge_id,)) == [(1,)]


def test_delete_function_records_cascades_in_manifest(project):
    ids = _ids(project)
    r = apply_audit_fixes("gone", delete_function_ids=[ids["add"]], project_root=project)
    assert r.applied == {"delete_functions": 1}
    content = _q(project, "SELECT content FROM notes WHERE id = ?", (r.manifest_note_id,))[0][0]
    manifest = json.loads(content.split("\n", 1)[1])
    assert manifest["deleted_functions"][0]["name"] == "add"
    assert {(e["source_function_id"], e["target_function_id"]) for e in manifest["deleted_interactions"]} == {
        (ids["run"], ids["add"])}
    assert _q(project, "SELECT COUNT(*) FROM interactions") == [(0,)]


def test_refresh_signature_keeps_descriptions_and_baseline_hash(project):
    ids = _ids(project)
    conn = sqlite3.connect(os.path.join(project, ".aimfp-project", "project.db"))
    conn.execute("UPDATE functions SET parameters = ? WHERE id = ?",
                 (json.dumps([{"name": "a", "description": "left operand"}]), ids["add"]))
    conn.commit()
    conn.close()
    r = apply_audit_fixes("sync", refresh_signature_function_ids=[ids["add"]],
                          baseline_hash_function_ids=[ids["add"]], project_root=project)
    assert r.applied == {"refresh_signatures": 1, "baseline_hashes": 1}
    params, digest = _q(project, "SELECT parameters, source_hash FROM functions WHERE id = ?", (ids["add"],))[0]
    assert json.loads(params) == [
        {"name": "a", "type": None, "required": True, "description": "left operand"},
        {"name": "b", "type": None, "required": True},
    ]
    assert digest == extract_python(LIB).functions[0].body_hash


def test_normalize_json_and_type_links(project):
    ids = _ids(project)
    conn = sqlite3.connect(os.path.join(project, ".aimfp-project", "project.db"))
    conn.execute("UPDATE functions SET returns = ? WHERE id = ?", (json.dumps(json.dumps({"type": "int"})), ids["add"]))
    conn.execute("INSERT INTO types (name, file_id, definition_json) VALUES ('T', 1, '{}')")
    conn.commit()
    type_id = conn.execute("SELECT id FROM types WHERE name = 'T'").fetchone()[0]
    conn.close()
    report_like = {"findings": {"malformed_json": [{"table": "functions", "column": "returns", "id": ids["add"]}],
                                "missing_type_links": [{"type_id": type_id, "function_id": ids["add"], "role": "factory"}]}}
    plan, refused = plan_from_report(report_like, ("malformed_json", "missing_type_links"))
    assert refused == () and len(plan.normalize_json) == 1 and len(plan.add_type_links) == 1
    from aimfp.helpers.audit.apply_fixes import _apply_plan
    from aimfp.database.connection import _open_project_connection
    conn = _open_project_connection(project)
    applied, skipped, manifest = _apply_plan(conn, project, plan)
    conn.commit()
    conn.close()
    assert applied == {"normalize_json": 1, "add_type_links": 1} and skipped == []
    assert _q(project, "SELECT returns FROM functions WHERE id = ?", (ids["add"],)) == [('{"type": "int"}',)]


def test_apply_refusals(project):
    assert "reason is required" in apply_audit_fixes("", delete_interaction_ids=[1], project_root=project).error
    assert "pass report" in apply_audit_fixes("x", apply=["stale_interactions"], project_root=project).error
    assert "Nothing to apply" in apply_audit_fixes("x", project_root=project).error
    _write(project, "src/app.py", "def run(x):\n    return x\n")
    audit = audit_interactions(paths=["src/app.py"], project_root=project)
    refused = apply_audit_fixes("x", report=audit.report_path, apply=["unconfirmed_interactions"], project_root=project)
    assert "Not appliable" in refused.error


def test_merge_plans_and_parameters_from_source():
    merged = merge_plans(FixPlan(delete_interactions=({"interaction_id": 1},)),
                         FixPlan(delete_interactions=({"interaction_id": 2},)))
    assert [d["interaction_id"] for d in merged.delete_interactions] == [1, 2]
    fn = extract_python("def f(a, *args, b=1, **kw):\n    return a\n").functions[0]
    params = parameters_from_source(fn, ["a: int"])
    assert [(p["name"], p["required"]) for p in params] == [("a", True), ("*args", False), ("b", False), ("**kw", False)]
    assert params[2]["default"] == "1" and params[2]["kind"] == "keyword_only"


def test_report_delete_skips_edge_whose_type_changed(project):
    _write(project, "src/app.py", "def run(x):\n    return x\n")
    audit = audit_interactions(paths=["src/app.py"], project_root=project)
    edge_id = audit.findings["stale_interactions"]["sample"][0]["interaction_id"]
    update_interaction(edge_id, interaction_type="chain", project_root=project)   # reclassified as deliberate
    r = apply_audit_fixes("t", report=audit.report_path, apply=["stale_interactions"], project_root=project)
    assert r.applied == {} and r.skipped[0]["reason"] == "edge changed since the audit"
