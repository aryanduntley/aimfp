"""reconcile_paths (task 41; svamanas item 11)."""
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.catalog.callgraph import fold_untracked_helpers
from aimfp.helpers.catalog.reconcile import (
    normalize_reconcile_path, pair_folder_rows, pair_renames, reconcile_paths,
)
from aimfp.helpers.orchestrators.entry_points import aimfp_init

CALC = '''def add(a, b):
    """Add two numbers."""
    return a + b


def total(values):
    """Sum a list."""
    result = 0
    for v in values:
        result = add(result, v)
    return result
'''


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


def _names(entries):
    return sorted(e["name"] for e in entries)


def test_new_file_is_cataloged_with_edges(root):
    _write(root, "src/calc.py", CALC)
    r = reconcile_paths(["src/calc.py"], project_root=root)
    assert r.success, r.error
    assert [f["path"] for f in r.files_cataloged] == ["src/calc.py"]
    assert _names(r.functions_added) == ["add", "total"]
    assert r.interactions_added == 1
    assert _q(root, "SELECT purpose FROM functions WHERE name = 'add'") == [("Add two numbers.",)]


def test_second_run_changes_nothing(root):
    _write(root, "src/calc.py", CALC)
    reconcile_paths(["src/calc.py"], project_root=root)
    r = reconcile_paths(["src/calc.py"], project_root=root)
    assert r.files_cataloged == () and r.functions_added == ()
    assert r.interactions_added == 0 and r.interactions_skipped == 1
    assert _q(root, "SELECT COUNT(*) FROM interactions") == [(1,)]
    assert _q(root, "SELECT COUNT(*) FROM files WHERE path = 'src/calc.py'") == [(1,)]


def test_prose_is_never_overwritten_and_removals_reported(root):
    _write(root, "src/calc.py", CALC)
    reconcile_paths(["src/calc.py"], project_root=root)
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    conn.execute("UPDATE functions SET purpose = 'curated prose' WHERE name = 'add'")
    conn.commit()
    conn.close()

    _write(root, "src/calc.py", CALC.split("\n\n\ndef total")[0] + "\n\n\ndef mean(v):\n    return total(v) / len(v)\n")
    r = reconcile_paths(["src/calc.py"], project_root=root)
    assert _names(r.functions_added) == ["mean"]
    assert _names(r.functions_missing_in_source) == ["total"]
    assert _q(root, "SELECT purpose FROM functions WHERE name = 'add'") == [("curated prose",)]
    assert _q(root, "SELECT COUNT(*) FROM functions WHERE name = 'total'") == [(1,)]  # not deleted


def test_rename_keeps_the_row(root):
    _write(root, "src/calc.py", CALC)
    first = reconcile_paths(["src/calc.py"], project_root=root)
    file_id = first.files_cataloged[0]["id"]
    os.makedirs(os.path.join(root, "src", "math"))
    os.rename(os.path.join(root, "src", "calc.py"), os.path.join(root, "src", "math", "calc.py"))

    r = reconcile_paths(["src/calc.py", "src/math/calc.py"], project_root=root)
    assert r.files_renamed == ({"id": file_id, "from": "src/calc.py", "to": "src/math/calc.py"},)
    assert r.files_cataloged == () and r.functions_added == () and r.files_missing == ()
    assert _q(root, "SELECT path FROM files WHERE id = ?", (file_id,)) == [("src/math/calc.py",)]
    assert _q(root, "SELECT COUNT(*) FROM functions WHERE file_id = ?", (file_id,)) == [(2,)]


def test_deleted_file_is_reported_not_removed(root):
    _write(root, "src/calc.py", CALC)
    reconcile_paths(["src/calc.py"], project_root=root)
    os.remove(os.path.join(root, "src", "calc.py"))
    r = reconcile_paths(["src/calc.py"], project_root=root)
    assert [f["path"] for f in r.files_missing] == ["src/calc.py"]
    assert _q(root, "SELECT COUNT(*) FROM files WHERE path = 'src/calc.py'") == [(1,)]


def test_dry_run_writes_nothing(root):
    _write(root, "src/calc.py", CALC)
    r = reconcile_paths(["src/calc.py"], dry_run=True, project_root=root)
    assert r.success and r.dry_run
    assert _names(r.functions_added) == ["add", "total"] and r.interactions_added == 1
    assert _q(root, "SELECT COUNT(*) FROM files WHERE path = 'src/calc.py'") == [(0,)]
    assert _q(root, "SELECT COUNT(*) FROM interactions") == [(0,)]


