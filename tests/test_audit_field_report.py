"""Audit verdict accuracy (task 66): the CIAMstack field report's regression fixtures.

One case per finding: a code shape the audit used to misjudge, and the verdict
it must reach now. Confirmed = in no finding category; unconfirmed = a warning
with a reason; stale = provably gone.
"""
import ast
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.audit.apply_fixes import apply_audit_fixes, exclude_findings, exclusion_problem
from aimfp.helpers.audit.functions_audit import audit_functions
from aimfp.helpers.audit.interactions_audit import audit_interactions
from aimfp.helpers.audit.reports import _effect_load_report
from aimfp.helpers.catalog.callgraph import (
    assigned_callables, extract_function_calls, record_definitions, value_fields_in,
)
from aimfp.helpers.catalog.reconcile import reconcile_paths
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


def _sql(root, sql, params=()):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.fetchall() if cursor.description else cursor.lastrowid
    finally:
        conn.close()


def _fn(root, path, name):
    """ID of the tracked function `name` in `path`."""
    return _sql(root, "SELECT fn.id FROM functions fn JOIN files f ON f.id = fn.file_id "
                      "WHERE f.path = ? AND fn.name = ?", (path, name))[0][0]


def _track(root, path, name, language="python"):
    """Track a function the cataloger does not create (assignment-bound, SQL)."""
    rows = _sql(root, "SELECT id FROM files WHERE path = ?", (path,))
    file_id = rows[0][0] if rows else _sql(
        root, "INSERT INTO files (name, path, language, id_in_name) VALUES (?, ?, ?, 0)",
        (os.path.basename(path), path, language))
    return _sql(root, "INSERT INTO functions (name, file_id, purpose, parameters, returns, id_in_name) "
                      "VALUES (?, ?, 'p', '[]', '{\"type\": \"None\"}', 0)", (name, file_id))


def _edge(root, source, target):
    return _sql(root, "INSERT INTO interactions (source_function_id, target_function_id, interaction_type) "
                      "VALUES (?, ?, 'call')", (source, target))


def _verdicts(root, paths):
    """{interaction_id: category} for every flagged edge, plus the report."""
    r = audit_interactions(paths=paths, project_root=root)
    report, _ = _effect_load_report(root, r.report_path) if r.report_path else ({'findings': {}}, None)
    found = {
        item['interaction_id']: category
        for category in ('stale_interactions', 'unconfirmed_interactions', 'repoint_interactions')
        for item in report['findings'].get(category, ())
    }
    return found, report


# -- Finding 1: partial-built functions --------------------------------------

def test_assignment_bound_function_is_not_stale(root):
    _write(root, "src/checks.py", (
        "from functools import partial\n\n\ndef check_one(x, *, kind):\n    return (x, kind)\n\n\n"
        "check_all = partial(check_one, kind='a')\nalias = check_one\n"))
    reconcile_paths(["src/checks.py"], project_root=root)
    _track(root, "src/checks.py", "check_all")
    _track(root, "src/checks.py", "alias")
    r = audit_functions(paths=["src/checks.py"], project_root=root)
    assert "stale_functions" not in r.counts and "param_mismatch" not in r.counts


def test_assignment_bound_function_dependencies_are_expected(root):
    _write(root, "src/checks.py", (
        "from functools import partial\n\n\ndef check_one(x, *, kind):\n    return (x, kind)\n\n\n"
        "check_all = partial(check_one, kind='a')\n"))
    reconcile_paths(["src/checks.py"], project_root=root)
    _track(root, "src/checks.py", "check_all")
    r = audit_interactions(paths=["src/checks.py"], project_root=root)
    assert r.counts.get("missing_interactions") == 1    # check_all -> check_one, through partial


def test_assigned_callables_shapes():
    tree = ast.parse("a = partial(f, 1)\nb = lambda x: x\nc = f\nd = mod.f\nN = 3\nS: str = 'x'\n")
    assert set(assigned_callables(tree)) == {"a", "b", "c", "d"}


# -- Finding 2a: dispatch tables ---------------------------------------------

def test_dispatch_table_edge_is_confirmed(root):
    _write(root, "src/cli.py", (
        "from types import MappingProxyType\n\n\ndef init(c):\n    return 1\n\n\ndef load(c):\n    return 2\n\n\n"
        "COMMANDS = MappingProxyType({'init': init, 'load': load})\n\n\n"
        "def main(cmd, c):\n    return COMMANDS[cmd](c)\n"))
    reconcile_paths(["src/cli.py"], project_root=root)
    main = _fn(root, "src/cli.py", "main")
    edges = [_edge(root, main, _fn(root, "src/cli.py", n)) for n in ("init", "load")]
    found, _ = _verdicts(root, ["src/cli.py"])
    assert not set(edges) & set(found)


