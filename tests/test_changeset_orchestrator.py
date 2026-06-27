"""
Tests for the InterCommAIMFP merge-orchestrator addon (helpers/changeset/):
changeset_id handle, merge_worker_branch[es], summarize_state_changeset,
verify_fanout_ready, plan_disjoint_partitions, get_merge_history.

See docs/intercommaimfptools/MERGE-ORCHESTRATOR-AND-BRIDGES.md.
"""

import os
import shutil
import sqlite3
import subprocess
import tempfile

import pytest

from aimfp.helpers.utils import set_project_root, clear_project_root_cache
from aimfp.helpers.project import tasks as T
from aimfp.helpers.changeset._common import (
    changeset_id_for, summarize_changeset, intercomm_present,
    INTERCOMM_DIR_NAME, INTERCOMM_DB_NAME, _changeset_path,
)
from aimfp.helpers.changeset.backfill import backfill_semantic_keys
from aimfp.helpers.changeset.export import export_state_changeset
from aimfp.helpers.changeset.apply import apply_state_changeset
from aimfp.helpers.changeset.summarize import summarize_state_changeset
from aimfp.helpers.changeset.preflight import verify_fanout_ready
from aimfp.helpers.changeset.partition import plan_disjoint_partitions
from aimfp.helpers.changeset.history import get_merge_history
from aimfp.helpers.changeset.merge import merge_worker_branch, merge_worker_branches

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql")


def _schema():
    with open(SCHEMA_PATH) as f:
        return f.read()


