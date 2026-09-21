"""
Tests for write-transaction isolation under a concurrent writer.

Two things are covered:

1. THE MECHANISM, in raw sqlite3. A deferred BEGIN that reads and then writes
   can be overtaken by another writer, and SQLite answers the upgrade with
   SQLITE_BUSY *immediately*, without invoking the busy handler — the read
   snapshot is stale, so waiting cannot help. No busy timeout value fixes this.
   BEGIN IMMEDIATE takes the write lock up front and does not have the problem.
   These tests exist because the conclusion ("raising the timeout will not help
   here") is easy to doubt and cheap to demonstrate.

2. THE CALLERS. batch_update_progress and update_items both SELECT a row and
   then UPDATE it inside one transaction, so both must use BEGIN IMMEDIATE.
"""

import os
import shutil
import sqlite3
import tempfile
import threading
import time

import pytest


@pytest.fixture
def db_path():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "iso.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO tasks (id, status) VALUES (1, 'pending')")
    conn.commit()
    conn.close()
    yield path
    shutil.rmtree(tmp)


# ============================================================================
# The mechanism
# ============================================================================

def test_deferred_begin_upgrade_ignores_the_busy_timeout(db_path):
    """A generous timeout does not save a deferred read-then-write upgrade."""
    reader = sqlite3.connect(db_path, timeout=30.0)
    writer = sqlite3.connect(db_path, timeout=30.0)
    try:
        reader.execute("BEGIN")  # deferred — snapshot taken at the first read
        reader.execute("SELECT status FROM tasks WHERE id = 1").fetchone()

        # Another writer commits while our snapshot is open.
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE tasks SET status = 'completed' WHERE id = 1")
        writer.commit()

        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError) as exc:
            reader.execute("UPDATE tasks SET status = 'in_progress' WHERE id = 1")
        elapsed = time.monotonic() - started

        # It failed, and crucially it failed AT ONCE rather than waiting out
        # the 30s timeout — the busy handler was never consulted.
        assert "database is locked" in str(exc.value)
        assert elapsed < 1.0, f"waited {elapsed:.2f}s — the busy handler ran"
    finally:
        reader.close()
        writer.close()


def test_immediate_begin_serializes_instead_of_failing(db_path):
    """BEGIN IMMEDIATE takes the write lock up front, so the other writer waits."""
    holder = sqlite3.connect(db_path, timeout=30.0)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("SELECT status FROM tasks WHERE id = 1").fetchone()

    outcome = {}

    def contender():
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE tasks SET status = 'contended' WHERE id = 1")
            conn.commit()
            outcome["ok"] = True
        except sqlite3.OperationalError as e:  # pragma: no cover - failure path
            outcome["error"] = str(e)
        finally:
            conn.close()

    t = threading.Thread(target=contender)
    t.start()
    time.sleep(0.1)  # contender is now blocked on the write lock, not failing

    # The holder completes its read-then-write without a stale snapshot.
    holder.execute("UPDATE tasks SET status = 'holder' WHERE id = 1")
    holder.commit()
    holder.close()

    t.join(timeout=30)
    assert outcome.get("ok"), f"contender did not complete: {outcome}"

    conn = sqlite3.connect(db_path)
    try:
        # Serialized, not lost: the contender's write landed after the holder's.
        assert conn.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0] == "contended"
    finally:
        conn.close()


# ============================================================================
# The callers
# ============================================================================

@pytest.mark.parametrize("module_path, function_name", [
    ("src/aimfp/helpers/orchestrators/state.py", "batch_update_progress"),
    ("src/aimfp/helpers/project/items_notes.py", "update_items"),
])
def test_read_then_write_callers_use_begin_immediate(module_path, function_name):
    """Both functions SELECT a row then UPDATE it inside one transaction.

    A plain `BEGIN` there is the bug demonstrated above, and it is invisible
    until a second process writes concurrently — which is exactly the setup
    this work is preparing for.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, module_path), encoding="utf-8").read()

    assert 'conn.execute("BEGIN IMMEDIATE")' in source, (
        f"{function_name} must open its transaction with BEGIN IMMEDIATE"
    )
    assert 'conn.execute("BEGIN")' not in source, (
        f"{module_path} still contains a deferred BEGIN"
    )