def test_dispatch_through_nested_and_imported_tables(root):
    _write(root, "src/plumbing.py", (
        "def egress(m):\n    return m\n\n\ndef route(m):\n    return m\n\n\n"
        "PAIRS = (('a', egress), ('b', route))\nRENDERERS = (PAIRS,)\n"))
    _write(root, "src/render.py", (
        "from .plumbing import RENDERERS\n\n\n"
        "def render(m):\n    return tuple(fn(m) for group in RENDERERS for _, fn in group)\n"))
    reconcile_paths(["src/plumbing.py", "src/render.py"], project_root=root)
    render = _fn(root, "src/render.py", "render")
    edges = [_edge(root, render, _fn(root, "src/plumbing.py", n)) for n in ("egress", "route")]
    found, _ = _verdicts(root, ["src/render.py"])
    assert not set(edges) & set(found)


# -- Finding 2b: record fields, factories, callbacks ------------------------

RECORDS = (
    "from typing import Callable, NamedTuple\nfrom collections import namedtuple\n\n\n"
    "class Change(NamedTuple):\n    name: str\n    touches: Callable\n\n\n"
    "Codec = namedtuple('Codec', ('split', 'encode'))\n")


def test_record_definitions():
    records = record_definitions(ast.parse(RECORDS + "Other = NamedTuple('Other', [('a', int), ('b', str)])\n"))
    assert records == {"Change": ("name", "touches"), "Codec": ("split", "encode"), "Other": ("a", "b")}


def test_value_fields_keyword_positional_wrapped_and_factory():
    tree = ast.parse("A = Rec(fn=f)\nB = Change('x', g)\nC = Rec(fn=partial(h, 1))\nD = Rec(fn=make(1))\n"
                     "E = {'fn': mod.k}\n")
    fields = value_fields_in(tree, lambda name, base: ("name", "touches") if name == "Change" else ())
    assert {(f, n) for f, n, _ in fields} >= {
        ("fn", "f"), ("touches", "g"), ("fn", "h"), ("fn", "make"), ("fn", "k")}


def test_call_through_record_field_confirmed_when_unique_else_unconfirmed(root):
    _write(root, "src/records.py", RECORDS)
    _write(root, "src/versions.py", (
        "from .records import Change, Codec\n\n\ndef removed(d):\n    return d\n\n\n"
        "def split(text):\n    return text.split()\n\n\ndef encode(v):\n    return v\n\n\n"
        "def touching(files):\n    return lambda d: files\n\n\n"
        "CHANGES = (Change('a', removed), Change('b', touching(('x',))))\nCODEC = Codec(split, encode)\n"))
    _write(root, "src/upgrades.py", (
        "def crossing(changes, d):\n    return tuple(c.touches(d) for c in changes)\n\n\n"
        "def capture(fmt, text):\n    return fmt.codec.split(text)\n"))
    reconcile_paths(["src/records.py", "src/versions.py", "src/upgrades.py"], project_root=root)
    crossing, capture = _fn(root, "src/upgrades.py", "crossing"), _fn(root, "src/upgrades.py", "capture")
    by_record = _edge(root, crossing, _fn(root, "src/versions.py", "removed"))
    by_factory = _edge(root, crossing, _fn(root, "src/versions.py", "touching"))
    by_codec = _edge(root, capture, _fn(root, "src/versions.py", "split"))
    unrelated = _edge(root, capture, _fn(root, "src/versions.py", "removed"))
    found, report = _verdicts(root, ["src/upgrades.py"])
    # 'touches' holds two functions (removed, touching): the call does not say which runs
    assert found[by_record] == found[by_factory] == "unconfirmed_interactions"
    # 'split' holds only split: capture's fmt.codec.split() can only reach it
    assert by_codec not in found
    assert found[unrelated] == "stale_interactions"
    reasons = {i["interaction_id"]: i["reason"] for i in report["findings"]["unconfirmed_interactions"]}
    assert ".touches()" in reasons[by_record] and "one of several" in reasons[by_record]