def test_skips_with_reasons(root):
    _write(root, "README.md", "# hi\n")
    r = reconcile_paths(["../x.py", "/abs/y.py", ".aimfp-project/project.db", "README.md",
                         "src/nope.py"], project_root=root)
    reasons = {s["path"]: s["reason"] for s in r.skipped}
    assert "leaves the project" in reasons["../x.py"]
    assert "absolute" in reasons["/abs/y.py"]
    assert reasons[".aimfp-project/project.db"] == "excluded by watchdog exclusions"
    assert reasons["README.md"] == "unsupported language"
    assert reasons["src/nope.py"] == "not tracked and not on disk"
    assert r.files_cataloged == ()


def test_no_usable_paths_fails(root):
    r = reconcile_paths(["", "../a"], project_root=root)
    assert not r.success and len(r.skipped) == 2


def test_names_only_language_never_reports_missing(root):
    _write(root, "src/app.js", "function greet() { return 1 }\n")
    first = reconcile_paths(["src/app.js"], project_root=root)
    assert _names(first.functions_added) == ["greet"]
    _write(root, "src/app.js", "const greet = () => 1\n")
    r = reconcile_paths(["src/app.js"], project_root=root)
    assert r.functions_missing_in_source == ()


def test_pure_helpers():
    assert normalize_reconcile_path("./src//a.py") == ("src/a.py", None)
    assert normalize_reconcile_path("a\\b.py") == ("a/b.py", None)
    assert pair_renames(("a/x.py", "a/y.py"), ("b/x.py", "c/y.py", "d/y.py")) == (("a/x.py", "b/x.py"),)


# ============================================================================
# Task 42: stale call edges + explicit renames
# ============================================================================

def test_removed_call_is_reported_stale_not_deleted(root):
    _write(root, "src/calc.py", CALC)
    reconcile_paths(["src/calc.py"], project_root=root)
    _write(root, "src/calc.py", CALC.replace("result = add(result, v)", "result = result + v"))
    r = reconcile_paths(["src/calc.py"], project_root=root)
    assert [(s["source_name"], s["target_name"]) for s in r.stale_interactions] == [("total", "add")]
    assert _q(root, "SELECT COUNT(*) FROM interactions") == [(1,)]


def test_unresolvable_or_callback_reference_is_not_stale(root):
    _write(root, "src/calc.py", CALC)
    reconcile_paths(["src/calc.py"], project_root=root)
    # add is now only passed as a callback, not called
    _write(root, "src/calc.py", CALC.replace(
        "    result = 0\n    for v in values:\n        result = add(result, v)\n    return result",
        "    from functools import reduce\n    return reduce(add, values, 0)"))
    assert reconcile_paths(["src/calc.py"], project_root=root).stale_interactions == ()


def test_import_alias_counts_as_reference(root):
    _write(root, "src/calc.py", CALC)
    _write(root, "src/use.py", "from src.calc import add\n\n\ndef twice(x):\n    return add(x, x)\n")
    reconcile_paths(["src/calc.py", "src/use.py"], project_root=root)
    assert _q(root, "SELECT COUNT(*) FROM interactions i JOIN functions s ON s.id = i.source_function_id "
                    "WHERE s.name = 'twice'") == [(1,)]  # the edge under test exists
    before = _q(root, "SELECT COUNT(*) FROM interactions")[0][0]
    _write(root, "src/use.py", "from src.calc import add as plus\n\n\ndef twice(x):\n    return plus(x, x)\n")
    r = reconcile_paths(["src/use.py"], project_root=root)
    assert r.stale_interactions == ()
    assert _q(root, "SELECT COUNT(*) FROM interactions")[0][0] == before


def test_non_call_edges_never_flagged(root):
    _write(root, "src/calc.py", CALC)
    reconcile_paths(["src/calc.py"], project_root=root)
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    ids = dict(conn.execute("SELECT name, id FROM functions").fetchall())
    conn.execute("INSERT INTO interactions (source_function_id, target_function_id, interaction_type) "
                 "VALUES (?, ?, 'compose')", (ids["add"], ids["total"]))
    conn.commit()
    conn.close()
    assert reconcile_paths(["src/calc.py"], project_root=root).stale_interactions == ()


