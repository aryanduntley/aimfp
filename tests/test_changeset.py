"""
Tests for the semantic changeset tools (helpers/changeset/) — Stage 1.

Covers slug backfill and export_state_changeset (pure-read, integer-free, semantic-key
diff between two committed git states).
"""

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile

import pytest

from aimfp.helpers.utils import set_project_root, clear_project_root_cache
from aimfp.helpers.project import tasks as T
from aimfp.helpers.changeset._common import _effect_mint_missing_slugs
from aimfp.helpers.changeset.export import export_state_changeset
from aimfp.helpers.changeset.apply import apply_state_changeset
from aimfp.helpers.changeset.conflicts import detect_state_conflicts

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)


def _schema() -> str:
    with open(SCHEMA_PATH) as f:
        return f.read()


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo():
    """A temp git repo with .aimfp-project/project.db on the new schema."""
    root = tempfile.mkdtemp(prefix="aimfp_cs_test_")
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


def _commit(root, msg):
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)
    return _git(root, "rev-parse", "HEAD")


def test_backfill_mints_unique_idempotent():
    root = tempfile.mkdtemp(prefix="aimfp_bf_")
    try:
        db = os.path.join(root, "p.db")
        conn = sqlite3.connect(db)
        conn.executescript(_schema())
        # rows without slug (simulate pre-migration)
        conn.execute("INSERT INTO completion_path (name, order_index) VALUES ('core',1)")
        conn.execute("INSERT INTO milestones (completion_path_id, name, status) VALUES (1,'M','pending')")
        conn.execute("INSERT INTO milestones (completion_path_id, name, status) VALUES (1,'M','pending')")
        conn.commit()
        n = _effect_mint_missing_slugs(conn)
        assert n["milestones"] == 2
        slugs = [r[0] for r in conn.execute("SELECT slug FROM milestones")]
        assert all(slugs) and len(set(slugs)) == 2  # unique, non-null
        # idempotent
        again = _effect_mint_missing_slugs(conn)
        assert again["milestones"] == 0
        conn.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


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


def test_export_add_modify_and_keyed_interaction(repo):
    root, db = repo
    m = _seed_base(db)
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO functions (file_id, name, purpose) VALUES (1,'bar','does bar')")
    foo = conn.execute("SELECT id FROM functions WHERE name='foo'").fetchone()[0]
    bar = conn.execute("SELECT id FROM functions WHERE name='bar'").fetchone()[0]
    conn.execute(
        "INSERT INTO interactions (source_function_id,target_function_id,interaction_type) VALUES (?,?,'call')",
        (foo, bar),
    )
    conn.execute("UPDATE milestones SET name='Core Development' WHERE id=?", (m,))
    T._insert_task(conn, m, "build bar", "pending", "medium", None, None)
    conn.commit()
    conn.close()
    _commit(root, "work")

    res = export_state_changeset(base, "work", worker_id="worker-1")
    assert res.success, res.error
    d = res.data

    assert d["provenance"] == {"worker_id": "worker-1", "branch": "work", "base_main_commit": base}
    assert d["warnings"] == []

    ents = {(e["kind"], e["op"], json.dumps(e["semantic_key"], sort_keys=True)) for e in d["entities"]}
    assert ("functions", "add", json.dumps({"file": "src/a.py", "name": "bar"}, sort_keys=True)) in ents

    # rename of a slug-keyed row is a clean modify, not delete+add
    ms_mods = [e for e in d["entities"] if e["kind"] == "milestones" and e["op"] == "modify"]
    assert len(ms_mods) == 1 and ms_mods[0]["attributes"]["name"] == "Core Development"

    task_adds = [e for e in d["entities"] if e["kind"] == "tasks" and e["op"] == "add"]
    assert any(t["attributes"]["name"] == "build bar" for t in task_adds)

    inter = [r for r in d["references"] if r["kind"] == "interaction" and r["op"] == "add"]
    assert inter and inter[0]["from"] == {"file": "src/a.py", "name": "foo"}
    assert inter[0]["to"] == {"file": "src/a.py", "name": "bar"}