def test_callback_parameter_of_the_target_name_is_unconfirmed(root):
    _write(root, "src/env.py", "def environment_at(d, p):\n    return p\n")
    _write(root, "src/importer.py", "def read(d, found, environment_at):\n    return [environment_at(d, p) for p in found]\n")
    reconcile_paths(["src/env.py", "src/importer.py"], project_root=root)
    edge = _edge(root, _fn(root, "src/importer.py", "read"), _fn(root, "src/env.py", "environment_at"))
    found, _ = _verdicts(root, ["src/importer.py"])
    assert found[edge] == "unconfirmed_interactions"


def test_name_in_a_string_counts_only_in_lookup_positions(root):
    _write(root, "src/target.py", "def comment(x):\n    return x\n")
    _write(root, "src/callers.py", (
        "def as_key(x):\n    return {'comment': x}\n\n\n"
        "def compared(kind):\n    return kind == 'comment'\n\n\n"
        "def by_getattr(mod, x):\n    return getattr(mod, 'comment')(x)\n\n\n"
        "def by_registry(reg, x):\n    return reg['comment'](x)\n\n\n"
        "def by_names(mod):\n    return [getattr(mod, n) for n in ('comment', 'other')]\n"))
    reconcile_paths(["src/target.py", "src/callers.py"], project_root=root)
    target = _fn(root, "src/target.py", "comment")
    edges = {n: _edge(root, _fn(root, "src/callers.py", n), target)
             for n in ("as_key", "compared", "by_getattr", "by_registry", "by_names")}
    found, _ = _verdicts(root, ["src/callers.py"])
    assert {n: found.get(e) for n, e in edges.items()} == {
        "as_key": "stale_interactions", "compared": "stale_interactions",
        "by_getattr": "unconfirmed_interactions", "by_registry": "unconfirmed_interactions",
        "by_names": "unconfirmed_interactions"}


def test_extract_lookup_strings_positions():
    from aimfp.helpers.catalog.callgraph import extract_lookup_strings
    tree = ast.parse("def f(m, r):\n    d = {'k': 'v'}\n    s = 'assigned'\n"
                     "    return getattr(m, 'g'), r.get(key='h'), r['i'], ['j', 'not a name']\n")
    assert extract_lookup_strings(tree) == {"f": frozenset({"g", "h", "i", "j"})}


# -- Finding 3: SQL functions in string literals -----------------------------

def test_sql_function_called_in_a_string_is_confirmed(root):
    _write(root, "src/queries.py", (
        "def blast(conn, dn):\n    return conn.execute('select depth from dependents(%s)', (dn,))\n\n\n"
        "def other(conn):\n    return conn.execute('select 1')\n"))
    reconcile_paths(["src/queries.py"], project_root=root)
    dependents = _track(root, "src/graph.sql", "dependents", language="sql")
    called = _edge(root, _fn(root, "src/queries.py", "blast"), dependents)
    unproven = _edge(root, _fn(root, "src/queries.py", "other"), dependents)
    found, _ = _verdicts(root, ["src/queries.py"])
    assert called not in found
    assert found[unproven] == "unconfirmed_interactions"     # never stale: Python can't rule it out


# -- Finding 4: recursion ----------------------------------------------------

def test_self_recursion_is_confirmed_and_a_dead_self_edge_is_stale(root):
    _write(root, "src/other.py", "def walk(t):\n    return t\n")
    _write(root, "src/hcl.py", (
        "def walk(t):\n    return [walk(c) for c in t]\n\n\ndef flat(t):\n    return list(t)\n"))
    reconcile_paths(["src/other.py", "src/hcl.py"], project_root=root)
    walk, flat = _fn(root, "src/hcl.py", "walk"), _fn(root, "src/hcl.py", "flat")
    recursive, dead = _edge(root, walk, walk), _edge(root, flat, flat)
    found, _ = _verdicts(root, ["src/hcl.py"])
    assert recursive not in found and found[dead] == "stale_interactions"


# -- Finding 5: module aliases, loop variables, methods, externals -----------

def test_alias_call_loop_variable_and_method_resolve_definitely(root):
    _write(root, "src/operations.py", "def upgrade(c):\n    return c\n")
    _write(root, "src/migrations.py", "def upgrade(c):\n    return c\n\n\ndef label(x):\n    return x\n")
    _write(root, "src/directory.py", "def values(b, k):\n    return b\n")
    _write(root, "src/cli.py", (
        "from . import operations as ops\nfrom .directory import values\n\n\n"
        "def cmd(r, d):\n    return ops.upgrade(r), [label for label in d], d.values()\n"))
    reconcile_paths(["src/operations.py", "src/migrations.py", "src/directory.py", "src/cli.py"], project_root=root)
    cmd = _fn(root, "src/cli.py", "cmd")
    wrong_module = _edge(root, cmd, _fn(root, "src/migrations.py", "upgrade"))
    loop_variable = _edge(root, cmd, _fn(root, "src/migrations.py", "label"))
    method_only = _edge(root, cmd, _fn(root, "src/directory.py", "values"))
    found, _ = _verdicts(root, ["src/cli.py"])
    assert found[wrong_module] == found[loop_variable] == found[method_only] == "stale_interactions"