def test_explicit_rename_with_new_name(root):
    _write(root, "src/calc.py", CALC)
    file_id = reconcile_paths(["src/calc.py"], project_root=root).files_cataloged[0]["id"]
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    conn.execute("UPDATE functions SET purpose = 'curated' WHERE name = 'add'")
    conn.commit()
    conn.close()
    os.rename(os.path.join(root, "src", "calc.py"), os.path.join(root, "src", "arith.py"))

    r = reconcile_paths([], renames=[{"from": "src/calc.py", "to": "src/arith.py"}], project_root=root)
    assert r.success, r.error
    assert r.files_renamed == ({"id": file_id, "from": "src/calc.py", "to": "src/arith.py"},)
    assert r.files_cataloged == () and r.files_missing == () and r.functions_added == ()
    assert _q(root, "SELECT path FROM files WHERE id = ?", (file_id,)) == [("src/arith.py",)]
    assert _q(root, "SELECT purpose FROM functions WHERE name = 'add'") == [("curated",)]


def test_without_renames_a_renamed_move_is_missing_plus_new(root):
    _write(root, "src/calc.py", CALC)
    reconcile_paths(["src/calc.py"], project_root=root)
    os.rename(os.path.join(root, "src", "calc.py"), os.path.join(root, "src", "arith.py"))
    r = reconcile_paths(["src/calc.py", "src/arith.py"], dry_run=True, project_root=root)
    assert [f["path"] for f in r.files_missing] == ["src/calc.py"]
    assert [f["path"] for f in r.files_cataloged] == ["src/arith.py"]


def test_explicit_rename_refusals(root):
    _write(root, "src/calc.py", CALC)
    _write(root, "src/other.py", "def f():\n    return 1\n")
    reconcile_paths(["src/calc.py", "src/other.py"], project_root=root)
    r = reconcile_paths(["src/calc.py"], renames=[
        {"from": "src/calc.py", "to": "src/other.py"},     # from still on disk
        {"from": "src/ghost.py", "to": "src/new.py"},      # from not tracked
        {"from": "src/calc.py"},                           # malformed
    ], project_root=root)
    reasons = {s["path"]: s["reason"] for s in r.skipped}
    assert reasons["src/calc.py -> src/other.py"] == "'from' still exists on disk"
    assert reasons["src/ghost.py -> src/new.py"] == "'from' is not tracked"
    assert reasons["src/calc.py -> None"] == "rename needs usable 'from' and 'to' paths"
    assert r.files_renamed == ()


def _legacy_row(root, path, name, language="python"):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        cur = conn.execute(
            "INSERT INTO files (name, path, language) VALUES (?, ?, ?)", (name, path, language))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.mark.parametrize("legacy_path, legacy_name", [(".", "calc.py"), ("src", "calc"), ("", "calc.py")])
def test_folder_path_row_is_adopted_not_duplicated(root, legacy_path, legacy_name):
    row = _legacy_row(root, legacy_path, legacy_name)
    _write(root, "src/calc.py", CALC)
    r = reconcile_paths(["src/calc.py"], project_root=root)
    assert r.success, r.error
    assert r.files_cataloged == ()
    assert r.files_renamed == ({"id": row, "from": legacy_path, "to": "src/calc.py"},)
    assert _q(root, "SELECT id, name, path FROM files WHERE path = 'src/calc.py'") == [
        (row, "calc.py", "src/calc.py")]
    assert _names(r.functions_added) == ["add", "total"]


def test_folder_row_left_alone_when_ambiguous_or_mismatched(root):
    _legacy_row(root, ".", "calc.py")
    _legacy_row(root, "src", "calc.py")          # two folder rows want one file
    os.makedirs(os.path.join(root, "lib"))
    _legacy_row(root, "lib", "util.py", "javascript")  # language disagrees
    _write(root, "src/calc.py", CALC)
    _write(root, "src/util.py", "def f():\n    return 1\n")
    r = reconcile_paths(["src/calc.py", "src/util.py"], project_root=root)
    assert r.files_renamed == ()
    assert sorted(f["path"] for f in r.files_cataloged) == ["src/calc.py", "src/util.py"]


def test_deleted_file_row_is_not_adopted_across_calls(root):
    _write(root, "src/old/calc.py", CALC)
    reconcile_paths(["src/old/calc.py"], project_root=root)
    os.remove(os.path.join(root, "src/old/calc.py"))
    _write(root, "src/new/calc.py", CALC)
    r = reconcile_paths(["src/new/calc.py"], project_root=root)
    assert r.files_renamed == ()
    assert [f["path"] for f in r.files_cataloged] == ["src/new/calc.py"]