def test_export_delete_classified(repo):
    root, db = repo
    _seed_base(db)
    base = _commit(root, "base")
    _git(root, "checkout", "-qb", "work")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM functions WHERE name='foo'")
    conn.commit()
    conn.close()
    _commit(root, "work")

    res = export_state_changeset(base, "work")
    assert res.success
    dels = [e for e in res.data["entities"] if e["kind"] == "functions" and e["op"] == "delete"]
    assert dels and dels[0]["semantic_key"] == {"file": "src/a.py", "name": "foo"}


def test_export_missing_base_db_is_nonfatal(repo):
    root, db = repo
    base = _commit(root, "empty-ish")  # project.db exists but empty
    # remove project.db content path at an earlier commit scenario: use a bogus base
    res = export_state_changeset("HEAD~0", "HEAD")
    assert res.success  # base == branch -> empty diff
    assert res.data["entities"] == []


def test_changeset_is_integer_free(repo):
    """No branch-local integer PKs may appear in any semantic key or edge endpoint."""
    root, db = repo
    m = _seed_base(db)
    base = _commit(root, "base")
    _git(root, "checkout", "-qb", "work")
    conn = sqlite3.connect(db)
    T._insert_task(conn, m, "another", "pending", "low", None, None)
    conn.commit()
    conn.close()
    _commit(root, "work")

    res = export_state_changeset(base, "work")
    for e in res.data["entities"]:
        for v in e["semantic_key"].values():
            assert not isinstance(v, int) or isinstance(v, bool), f"integer in key: {e}"


# ---------------------------------------------------------------------------
# apply_state_changeset (3-way merge)
# ---------------------------------------------------------------------------

