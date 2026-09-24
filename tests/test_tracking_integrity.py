"""
Tracking data integrity (task 35; svamanas small-model feedback items 1-4).

- catalog_* never erases: omitted or blank fields keep the stored value
- add_interaction(s) / add_types_functions skip already-tracked links and report it
- finalize_file(s) refuse '.', directories and paths leaving the project
- finalize_function(s) warn (never refuse) when the name is not in the source
"""
import json
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.catalog.register import catalog_files, catalog_functions, catalog_types
from aimfp.helpers.project.files_1 import finalize_file, finalize_files, reserve_file
from aimfp.helpers.project.functions_1 import (
    finalize_function,
    finalize_functions,
    reserve_function,
)
from aimfp.helpers.project.interactions import add_interaction, add_interactions
from aimfp.helpers.project.types_2 import add_types_functions

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    os.makedirs(tmp_path / ".aimfp-project")
    conn = sqlite3.connect(tmp_path / ".aimfp-project" / "project.db")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    (tmp_path / "src").mkdir()
    yield str(tmp_path)
    clear_project_root_cache()


def _row(root, sql, params=()):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _catalog_file(root, rel, language="python"):
    r = catalog_files([{"name": os.path.basename(rel), "path": rel, "language": language}],
                      project_root=root)
    assert r.success, r.error
    return r.ids[0]


# ============================================================================
# 1. catalog never erases
# ============================================================================

class TestCatalogNoClobber:
    def test_recatalog_function_keeps_prose(self, root):
        file_id = _catalog_file(root, "src/calc.py")
        first = catalog_functions([{
            "name": "add", "file_id": file_id, "purpose": "Add two numbers",
            "parameters": [{"name": "a", "type": "int"}], "returns": {"type": "int"},
        }], project_root=root)
        assert first.success

        again = catalog_functions([{"name": "add", "file_id": file_id, "purpose": "  "}],
                                  project_root=root)
        assert again.success and again.updated_count == 1 and again.ids == first.ids
        purpose, params, returns = _row(
            root, "SELECT purpose, parameters, returns FROM functions WHERE id = ?", first.ids)
        assert purpose == "Add two numbers"
        assert json.loads(params) == [{"name": "a", "type": "int"}]
        assert json.loads(returns) == {"type": "int"}

    def test_supplied_fields_still_overwrite(self, root):
        file_id = _catalog_file(root, "src/calc.py")
        first = catalog_functions([{"name": "add", "file_id": file_id, "purpose": "old",
                                    "parameters": [{"name": "a"}]}], project_root=root)
        catalog_functions([{"name": "add", "file_id": file_id, "purpose": "new",
                            "parameters": []}], project_root=root)
        purpose, params = _row(
            root, "SELECT purpose, parameters FROM functions WHERE id = ?", first.ids)
        assert purpose == "new"
        assert json.loads(params) == []

    def test_recatalog_type_keeps_description(self, root):
        file_id = _catalog_file(root, "src/calc.py")
        first = catalog_types([{"name": "Money", "file_id": file_id,
                                "definition": {"kind": "product"}, "description": "Amount + currency"}],
                              project_root=root)
        catalog_types([{"name": "Money", "file_id": file_id, "definition": {"kind": "product", "v": 2}}],
                      project_root=root)
        definition, description = _row(
            root, "SELECT definition_json, description FROM types WHERE id = ?", first.ids)
        assert json.loads(definition) == {"kind": "product", "v": 2}
        assert description == "Amount + currency"

    def test_recatalog_file_keeps_name_and_language(self, root):
        file_id = _catalog_file(root, "src/calc.py")
        again = catalog_files([{"path": "src/calc.py"}], project_root=root)
        assert again.ids == (file_id,)
        assert _row(root, "SELECT name, language FROM files WHERE id = ?", (file_id,)) == \
            ("calc.py", "python")

    def test_new_file_without_name_uses_basename(self, root):
        r = catalog_files([{"path": "src/util.py"}], project_root=root)
        assert _row(root, "SELECT name FROM files WHERE id = ?", r.ids)[0] == "util.py"


# ============================================================================
# 2. linkers skip already-tracked rows
# ============================================================================

def _two_functions(root):
    file_id = _catalog_file(root, "src/calc.py")
    r = catalog_functions([
        {"name": "add", "file_id": file_id, "purpose": "p", "parameters": [], "returns": {}},
        {"name": "total", "file_id": file_id, "purpose": "p", "parameters": [], "returns": {}},
    ], project_root=root)
    return file_id, r.ids


class TestInteractionDedup:
    def test_rerun_batch_adds_nothing(self, root):
        _, (add_id, total_id) = _two_functions(root)
        edges = [(total_id, add_id, "call", "total sums with add")]
        first = add_interactions(edges, project_root=root)
        assert first.success and first.added_count == 1 and first.skipped_count == 0

        second = add_interactions(edges, project_root=root)
        assert second.success and second.added_count == 0 and second.skipped_count == 1
        assert second.ids == first.ids
        assert _row(root, "SELECT COUNT(*) FROM interactions")[0] == 1

    def test_repeat_within_batch_and_type_distinguishes(self, root):
        _, (add_id, total_id) = _two_functions(root)
        r = add_interactions([
            (total_id, add_id, "call", None),
            (total_id, add_id, "call", None),
            (total_id, add_id, "compose", None),
        ], project_root=root)
        assert r.added_count == 2 and r.skipped_count == 1
        assert r.ids[0] == r.ids[1] != r.ids[2]
        assert _row(root, "SELECT COUNT(*) FROM interactions")[0] == 2

    def test_single_reports_already_existed(self, root):
        _two_functions(root)
        first = add_interaction("total", "add", "call", project_root=root)
        second = add_interaction("total", "add", "call", project_root=root)
        assert first.success and not first.already_existed
        assert second.success and second.already_existed and second.id == first.id