def test_folder_row_adoption_dry_run_rolls_back(root):
    _legacy_row(root, ".", "calc.py")
    _write(root, "src/calc.py", CALC)
    r = reconcile_paths(["src/calc.py"], dry_run=True, project_root=root)
    assert len(r.files_renamed) == 1
    assert _q(root, "SELECT path FROM files WHERE name = 'calc.py'") == [(".",)]


def test_pair_folder_rows_pure():
    rows = (
        {"id": 1, "name": "a.py", "path": ".", "language": None},
        {"id": 2, "name": "b", "path": "pkg", "language": "python"},
        {"id": 3, "name": "c.py", "path": "gone/c.py", "language": "python"},
    )
    is_folder = lambda p: p in (".", "pkg")  # noqa: E731
    pairs = pair_folder_rows(
        (("x/a.py", "python"), ("x/b.py", "python"), ("x/c.py", "python"), ("y/a.py", "python")),
        rows, is_folder,
    )
    assert pairs == ((2, "pkg", "x/b.py"),)   # a.py claimed twice, c.py's row is not a folder
    assert pair_folder_rows((("x/a.py", None),), rows, is_folder) == ()


# ============================================================================
# Task 44: calls made through untracked helpers are not stale
# ============================================================================

RUNNER = '''from src.calc import add


def run(values):
    return _step(values)


def _step(values):
    return _inner(values)


def _inner(values):
    return add(values[0], values[1])
'''


def _edge(root, source, target):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    ids = dict(conn.execute("SELECT name, id FROM functions").fetchall())
    conn.execute("INSERT INTO interactions (source_function_id, target_function_id, interaction_type) "
                 "VALUES (?, ?, 'call')", (ids[source], ids[target]))
    conn.commit()
    conn.close()


def test_call_through_untracked_helpers_is_not_stale(root):
    _write(root, "src/calc.py", CALC)
    _write(root, "src/runner.py", RUNNER)
    reconcile_paths(["src/calc.py", "src/runner.py"], include_private=False, project_root=root)
    _edge(root, "run", "add")   # recorded on run, made inside _inner
    assert reconcile_paths(["src/runner.py"], include_private=False,
                           project_root=root).stale_interactions == ()
    _write(root, "src/runner.py", RUNNER.replace("add(values[0], values[1])", "values[0] + values[1]"))
    r = reconcile_paths(["src/runner.py"], include_private=False, project_root=root)
    assert [(s["source_name"], s["target_name"]) for s in r.stale_interactions] == [("run", "add")]


def test_tracked_helper_keeps_its_calls_to_itself(root):
    _write(root, "src/calc.py", CALC)
    _write(root, "src/runner.py", RUNNER)
    reconcile_paths(["src/calc.py", "src/runner.py"], project_root=root)  # privates tracked
    _edge(root, "run", "add")
    r = reconcile_paths(["src/runner.py"], project_root=root)
    assert [(s["source_name"], s["target_name"]) for s in r.stale_interactions] == [("run", "add")]


def test_fold_untracked_helpers_pure():
    referenced = {
        "a": frozenset({"_h", "x"}),
        "_h": frozenset({"_g", "y", "a"}),   # cycle back to a
        "_g": frozenset({"z"}),
        "b": frozenset({"t"}),
        "t": frozenset({"w"}),
    }
    folded = fold_untracked_helpers(referenced, frozenset({"a", "b", "t"}))
    assert folded["a"] == frozenset({"_h", "x", "_g", "y", "a", "z"})
    assert folded["b"] == frozenset({"t"})   # t is tracked: its calls stay its own
    assert folded["_g"] == frozenset({"z"})


def test_tracked_private_not_missing_when_privates_not_cataloged(root):
    _write(root, "src/runner.py", RUNNER)
    reconcile_paths(["src/runner.py"], project_root=root)   # privates tracked
    r = reconcile_paths(["src/runner.py"], include_private=False, project_root=root)
    assert r.functions_missing_in_source == ()
    _write(root, "src/runner.py", RUNNER.replace("def _inner", "def _renamed"))
    r = reconcile_paths(["src/runner.py"], include_private=False, project_root=root)
    assert _names(r.functions_missing_in_source) == ["_inner"]
    assert r.functions_added == ()   # _renamed not cataloged: privates off
