"""audit_descriptions (milestone 8, task 60)."""
import ast
import json
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.audit.apply_fixes import apply_audit_fixes
from aimfp.helpers.audit.descriptions_audit import (
    audit_descriptions, defined_and_imported_names, description_texts, mentioned_callables,
)
from aimfp.helpers.audit.reports import _effect_load_report
from aimfp.helpers.catalog.reconcile import reconcile_paths
from aimfp.helpers.orchestrators.entry_points import aimfp_init
from aimfp.helpers.project.functions_2 import update_function


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


def _exec(root, sql, params=()):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _id(root, name):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        return conn.execute("SELECT id FROM functions WHERE name = ?", (name,)).fetchone()[0]
    finally:
        conn.close()


CODE = '''import sqlite3
from difflib import get_close_matches


def parse(text):
    """Split text into words."""
    return text.split()


def open_db(path):
    """Open via sqlite3.connect."""
    return sqlite3.connect(path)
'''


@pytest.fixture
def project(root):
    _write(root, "src/m.py", CODE)
    reconcile_paths(["src/m.py"], project_root=root)
    return root


@pytest.mark.parametrize("text,expected", [
    ("per_file() adapts one-file parsers", ("per_file",)),
    ("uses run() then helper_fn(x)", ("run", "helper_fn")),
    ("calls os.path.join() and json.dumps()", ()),          # attribute calls
    ("COALESCE(a, b) and MAX(x)", ()),                       # SQL
    ("each file(s) and box(es)", ()),                        # plurals
    ("add(a, b) adds", ()),                                  # plain word with args, no underscore
    ("_private_helper(x) and __dunder__()", ("_private_helper", "__dunder__")),
])
def test_mentioned_callables(text, expected):
    assert mentioned_callables(text) == expected


def test_known_names_cover_defs_imports_and_called_attributes():
    tree = ast.parse(CODE + "\nclass Box:\n    pass\n")
    names = defined_and_imported_names([tree])
    assert {"parse", "open_db", "get_close_matches", "sqlite3", "connect", "split", "Box"} <= names


def test_description_texts_reads_every_prose_field():
    row = {"purpose": "p", "parameters": json.dumps([{"name": "a", "description": "da"}, {"name": "b"}]),
           "returns": json.dumps(json.dumps({"type": "int", "description": "dr"}))}
    assert description_texts(row) == (("purpose", "p"), ("parameters.a", "da"), ("returns", "dr"))


def test_fresh_project_is_clean(project):
    r = audit_descriptions(project_root=project)
    assert r.success and r.clean and r.counts == {}


def test_code_change_makes_description_stale_until_rewritten(project):
    _write(project, "src/m.py", CODE.replace("return text.split()", "return text.lower().split()"))
    r = audit_descriptions(project_root=project)
    assert r.counts == {"stale_descriptions": 1}
    assert r.findings["stale_descriptions"]["sample"][0]["name"] == "parse"
    assert r.warnings == ("stale_descriptions",) and not r.blocking
    assert update_function(_id(project, "parse"), purpose="Lowercase, then split.", project_root=project).success
    assert audit_descriptions(project_root=project).clean


def test_formatting_and_docstring_edits_are_not_drift(project):
    _write(project, "src/m.py", CODE.replace('"""Split text into words."""', '"""Tokenize."""\n\n    # comment'))
    assert audit_descriptions(project_root=project).clean


def test_unbaselined_then_baseline_from_report(project):
    _exec(project, "UPDATE functions SET source_hash = NULL")
    r = audit_descriptions(project_root=project)
    assert r.counts == {"unbaselined_descriptions": 2} and r.clean   # info only
    applied = apply_audit_fixes("reviewed", report=r.report_path, apply=["unbaselined_descriptions"],
                                project_root=project)
    assert applied.applied == {"baseline_hashes": 2}
    assert audit_descriptions(project_root=project).counts == {}


def test_dangling_mentions_in_functions_and_types(project):
    _exec(project, "UPDATE functions SET purpose = 'Wraps per_file() and parse() for get_close_matches()' "
                   "WHERE name = 'parse'")
    _exec(project, "UPDATE functions SET parameters = ? WHERE name = 'open_db'",
          (json.dumps([{"name": "path", "description": "passed to old_opener(path)"}]),))
    _exec(project, "INSERT INTO types (name, file_id, definition_json, description) "
                   "VALUES ('T', 1, '{}', 'built by make_t()')")
    r = audit_descriptions(project_root=project)
    found = {(d.get("function_id") is None, d["field"], d["mentions"])
             for d in _effect_load_report(project, r.report_path)[0]["findings"]["dangling_mentions"]}
    assert found == {(False, "purpose", "per_file"), (False, "parameters.path", "old_opener"),
                     (True, "description", "make_t")}


def test_own_parameter_names_are_not_dangling(project):
    _exec(project, "UPDATE functions SET purpose = 'Calls the given on_done() callback' WHERE name = 'parse'")
    _write(project, "src/m.py", CODE.replace("def parse(text):", "def parse(text, on_done=None):"))
    r = audit_descriptions(project_root=project)
    assert "dangling_mentions" not in r.counts
