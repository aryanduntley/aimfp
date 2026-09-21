"""
Tests for aimfp.database.connection._open_connection

Covers the concurrency contract the opener is responsible for:
- every connection carries an explicit busy timeout (not CPython's implicit 5s)
- readonly=True inspects a database without reconfiguring it
- readonly=True is NOT a fix for a WAL-mode database on a read-only filesystem

That last one is a regression test for a wrong fix, not for a bug. The
journal mode lives in the database file header, so SQLite demands the -shm
sidecar before any pragma runs; skipping the journal_mode write is too late to
help. immutable=1 is what works. Encoding it here so the next person reaching
for readonly= to solve a read-only-install failure sees why it cannot.
"""

import os
import sqlite3
import shutil
import tempfile

import pytest

from aimfp.database.connection import DEFAULT_BUSY_TIMEOUT, _open_connection


# Header byte 18 is the file-format write version: 1 = rollback, 2 = WAL.
_JOURNAL_BYTE = 18
_ROLLBACK, _WAL = 1, 2


def _journal_byte(db_path: str) -> int:
    with open(db_path, "rb") as f:
        return f.read(20)[_JOURNAL_BYTE]


def _make_db(directory: str, name: str = "t.db", journal: str = "DELETE") -> str:
    db_path = os.path.join(directory, name)
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA journal_mode={journal}")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    conn.commit()
    conn.close()
    # The wheel ships no sidecars; neither do these fixtures.
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)
    return db_path


# ============================================================================
# Busy timeout
# ============================================================================

def test_default_busy_timeout_is_applied():
    tmp = tempfile.mkdtemp()
    try:
        conn = _open_connection(_make_db(tmp))
        try:
            ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert ms == int(DEFAULT_BUSY_TIMEOUT * 1000)
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp)


def test_default_busy_timeout_is_not_cpython_default():
    """5.0s is sqlite3's implicit default and was never a deliberate choice."""
    assert DEFAULT_BUSY_TIMEOUT > 5.0


def test_explicit_timeout_overrides_default():
    tmp = tempfile.mkdtemp()
    try:
        conn = _open_connection(_make_db(tmp), timeout=0.25)
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 250
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp)


def test_busy_timeout_governs_a_blocked_write():
    """The point of the timeout: a second writer waits rather than erroring.

    The fixture is WAL-mode because every real AIMFP database is. On a
    rollback-mode database the opener's own `PRAGMA journal_mode = WAL` has to
    convert the file and so needs the exclusive lock, and the open itself fails
    before any statement runs; on a WAL database that pragma is a no-op read and
    contention lands where it belongs, on the write.
    """
    tmp = tempfile.mkdtemp()
    try:
        db_path = _make_db(tmp, journal="WAL")
        holder = sqlite3.connect(db_path, timeout=0.1)
        holder.execute("BEGIN EXCLUSIVE")

        waiter = _open_connection(db_path, timeout=0.05)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                waiter.execute("INSERT INTO t VALUES (99)")
                waiter.commit()
        finally:
            waiter.close()

        holder.rollback()
        holder.close()

        # Lock released — the same write now succeeds.
        after = _open_connection(db_path)
        try:
            after.execute("INSERT INTO t VALUES (99)")
            after.commit()
            assert after.execute("SELECT count(*) FROM t").fetchone()[0] == 4
        finally:
            after.close()
    finally:
        shutil.rmtree(tmp)


# ============================================================================
# readonly=True
# ============================================================================

def test_normal_open_configures_wal():
    tmp = tempfile.mkdtemp()
    try:
        db_path = _make_db(tmp, journal="DELETE")
        assert _journal_byte(db_path) == _ROLLBACK
        conn = _open_connection(db_path)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()
        assert _journal_byte(db_path) == _WAL
    finally:
        shutil.rmtree(tmp)


def test_readonly_does_not_rewrite_journal_mode():
    """A version probe must not reconfigure the database it is inspecting.

    This is the migration case: reading schema_version off a database that has
    not been migrated yet must leave that database exactly as it was found.
    """
    tmp = tempfile.mkdtemp()
    try:
        db_path = _make_db(tmp, journal="DELETE")
        conn = _open_connection(db_path, readonly=True)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 3
        finally:
            conn.close()
        assert _journal_byte(db_path) == _ROLLBACK
        assert not os.path.exists(db_path + "-wal")
    finally:
        shutil.rmtree(tmp)