class TestTypesFunctionsDedup:
    def test_duplicate_is_skipped_not_fatal(self, root):
        file_id, (add_id, total_id) = _two_functions(root)
        t = catalog_types([{"name": "Money", "file_id": file_id, "definition": {"k": 1},
                            "description": "d"}], project_root=root)
        type_id = t.ids[0]
        first = add_types_functions([(type_id, add_id, "operator")], project_root=root)
        assert first.success and first.added_count == 1

        second = add_types_functions([(type_id, add_id, "operator"),
                                      (type_id, total_id, "accessor")], project_root=root)
        assert second.success, second.error
        assert second.added_count == 1 and second.skipped_count == 1
        assert second.ids[0] == first.ids[0]


# ============================================================================
# 3. finalize_file path checks
# ============================================================================

class TestFinalizeFilePath:
    def _reserved(self, root):
        r = reserve_file(name="calc", path="src/calc.py", language="python", project_root=root)
        assert r.success
        return r.id

    @pytest.mark.parametrize("bad,fragment", [
        (".", "does not name a file"),
        ("", "does not name a file"),
        ("src/", "does not name a file"),
        ("src", "is a directory"),
        ("../elsewhere.py", "leaves the project"),
        ("src/missing.py", "does not exist"),
    ])
    def test_refuses(self, root, bad, fragment):
        file_id = self._reserved(root)
        r = finalize_file(file_id=file_id, name="calc.py", path=bad, language="python",
                          skip_id_naming=True, project_root=root)
        assert not r.success
        assert fragment in r.error

    def test_accepts_regular_file(self, root):
        file_id = self._reserved(root)
        _write(root, "src/calc.py", "x = 1\n")
        r = finalize_file(file_id=file_id, name="calc.py", path="src/calc.py",
                          language="python", skip_id_naming=True, project_root=root)
        assert r.success, r.error

    def test_batch_refuses_directory(self, root):
        file_id = self._reserved(root)
        r = finalize_files([{"file_id": file_id, "name": "calc.py", "path": ".",
                             "language": "python", "skip_id_naming": True}], project_root=root)
        assert not r.success
        assert f"File {file_id}" in r.error and "does not name a file" in r.error


# ============================================================================
# 4. finalize_function name check
# ============================================================================

class TestFinalizeFunctionName:
    def _setup(self, root, rel="src/hello.py", source="def print_hi():\n    return 'hi'\n",
               language="python"):
        _write(root, rel, source)
        file_id = _catalog_file(root, rel, language)
        r = reserve_function(name="print_hi", file_id=file_id, purpose="Say hi",
                             parameters=[], returns={"type": "str"}, project_root=root)
        assert r.success, r.error
        return file_id, r.id

    def test_mismatch_warns_with_did_you_mean(self, root):
        file_id, fn_id = self._setup(root)
        r = finalize_function(function_id=fn_id, name=f"print_hi_id_{fn_id}", file_id=file_id,
                              project_root=root)
        assert r.success, r.error
        assert len(r.warnings) == 1
        assert "Did you mean 'print_hi'" in r.warnings[0]
        assert "update_function" in r.warnings[0]

    def test_match_has_no_warning(self, root):
        file_id, fn_id = self._setup(root)
        r = finalize_function(function_id=fn_id, name="print_hi", file_id=file_id,
                              skip_id_naming=True, project_root=root)
        assert r.success and r.warnings == ()

    def test_unparsed_language_is_skipped(self, root):
        file_id, fn_id = self._setup(root, rel="src/hello.js",
                                     source="const printHi = () => 'hi';\n",
                                     language="javascript")
        r = finalize_function(function_id=fn_id, name="print_hi", file_id=file_id,
                              skip_id_naming=True, project_root=root)
        assert r.success and r.warnings == ()

    def test_syntax_error_is_skipped(self, root):
        file_id, fn_id = self._setup(root, source="def print_hi(:\n")
        r = finalize_function(function_id=fn_id, name="nope", file_id=file_id,
                              skip_id_naming=True, project_root=root)
        assert r.success and r.warnings == ()

    def test_batch_warns_per_name(self, root):
        file_id, fn_id = self._setup(root)
        r2 = reserve_function(name="shout", file_id=file_id, purpose="p", parameters=[],
                              returns={}, project_root=root)
        r = finalize_functions([
            {"function_id": fn_id, "name": "print_hi", "file_id": file_id, "skip_id_naming": True},
            {"function_id": r2.id, "name": "shout", "file_id": file_id, "skip_id_naming": True},
        ], project_root=root)
        assert r.success, r.error
        assert len(r.warnings) == 1 and "'shout'" in r.warnings[0]