def test_dotted_module_call_and_star_reexport_resolve(root):
    _write(root, "src/pkg/__init__.py", "from .impl import *\n")
    _write(root, "src/pkg/impl.py", "def fn(x):\n    return x\n")
    _write(root, "src/pkg/sub.py", "def helper(x):\n    return x\n")
    _write(root, "src/app.py", (
        "import pkg.sub\nfrom .pkg import fn\n\n\ndef run(x):\n    return fn(pkg.sub.helper(x))\n"))
    reconcile_paths(["src/pkg/__init__.py", "src/pkg/impl.py", "src/pkg/sub.py", "src/app.py"], project_root=root)
    edges = _sql(root, "SELECT t.name FROM interactions i JOIN functions s ON s.id = i.source_function_id "
                       "JOIN functions t ON t.id = i.target_function_id WHERE s.name = 'run'")
    assert sorted(e[0] for e in edges) == ["fn", "helper"]


def test_imported_external_name_is_not_unresolved(root):
    _write(root, "src/a.py", "def quote(s):\n    return s\n")
    _write(root, "src/b.py", "def quote(s):\n    return s\n")
    _write(root, "src/c.py", "from urllib.parse import quote\n\n\ndef file_name(s):\n    return quote(s)\n")
    reconcile_paths(["src/a.py", "src/b.py", "src/c.py"], project_root=root)
    r = audit_interactions(paths=["src/c.py"], project_root=root)
    assert "unresolved_calls" not in r.counts


def test_dotted_receiver_is_recorded_as_written():
    calls = extract_function_calls(ast.parse("def f(x):\n    return pkg.mod.fn(x), ', '.join(x)\n"))["f"]
    assert ("fn", "pkg.mod") in calls and ("join", "<expr>") in calls


# -- Finding 7: apply_audit_fixes exclude -----------------------------------

def test_exclude_holds_back_named_findings(root):
    _write(root, "src/lib.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n")
    reconcile_paths(["src/lib.py"], project_root=root)
    a = _fn(root, "src/lib.py", "a")
    kept, dropped = _edge(root, a, _fn(root, "src/lib.py", "b")), _edge(root, a, _fn(root, "src/lib.py", "c"))
    r = audit_interactions(paths=["src/lib.py"], project_root=root)
    result = apply_audit_fixes("test", report=r.report_path, apply=["stale_interactions"],
                               exclude={"stale_interactions": [kept]}, project_root=root)
    assert result.success and result.excluded_count == 1 and result.applied == {"delete_interactions": 1}
    remaining = {row[0] for row in _sql(root, "SELECT id FROM interactions")}
    assert kept in remaining and dropped not in remaining


def test_exclude_validation():
    assert exclusion_problem({"stale_interactions": [1]}, ("stale_interactions",)) is None
    assert exclusion_problem({"missing_interactions": [[1, 2]]}, ("missing_interactions",)) is None
    assert "not in apply" in exclusion_problem({"stale_functions": [1]}, ("stale_interactions",))
    assert "pair" in exclusion_problem({"missing_interactions": [1]}, ("missing_interactions",))
    report = {"findings": {"missing_interactions": [
        {"source_function_id": 1, "target_function_id": 2}, {"source_function_id": 1, "target_function_id": 3}]}}
    kept, removed = exclude_findings(report, {"missing_interactions": [[1, 2]]})
    assert removed == 1 and kept["findings"]["missing_interactions"] == [{"source_function_id": 1, "target_function_id": 3}]


def test_exclude_requires_apply(root):
    r = apply_audit_fixes("test", exclude={"stale_interactions": [1]}, project_root=root)
    assert not r.success and "apply" in r.error


# -- Finding 6: dangling mentions of DSL built-ins and keywords ---------------