def test_readonly_refuses_writes():
    tmp = tempfile.mkdtemp()
    try:
        conn = _open_connection(_make_db(tmp), readonly=True)
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("INSERT INTO t VALUES (4)")
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp)


def test_readonly_handles_uri_hostile_paths():
    """readonly builds a file: URI, so '?' and '#' must survive escaping."""
    tmp = tempfile.mkdtemp()
    try:
        conn = _open_connection(_make_db(tmp, name="odd ? and # name.db"), readonly=True)
        try:
            assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 3
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_readonly_does_not_rescue_wal_db_on_readonly_filesystem():
    """Regression test for a WRONG fix.

    A WAL-mode database in a non-writable directory cannot be opened at all:
    the mode is in the file header, so SQLite needs -shm before any pragma
    runs. readonly= skips the journal_mode write, which is too late. Only
    immutable=1 (or shipping a non-WAL database) works.
    """
    tmp = tempfile.mkdtemp()
    try:
        wal_db = _make_db(tmp, name="wal.db", journal="WAL")
        rollback_db = _make_db(tmp, name="rollback.db", journal="DELETE")
        assert _journal_byte(wal_db) == _WAL

        os.chmod(tmp, 0o555)
        try:
            # readonly= is not enough for the WAL-mode database.
            with pytest.raises(sqlite3.OperationalError):
                _open_connection(wal_db, readonly=True).close()

            # immutable=1 is what actually works.
            conn = sqlite3.connect(f"file:{wal_db}?mode=ro&immutable=1", uri=True)
            try:
                assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 3
            finally:
                conn.close()

            # A non-WAL database needs neither.
            conn = _open_connection(rollback_db, readonly=True)
            try:
                assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 3
            finally:
                conn.close()
        finally:
            os.chmod(tmp, 0o755)
    finally:
        shutil.rmtree(tmp)


# ============================================================================
# aimfp_core.db ships read-only
# ============================================================================

def test_shipped_core_db_is_not_in_wal_mode():
    """The wheel must not ship a WAL-mode database.

    WAL lives in the file header, so a WAL-mode core.db cannot be OPENED at all
    on a read-only install — SQLite needs the -shm sidecar before any pragma
    runs. dev/sync-directives.py::finalize_shipped_db resets it to rollback
    mode after every build; this asserts the committed artifact matches.
    """
    from aimfp.database.connection import get_core_db_path

    core_db = get_core_db_path()
    with open(core_db, "rb") as f:
        assert f.read(20)[_JOURNAL_BYTE] == _ROLLBACK, (
            "aimfp_core.db is in WAL mode — rerun dev/sync-directives.py"
        )


def test_reading_core_db_never_reconfigures_it():
    """Regression guard for the hot path.

    get_return_statements runs on EVERY tool call. When it opened core.db
    read-write it rewrote the journal mode into the header of a shipped
    artifact, silently undoing the build-time fix.
    """
    from aimfp.database.connection import (
        get_core_db_path,
        _open_core_connection,
        get_return_statements,
    )

    core_db = get_core_db_path()
    with open(core_db, "rb") as f:
        before = f.read(100)

    conn = _open_core_connection()
    try:
        conn.execute("SELECT COUNT(*) FROM directives").fetchone()
    finally:
        conn.close()
    get_return_statements("aimfp_run")

    with open(core_db, "rb") as f:
        assert f.read(100) == before, "reading core.db mutated its header"
    assert not os.path.exists(core_db + "-wal")


def test_core_db_opens_from_a_readonly_directory():
    """The scenario this whole item exists for: a read-only install."""
    from aimfp.database.connection import get_core_db_path

    tmp = tempfile.mkdtemp()
    try:
        staged = os.path.join(tmp, "aimfp_core.db")
        shutil.copyfile(get_core_db_path(), staged)
        if os.geteuid() != 0:
            os.chmod(tmp, 0o555)
        try:
            conn = _open_connection(staged, immutable=True)
            try:
                assert conn.execute("SELECT COUNT(*) FROM directives").fetchone()[0] > 0
            finally:
                conn.close()
        finally:
            os.chmod(tmp, 0o755)
    finally:
        shutil.rmtree(tmp)