def _git(root, *args):
    return subprocess.run(["git", "-C", root, *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _main_branch(root):
    return "master" if "master" in _git(root, "branch") else "main"


@pytest.fixture
def repo():
    root = tempfile.mkdtemp(prefix="aimfp_orch_test_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    db = os.path.join(root, ".aimfp-project", "project.db")
    conn = sqlite3.connect(db)
    conn.executescript(_schema())
    conn.close()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "tester")
    clear_project_root_cache()
    set_project_root(root)
    yield root, db
    clear_project_root_cache()
    shutil.rmtree(root, ignore_errors=True)


def _conn(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def _commit(root, msg):
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)
    return _git(root, "rev-parse", "HEAD")


def _seed_base(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO completion_path (name, order_index) VALUES ('core',1)")
    conn.execute("INSERT INTO files (path, name, language) VALUES ('src/a.py','a.py','Python')")
    conn.execute("INSERT INTO functions (file_id, name, purpose) VALUES (1,'foo','does foo')")
    conn.commit()
    cp = conn.execute("SELECT id FROM completion_path").fetchone()[0]
    m = T._insert_milestone(conn, cp, "Core Dev", "pending", None)
    T._insert_task(conn, m, "build foo", "pending", "high", None, None)
    conn.close()
    return m


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_changeset_id_is_stable_and_branch_distinct():
    a = changeset_id_for("base123", "aimfp-alice-001")
    assert a == changeset_id_for("base123", "aimfp-alice-001")  # stable
    assert a != changeset_id_for("base123", "aimfp-bob-001")    # branch-distinct
    assert a.startswith("cs-aimfp-alice-001-")


def test_summarize_changeset_counts():
    cs = {
        "entities": [
            {"kind": "files", "op": "add", "semantic_key": {"path": "src/x.py"}},
            {"kind": "functions", "op": "add", "semantic_key": {"file": "src/x.py", "name": "f"},
             "attributes": {"file": "src/x.py"}},
            {"kind": "functions", "op": "modify", "semantic_key": {"file": "src/x.py", "name": "g"}},
        ],
        "references": [{"kind": "interaction", "op": "add"}],
        "warnings": ["w"],
    }
    s = summarize_changeset(cs)
    assert s["entities"]["functions"] == {"add": 1, "modify": 1, "delete": 0}
    assert s["references"]["interaction"] == {"add": 1, "remove": 0}
    assert "src/x.py" in s["touched_files"]
    assert s["totals"] == {"entities": 3, "references": 1, "warnings": 1}


def test_intercomm_present(repo):
    root, _db = repo
    assert intercomm_present(root) is False
    os.makedirs(os.path.join(root, INTERCOMM_DIR_NAME))
    open(os.path.join(root, INTERCOMM_DIR_NAME, INTERCOMM_DB_NAME), "w").close()
    assert intercomm_present(root) is True


# ---------------------------------------------------------------------------
# changeset_id handle round-trip (§2.1)
# ---------------------------------------------------------------------------

def test_export_returns_handle_and_apply_by_id(repo):
    root, db = repo
    _seed_base(db)
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("INSERT INTO functions (file_id,name,purpose) VALUES (1,'bar','bar')")
    c.commit(); c.close()
    _commit(root, "work")

    ex = export_state_changeset(base, "work", worker_id="w1")
    assert ex.success
    cid = ex.data["changeset_id"]
    assert cid and os.path.exists(_changeset_path(root, cid))
    assert ex.data["summary"]["totals"]["entities"] >= 1

    _git(root, "checkout", "-q", _main_branch(root))

    # apply by handle (no inline object) merges the function add
    res = apply_state_changeset(changeset_id=cid)
    assert res.success, res.error
    names = sorted(r[0] for r in _conn(db).execute("SELECT name FROM functions"))
    assert "bar" in names


def test_apply_requires_changeset_or_id(repo):
    res = apply_state_changeset()
    assert not res.success and "changeset" in res.error

    res2 = apply_state_changeset(changeset_id="cs-does-not-exist-00000000")
    assert not res2.success and "not found" in res2.error


# ---------------------------------------------------------------------------
# summarize_state_changeset (§5.3)
# ---------------------------------------------------------------------------

def test_summarize_by_id_and_by_branch(repo):
    root, db = repo
    _seed_base(db)
    base = _commit(root, "base")
    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("INSERT INTO functions (file_id,name,purpose) VALUES (1,'bar','bar')")
    c.commit(); c.close()
    _commit(root, "work")

    ex = export_state_changeset(base, "work")
    cid = ex.data["changeset_id"]

    by_id = summarize_state_changeset(changeset_id=cid)
    assert by_id.success and "entities" in by_id.data["summary"]

    by_branch = summarize_state_changeset(branch="work", base_commit=base)
    assert by_branch.success
    assert by_branch.data["summary"]["totals"] == by_id.data["summary"]["totals"]

    assert not summarize_state_changeset().success  # nothing to identify


# ---------------------------------------------------------------------------
# verify_fanout_ready (§5.2)
# ---------------------------------------------------------------------------

def test_verify_fanout_ready_blockers_then_ready(repo):
    root, db = repo
    _seed_base(db)
    # functions lack entity_key -> not ready before backfill
    _commit(root, "base")
    before = verify_fanout_ready()
    assert before.success and before.data["ready"] is False
    assert before.data["missing_keys"]  # functions/types need entity_key

    backfill_semantic_keys()
    _commit(root, "backfilled")
    after = verify_fanout_ready()
    assert after.success and after.data["ready"] is True, after.data["blockers"]

    # uncommitted project.db change -> blocked again
    c = _conn(db); c.execute("INSERT INTO functions (file_id,name) VALUES (1,'zzz')"); c.commit(); c.close()
    dirty = verify_fanout_ready()
    assert dirty.data["ready"] is False
    assert any("uncommitted" in b for b in dirty.data["blockers"])


# ---------------------------------------------------------------------------
# plan_disjoint_partitions (§5.1)
# ---------------------------------------------------------------------------

def test_plan_partitions_disjoint_and_coupled(repo):
    root, db = repo
    c = _conn(db)
    c.execute("INSERT INTO files (path,name,language) VALUES ('src/a.py','a.py','Python')")
    c.execute("INSERT INTO files (path,name,language) VALUES ('src/b.py','b.py','Python')")
    c.execute("INSERT INTO functions (file_id,name) VALUES (1,'fa')")
    c.execute("INSERT INTO functions (file_id,name) VALUES (2,'fb')")
    c.commit(); c.close()

    disjoint = plan_disjoint_partitions(n_workers=2)
    assert disjoint.success
    assert disjoint.data["component_count"] == 2
    assert len(disjoint.data["partitions"]) == 2

    # couple the two files via an interaction -> single component
    c = _conn(db)
    fa = c.execute("SELECT id FROM functions WHERE name='fa'").fetchone()[0]
    fb = c.execute("SELECT id FROM functions WHERE name='fb'").fetchone()[0]
    c.execute("INSERT INTO interactions (source_function_id,target_function_id,interaction_type) "
              "VALUES (?,?,'call')", (fa, fb))
    c.commit(); c.close()

    coupled = plan_disjoint_partitions(n_workers=2)
    assert coupled.data["component_count"] == 1
    assert coupled.data["note"]  # warns the region is one component


# ---------------------------------------------------------------------------
# merge_worker_branch end-to-end + get_merge_history (§2.2, §5.4)
# ---------------------------------------------------------------------------

def test_merge_worker_branch_end_to_end_and_history(repo):
    root, db = repo
    _seed_base(db)
    # a real source file on main so the branch can diverge in source too
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "a.py"), "w") as f:
        f.write("def foo():\n    return 1\n")
    _commit(root, "base")

    _git(root, "checkout", "-qb", "aimfp-alice-001")
    with open(os.path.join(root, "src", "feature.py"), "w") as f:
        f.write("def bar():\n    return 2\n")
    c = _conn(db)
    c.execute("INSERT INTO functions (file_id,name,purpose) VALUES (1,'bar','bar')")
    c.commit(); c.close()
    _commit(root, "work")

    _git(root, "checkout", "-q", _main_branch(root))

    # no history yet
    assert get_merge_history(branch="aimfp-alice-001").data["already_merged"] is False

    res = merge_worker_branch("aimfp-alice-001", worker_id="alice")
    assert res.success, res.error
    assert res.data["status"] == "merged"
    assert res.data["changeset_id"]
    assert "src/feature.py" in res.data["source_merge"]["merged_paths"]
    names = sorted(r[0] for r in _conn(db).execute("SELECT name FROM functions"))
    assert "bar" in names  # DB state applied

    hist = get_merge_history(branch="aimfp-alice-001")
    assert hist.data["already_merged"] is True
    assert hist.data["merges"] and hist.data["merges"][0]["source_branch"] == "aimfp-alice-001"


def test_supportive_context_intercomm_note_is_presence_gated(repo):
    root, _db = repo
    from aimfp.helpers.shared.supportive_context import get_supportive_context

    # absent -> no InterComm note
    before = get_supportive_context("core")
    assert before.success and "INTERCOMMAIMFP DETECTED" not in before.data["content"]

    # present -> note appended to core, referencing the real protocol tool
    os.makedirs(os.path.join(root, INTERCOMM_DIR_NAME))
    open(os.path.join(root, INTERCOMM_DIR_NAME, INTERCOMM_DB_NAME), "w").close()
    after = get_supportive_context("core")
    assert "INTERCOMMAIMFP DETECTED" in after.data["content"]
    assert "intercomm_get_protocol" in after.data["content"]
    assert "merge_worker_branch" in after.data["content"]

    # other variants are NOT decorated (note is core-only)
    coding = get_supportive_context("coding")
    assert "INTERCOMMAIMFP DETECTED" not in coding.data["content"]


def test_merge_worker_branch_status_is_conflict_enum(repo):
    """A concurrent-modify conflict yields status='conflict' (a real InterComm enum value)."""
    root, db = repo
    m = _seed_base(db)
    _commit(root, "base")

    _git(root, "checkout", "-qb", "aimfp-bob-001")
    c = _conn(db); c.execute("UPDATE milestones SET name='Branch Name' WHERE id=?", (m,)); c.commit(); c.close()
    _commit(root, "bob")

    _git(root, "checkout", "-q", _main_branch(root))
    c = _conn(db); c.execute("UPDATE milestones SET name='Main Name' WHERE id=?", (m,)); c.commit(); c.close()
    _commit(root, "main drift")

    res = merge_worker_branch("aimfp-bob-001", worker_id="bob")
    assert res.success, res.error
    assert res.data["conflicts"]
    assert res.data["status"] == "conflict"


def test_merge_worker_branches_batch(repo):
    root, db = repo
    _seed_base(db)
    _commit(root, "base")

    _git(root, "checkout", "-qb", "aimfp-alice-001")
    c = _conn(db); c.execute("INSERT INTO functions (file_id,name) VALUES (1,'bar')"); c.commit(); c.close()
    _commit(root, "alice")

    _git(root, "checkout", "-q", _main_branch(root))

    res = merge_worker_branches(["aimfp-alice-001"], on_conflict="continue")
    assert res.success, res.error
    assert res.data["integrated"] == ["aimfp-alice-001"]
    assert res.data["results"][0]["success"]