def _conn(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def _main_branch(root):
    return "master" if "master" in _git(root, "branch") else "main"


def test_apply_nonoverlapping_merge_and_ref_rewire(repo):
    """Branch adds/modifies; main has unrelated drift; apply merges cleanly and rewires refs."""
    root, db = repo
    m = _seed_base(db)
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("INSERT INTO functions (file_id,name,purpose) VALUES (1,'bar','bar')")
    foo = c.execute("SELECT id FROM functions WHERE name='foo'").fetchone()[0]
    bar = c.execute("SELECT id FROM functions WHERE name='bar'").fetchone()[0]
    c.execute(
        "INSERT INTO interactions (source_function_id,target_function_id,interaction_type) VALUES (?,?,'call')",
        (foo, bar))
    c.execute("UPDATE milestones SET name='Core Development' WHERE id=?", (m,))
    T._insert_task(c, m, "build bar", "pending", "medium", None, None)
    c.commit(); c.close()
    _commit(root, "work")

    cs = export_state_changeset(base, "work", worker_id="w1").data

    # drift main with an unrelated function
    _git(root, "checkout", "-q", _main_branch(root))
    c = _conn(db); c.execute("INSERT INTO functions (file_id,name,purpose) VALUES (1,'baz','baz')")
    c.commit(); c.close()

    res = apply_state_changeset(cs)
    assert res.success, res.error
    assert res.data["conflicts"] == []

    c = _conn(db)
    names = sorted(r[0] for r in c.execute("SELECT name FROM functions"))
    assert names == ["bar", "baz", "foo"]  # branch add + main drift both survive
    assert c.execute("SELECT name FROM milestones").fetchone()[0] == "Core Development"
    fooid = c.execute("SELECT id FROM functions WHERE name='foo'").fetchone()[0]
    barid = c.execute("SELECT id FROM functions WHERE name='bar'").fetchone()[0]
    inter = c.execute("SELECT source_function_id, target_function_id FROM interactions").fetchone()
    assert (inter[0], inter[1]) == (fooid, barid)  # rewired to canonical ids
    mid = c.execute("SELECT id FROM milestones").fetchone()[0]
    tk = c.execute("SELECT milestone_id FROM tasks WHERE name='build bar'").fetchone()
    assert tk[0] == mid  # new task parented correctly
    c.close()


def test_apply_conflict_does_not_clobber_main(repo):
    """Concurrent modify of the same field is a conflict; main is preserved."""
    root, db = repo
    m = _seed_base(db)
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    c = _conn(db); c.execute("UPDATE milestones SET name='Branch Name' WHERE id=?", (m,))
    c.commit(); c.close()
    _commit(root, "work")
    cs = export_state_changeset(base, "work").data

    _git(root, "checkout", "-q", _main_branch(root))
    c = _conn(db); c.execute("UPDATE milestones SET name='Main Name' WHERE id=?", (m,))
    c.commit(); c.close()

    res = apply_state_changeset(cs)
    assert res.success
    assert any(x["kind"] == "milestones" and x["op"] == "modify" for x in res.data["conflicts"])
    c = _conn(db)
    assert c.execute("SELECT name FROM milestones").fetchone()[0] == "Main Name"  # not clobbered
    c.close()


def test_apply_idempotent_reapply_is_noop(repo):
    """Re-applying the same changeset onto an already-merged main produces no new changes."""
    root, db = repo
    m = _seed_base(db)
    base = _commit(root, "base")
    _git(root, "checkout", "-qb", "work")
    c = _conn(db); T._insert_task(c, m, "new task", "pending", "low", None, None); c.commit(); c.close()
    _commit(root, "work")
    cs = export_state_changeset(base, "work").data
    _git(root, "checkout", "-q", _main_branch(root))

    first = apply_state_changeset(cs)
    assert first.success and any(a.get("op") == "add" for a in first.data["applied"])
    second = apply_state_changeset(cs)
    assert second.success and second.data["conflicts"] == []
    # second apply mints nothing new
    assert second.data["minted_ids"] == []
    c = _conn(db)
    assert c.execute("SELECT COUNT(*) FROM tasks WHERE name='new task'").fetchone()[0] == 1
    c.close()


def test_apply_safe_delete_and_blocked_delete(repo):
    """Delete applies when safe; is a conflict when an inbound dependent exists in main."""
    root, db = repo
    _seed_base(db)
    # add a second function with no dependents to delete safely
    c = _conn(db); c.execute("INSERT INTO functions (file_id,name,purpose) VALUES (1,'lonely','x')")
    c.commit(); c.close()
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("DELETE FROM functions WHERE name='lonely'")  # safe delete
    c.execute("DELETE FROM functions WHERE name='foo'")     # will be blocked by a main dependent
    c.commit(); c.close()
    _commit(root, "work")
    cs = export_state_changeset(base, "work").data

    # main adds an inbound interaction into foo after base -> delete of foo must conflict
    _git(root, "checkout", "-q", _main_branch(root))
    c = _conn(db)
    foo = c.execute("SELECT id FROM functions WHERE name='foo'").fetchone()[0]
    lonely = c.execute("SELECT id FROM functions WHERE name='lonely'").fetchone()[0]
    c.execute(
        "INSERT INTO interactions (source_function_id,target_function_id,interaction_type) VALUES (?,?,'call')",
        (lonely, foo))
    c.commit(); c.close()

    res = apply_state_changeset(cs)
    assert res.success
    c = _conn(db)
    # foo kept (delete blocked by dependent), lonely... is referenced by the interaction we added,
    # so its delete is also blocked -> both remain; conflicts recorded
    fns = sorted(r[0] for r in c.execute("SELECT name FROM functions"))
    c.close()
    conflicts = [(x["kind"], x["op"]) for x in res.data["conflicts"]]
    assert ("functions", "delete") in conflicts
    assert "foo" in fns  # not deleted (had inbound dependent)


def test_detect_conflicts_clean_and_overlap(repo):
    """detect_state_conflicts flags entities touched by >1 branch; clean when disjoint."""
    root, db = repo
    m = _seed_base(db)
    base = _commit(root, "base")

    # work1: add task T1 (disjoint)
    _git(root, "checkout", "-q", base)
    _git(root, "checkout", "-qb", "work1")
    c = _conn(db); T._insert_task(c, m, "T1", "pending", "low", None, None); c.commit(); c.close()
    _commit(root, "work1")

    # work2: add task T2 (disjoint)
    _git(root, "checkout", "-q", base)
    _git(root, "checkout", "-qb", "work2")
    c = _conn(db); T._insert_task(c, m, "T2", "pending", "low", None, None); c.commit(); c.close()
    _commit(root, "work2")

    res = detect_state_conflicts([
        {"branch": "work1", "base_commit": base},
        {"branch": "work2", "base_commit": base},
    ])
    assert res.success, res.error
    assert res.data["clean"] is True
    assert set(res.data["branches_analyzed"]) == {"work1", "work2"}

    # now an overlapping pair: both rename the same milestone
    _git(root, "checkout", "-q", base)
    _git(root, "checkout", "-qb", "work3")
    c = _conn(db); c.execute("UPDATE milestones SET name='A' WHERE id=?", (m,)); c.commit(); c.close()
    _commit(root, "work3")
    _git(root, "checkout", "-q", base)
    _git(root, "checkout", "-qb", "work4")
    c = _conn(db); c.execute("UPDATE milestones SET name='B' WHERE id=?", (m,)); c.commit(); c.close()
    _commit(root, "work4")

    res2 = detect_state_conflicts([
        {"branch": "work3", "base_commit": base},
        {"branch": "work4", "base_commit": base},
    ])
    assert res2.success
    assert res2.data["clean"] is False
    ms_overlap = [o for o in res2.data["entity_overlaps"] if o["kind"] == "milestones"]
    assert ms_overlap and ms_overlap[0]["severity"] == "concurrent_modify"
    assert {t["branch"] for t in ms_overlap[0]["touched_by"]} == {"work3", "work4"}


def test_detect_conflicts_reports_export_errors(repo):
    """Bad branch refs are non-fatal and reported in export_errors."""
    root, db = repo
    _seed_base(db)
    base = _commit(root, "base")
    res = detect_state_conflicts([{"branch": "nonexistent-branch", "base_commit": base}])
    assert res.success
    assert res.data["export_errors"] and res.data["branches_analyzed"] == []


def _seed_base_with_entity_keys(db):
    """Seed two files + a function and type that carry entity_keys (Stage 2)."""
    c = _conn(db)
    c.execute("INSERT INTO files (path,name,language) VALUES ('src/a.py','a.py','Python')")
    c.execute("INSERT INTO files (path,name,language) VALUES ('src/b.py','b.py','Python')")
    c.execute("INSERT INTO functions (entity_key,file_id,name,purpose) VALUES ('fn-foo-aaaa1111',1,'foo','foo')")
    c.execute("INSERT INTO types (entity_key,file_id,name,definition_json) VALUES ('ty-color-bbbb2222',1,'Color','{}')")
    c.commit(); c.close()


def test_stage2_rename_and_move_is_modify_not_delete_add(repo):
    """With entity_key, renaming AND moving a function is a single modify, applied with no duplicate."""
    root, db = repo
    _seed_base_with_entity_keys(db)
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("UPDATE functions SET name='foo_renamed', file_id=2 WHERE entity_key='fn-foo-aaaa1111'")
    c.execute("UPDATE types SET name='Colour' WHERE entity_key='ty-color-bbbb2222'")
    c.commit(); c.close()
    _commit(root, "work")

    cs = export_state_changeset(base, "work").data
    fns = [e for e in cs["entities"] if e["kind"] == "functions"]
    assert len(fns) == 1 and fns[0]["op"] == "modify"
    assert fns[0]["semantic_key"] == {"entity_key": "fn-foo-aaaa1111"}
    assert fns[0]["attributes"]["name"] == "foo_renamed"
    assert fns[0]["attributes"]["file"] == "src/b.py"
    tys = [e for e in cs["entities"] if e["kind"] == "types"]
    assert len(tys) == 1 and tys[0]["op"] == "modify"

    _git(root, "checkout", "-q", _main_branch(root))
    res = apply_state_changeset(cs)
    assert res.success and res.data["conflicts"] == []
    c = _conn(db)
    row = c.execute("SELECT name, file_id FROM functions WHERE entity_key='fn-foo-aaaa1111'").fetchone()
    bid = c.execute("SELECT id FROM files WHERE path='src/b.py'").fetchone()[0]
    assert row[0] == "foo_renamed" and row[1] == bid
    assert c.execute("SELECT COUNT(*) FROM functions").fetchone()[0] == 1  # no duplicate
    assert c.execute("SELECT name FROM types WHERE entity_key='ty-color-bbbb2222'").fetchone()[0] == "Colour"
    c.close()


def test_stage2_rename_survives_concurrent_inbound_edge(repo):
    """The spec §1c win: branch renames foo while main concurrently adds an edge INTO foo.
    With entity_key the rename is a modify, so the edge (FK to the same row) survives — no conflict."""
    root, db = repo
    _seed_base_with_entity_keys(db)
    # add a caller function in base
    c = _conn(db)
    c.execute("INSERT INTO functions (entity_key,file_id,name,purpose) VALUES ('fn-caller-cccc3333',1,'caller','c')")
    c.commit(); c.close()
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("UPDATE functions SET name='foo2' WHERE entity_key='fn-foo-aaaa1111'")  # rename foo
    c.commit(); c.close()
    _commit(root, "work")
    cs = export_state_changeset(base, "work").data

    # main concurrently adds caller -> foo edge
    _git(root, "checkout", "-q", _main_branch(root))
    c = _conn(db)
    caller = c.execute("SELECT id FROM functions WHERE entity_key='fn-caller-cccc3333'").fetchone()[0]
    foo = c.execute("SELECT id FROM functions WHERE entity_key='fn-foo-aaaa1111'").fetchone()[0]
    c.execute(
        "INSERT INTO interactions (source_function_id,target_function_id,interaction_type) VALUES (?,?,'call')",
        (caller, foo))
    c.commit(); c.close()

    res = apply_state_changeset(cs)
    assert res.success
    assert not any(x["kind"] == "functions" for x in res.data["conflicts"])  # rename, not delete -> no conflict
    c = _conn(db)
    assert c.execute("SELECT name FROM functions WHERE entity_key='fn-foo-aaaa1111'").fetchone()[0] == "foo2"
    # the concurrent edge still points at the (renamed) foo row
    foo = c.execute("SELECT id FROM functions WHERE entity_key='fn-foo-aaaa1111'").fetchone()[0]
    edge = c.execute("SELECT target_function_id FROM interactions").fetchone()
    assert edge[0] == foo  # edge survived the rename
    c.close()


def test_apply_remove_edge_then_delete_node_single_pass(repo):
    """A self-consistent changeset that removes an edge AND deletes its target deletes
    cleanly in ONE pass (reference-removes are applied before entity-deletes)."""
    root, db = repo
    _seed_base(db)
    c = _conn(db)
    c.execute("INSERT INTO functions (file_id,name,purpose) VALUES (1,'bar','bar')")
    foo = c.execute("SELECT id FROM functions WHERE name='foo'").fetchone()[0]
    bar = c.execute("SELECT id FROM functions WHERE name='bar'").fetchone()[0]
    c.execute(
        "INSERT INTO interactions (source_function_id,target_function_id,interaction_type) VALUES (?,?,'call')",
        (bar, foo))
    c.commit(); c.close()
    base = _commit(root, "base")

    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("DELETE FROM interactions")          # remove bar->foo edge
    c.execute("DELETE FROM functions WHERE name='foo'")  # delete foo
    c.commit(); c.close()
    _commit(root, "work")
    cs = export_state_changeset(base, "work").data

    _git(root, "checkout", "-q", _main_branch(root))  # main == base, no drift
    res = apply_state_changeset(cs)
    assert res.success, res.error
    assert res.data["conflicts"] == []  # delete NOT falsely blocked
    c = _conn(db)
    names = [r[0] for r in c.execute("SELECT name FROM functions")]
    assert "foo" not in names and "bar" in names
    assert c.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 0
    c.close()