def test_dangling_mentions_skip_dsl_vocabulary_and_keywords(root):
    from aimfp.helpers.audit.descriptions_audit import audit_descriptions
    _write(root, "src/hcl.py", (
        "def jsonencoded(v):\n    \"\"\"Docstrings are prose: per_file() here is not vocabulary.\"\"\"\n"
        "    return 'jsonencode(' + str(v) + ')'\n\n\n"
        "def kept(rows):\n    return [r for r in rows if r != 'reference']\n"))
    reconcile_paths(["src/hcl.py"], project_root=root)
    _sql(root, "UPDATE functions SET purpose = 'Renders jsonencode() of v' WHERE name = 'jsonencoded'")
    _sql(root, "UPDATE functions SET purpose = 'Keeps rows that do not() match reference(); was per_file()' "
               "WHERE name = 'kept'")
    r = audit_descriptions(paths=["src/hcl.py"], project_root=root)
    report, _ = _effect_load_report(root, r.report_path)
    assert [m["mentions"] for m in report["findings"]["dangling_mentions"]] == ["per_file"]


# -- Finding 8: a moved function is a repoint, not a deletion -----------------

def test_moved_function_is_stale_with_moved_to_and_apply_hands_over_edges(root):
    _write(root, "src/adapter.py", "def applies(m):\n    return m\n\n\ndef use(m):\n    return applies(m)\n")
    reconcile_paths(["src/adapter.py"], project_root=root)
    old = _fn(root, "src/adapter.py", "applies")
    # The code moves applies to render.py; adapter.py imports it from there
    _write(root, "src/render.py", "def applies(m):\n    return m\n")
    _write(root, "src/adapter.py", "from .render import applies\n\n\ndef use(m):\n    return applies(m)\n")
    reconcile_paths(["src/render.py"], project_root=root)
    new = _fn(root, "src/render.py", "applies")
    r = audit_functions(paths=["src/adapter.py"], project_root=root)
    stale = r.findings["stale_functions"]["sample"][0]
    assert stale["function_id"] == old and stale["moved_to"] == {"function_id": new, "path": "src/render.py"}
    fixed = apply_audit_fixes("applies moved to render.py", report=r.report_path, apply=["stale_functions"],
                              project_root=root)
    assert fixed.applied == {"repoint_to_successor": 1, "delete_functions": 1}
    use = _fn(root, "src/adapter.py", "use")
    assert _sql(root, "SELECT target_function_id FROM interactions WHERE source_function_id = ?", (use,)) == [(new,)]


# -- Finding 9: description review at scale ------------------------------------

def test_unbaselined_descriptions_are_triaged(root):
    from aimfp.helpers.audit.descriptions_audit import audit_descriptions
    _write(root, "src/calc.py", (
        "def add(a, b) -> int:\n    return a + b\n\n\n"
        "def total(xs) -> int:\n    return sum(xs)\n\n\n"
        "def scale(x) -> int:\n    return x * 2\n"))
    reconcile_paths(["src/calc.py"], project_root=root)
    _sql(root, "UPDATE functions SET source_hash = NULL")
    _sql(root, "UPDATE functions SET purpose = 'Adds two numbers together' WHERE name = 'add'")
    _sql(root, "UPDATE functions SET purpose = 'Sums xs after drop_nulls removes gaps' WHERE name = 'total'")
    _sql(root, "UPDATE functions SET purpose = 'Doubles', returns = '{\"type\": \"str\"}' WHERE name = 'scale'")
    r = audit_descriptions(paths=["src/calc.py"], project_root=root)
    report, _ = _effect_load_report(root, r.report_path)
    assert [i["name"] for i in report["findings"]["unbaselined_descriptions"]] == ["add"]
    review = {i["name"]: i for i in report["findings"]["unbaselined_needs_review"]}
    assert review["total"]["triage"] == ["prose names drop_nulls, found nowhere in the repo"]
    assert review["total"]["priority"] == "high" and review["scale"]["priority"] == "high"
    assert set(review["scale"]["triage"]) == {"returns recorded as str, annotated int", "purpose under three words"}
    assert review["total"]["code"] == "def total(xs) -> int:\n    return sum(xs)"
    fixed = apply_audit_fixes("triaged clean", report=r.report_path, apply=["unbaselined_descriptions"], project_root=root)
    assert fixed.applied == {"baseline_hashes": 1}


def test_triage_knows_file_names_plurals_and_ranks_repo_words_below_unknown_ones(root):
    from aimfp.helpers.audit.descriptions_audit import audit_descriptions
    _write(root, "src/infra.py", (
        '"""Module prose: mentions doc_only_word, which is not vocabulary."""\n'
        "from boto3 import client as aws_client\n\n"
        "BUCKET_KIND = 'aws_s3_bucket'\n\n\n"
        "def plan(rows) -> list:\n    return [r for r in rows]\n\n\n"
        "def emit(r) -> str:\n    return str(r)\n"))
    _write(root, "infra/main.tf", 'resource "azurerm_key_vault" "kv" {}\n')
    reconcile_paths(["src/infra.py"], project_root=root)
    _sql(root, "UPDATE functions SET source_hash = NULL")
    purposes = {
        # file-level import, string literal word and a plural of one: all pass
        "plan": "Plans aws_s3_buckets through aws_client for each row",
        # one word only in a .tf file (elsewhere), one only in a docstring, one nowhere
        "emit": "Emits azurerm_key_vault rows; see doc_only_word and ghost_helper",
    }
    for name, purpose in purposes.items():
        _sql(root, "UPDATE functions SET purpose = ? WHERE name = ?", (purpose, name))
    r = audit_descriptions(paths=["src/infra.py"], project_root=root)
    report, _ = _effect_load_report(root, r.report_path)
    assert [i["name"] for i in report["findings"]["unbaselined_descriptions"]] == ["plan"]
    (emit,) = report["findings"]["unbaselined_needs_review"]
    assert emit["triage"] == [
        "prose names ghost_helper, found nowhere in the repo",
        "prose names azurerm_key_vault, doc_only_word, which this file never uses (found elsewhere in the repo)",
    ]
    assert emit["priority"] == "high"


def test_review_lists_put_high_priority_first(root):
    from aimfp.helpers.audit.descriptions_audit import audit_descriptions
    _write(root, "src/two.py", (
        "def first(x) -> int:\n    return x\n\n\n"
        "def second(x) -> int:\n    return x\n"))
    _write(root, "notes.md", "elsewhere_word lives in the docs\n")
    reconcile_paths(["src/two.py"], project_root=root)
    _sql(root, "UPDATE functions SET source_hash = NULL")
    _sql(root, "UPDATE functions SET purpose = 'Returns x, see elsewhere_word' WHERE name = 'first'")
    _sql(root, "UPDATE functions SET purpose = 'Returns x via missing_word' WHERE name = 'second'")
    r = audit_descriptions(paths=["src/two.py"], project_root=root)
    report, _ = _effect_load_report(root, r.report_path)
    assert [(i["name"], i["priority"]) for i in report["findings"]["unbaselined_needs_review"]] == [
        ("second", "high"), ("first", "low")]


def test_reviewed_triage_and_stale_prose_baseline_by_category_with_exclude(root):
    from aimfp.helpers.audit.descriptions_audit import audit_descriptions
    _write(root, "src/rev.py", (
        "def a(x) -> int:\n    return x\n\n\n"
        "def b(x) -> int:\n    return x\n\n\n"
        "def c(x) -> int:\n    return x\n"))
    reconcile_paths(["src/rev.py"], project_root=root)
    _sql(root, "UPDATE functions SET source_hash = NULL, purpose = 'Returns x unchanged via odd_word'")
    r = audit_descriptions(paths=["src/rev.py"], project_root=root)
    assert r.counts == {"unbaselined_needs_review": 3}
    keep = _fn(root, "src/rev.py", "c")
    fixed = apply_audit_fixes("prose reviewed; c is being rewritten", report=r.report_path,
                              apply=["unbaselined_needs_review"], exclude={"unbaselined_needs_review": [keep]},
                              project_root=root)
    assert fixed.applied == {"baseline_hashes": 2}
    assert audit_descriptions(paths=["src/rev.py"], project_root=root).counts == {"unbaselined_needs_review": 1}

    # code changes after baselining: stale, then baselined by category once reviewed
    _write(root, "src/rev.py", (
        "def a(x) -> int:\n    return int(x)\n\n\n"
        "def b(x) -> int:\n    return x\n\n\n"
        "def c(x) -> int:\n    return x\n"))
    r = audit_descriptions(paths=["src/rev.py"], project_root=root)
    assert r.counts == {"stale_descriptions": 1, "unbaselined_needs_review": 1}
    fixed = apply_audit_fixes("a's prose still holds", report=r.report_path, apply=["stale_descriptions"],
                              project_root=root)
    assert fixed.applied == {"baseline_hashes": 1}
    assert audit_descriptions(paths=["src/rev.py"], project_root=root).counts == {"unbaselined_needs_review": 1}
