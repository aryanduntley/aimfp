"""
Tests for aimfp.helpers.orchestrators.migration

Verifies:
- Version mismatch detection
- Data preservation through migration
- Timestamp preservation (triggers don't fire on INSERT)
- Backup + new DB in temp locations
- Row count verification
- Missing DB handling (user_directives skipped)
- All-current returns empty migrated list
- Custom aimfp_folder parameter
"""

import os
import re
import sqlite3
import tempfile
import shutil

import pytest

from aimfp.helpers.orchestrators.migration import (
    _check_pending_migrations,
    _get_db_version,
    _impossible_version_reason,
    _get_schema_path,
    migrate_databases,
)
from aimfp.helpers.orchestrators._common import get_core_db_path
from aimfp.database.connection import set_project_root, clear_project_root_cache


# ============================================================================
# Authoritative version source
# ============================================================================
#
# The migration system's source of truth for "what version should this DB be"
# is aimfp_core.db.expected_schema_versions — migrate_databases() reads it to
# decide the target version. Tests assert against THAT, not hardcoded literals,
# so a schema bump never silently breaks (or requires editing) these tests.

_SCHEMA_VERSION_RE = (
    r"INSERT OR REPLACE INTO schema_version \(id, version\) VALUES \(1, '[^']+'\)"
)


def _expected_version(db_name: str) -> str:
    """Authoritative target version for db_name, from aimfp_core.db (same
    source migrate_databases uses)."""
    conn = sqlite3.connect(get_core_db_path())
    try:
        row = conn.execute(
            "SELECT expected_version FROM expected_schema_versions WHERE db_name=?",
            (db_name,),
        ).fetchone()
        assert row is not None, f"no expected_schema_versions row for {db_name}"
        return row[0]
    finally:
        conn.close()


# ============================================================================
# Fixtures: Create temp project with old-version DBs
# ============================================================================

def _setup_project(tmp_dir, aimfp_folder=".aimfp-project"):
    """Create a temp project with old-version project.db and user_preferences.db."""
    aimfp_dir = os.path.join(tmp_dir, aimfp_folder)
    os.makedirs(aimfp_dir, exist_ok=True)
    os.makedirs(os.path.join(aimfp_dir, "backups"), exist_ok=True)
    return aimfp_dir


def _create_old_project_db(aimfp_dir, version="1.6"):
    """Create a project.db with old version and some test data."""
    db_path = os.path.join(aimfp_dir, "project.db")
    conn = sqlite3.connect(db_path)
    # Use real schema but with old version
    schema_path = _get_schema_path("project.sql")
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    # Downgrade the schema's declared version to the requested old version.
    # Match whatever version the schema currently declares (don't hardcode it)
    # so this fixture keeps working across schema bumps.
    schema_sql, n = re.subn(
        _SCHEMA_VERSION_RE,
        f"INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, '{version}')",
        schema_sql,
    )
    assert n == 1, f"expected exactly 1 schema_version insert in project.sql, found {n}"
    # Remove the new note types to simulate old schema
    schema_sql = schema_sql.replace(
        "'summary',         -- Manual summaries (distinct from auto_summary which is automated)\n"
        "        -- Deferred work tracking\n"
        "        'deferred',        -- Placeholder code, TODOs, stubs, intentionally incomplete work\n"
        "        'completed',       -- Resolved deferred items, closed follow-ups\n"
        "        'obsolete'         -- Deferred items no longer relevant (code refactored, task re-scoped)\n",
        "'summary'          -- Manual summaries (distinct from auto_summary which is automated)\n"
    )
    conn.executescript(schema_sql)

    # Insert test data
    conn.execute(
        "INSERT INTO project (name, purpose, goals_json) VALUES (?, ?, ?)",
        ("TestProject", "Testing migration", '["test"]')
    )
    conn.execute(
        "INSERT INTO infrastructure (type, value, description) VALUES (?, ?, ?)",
        ("language", "Python", "Primary language")
    )
    conn.execute(
        "INSERT INTO notes (content, note_type, source, severity) VALUES (?, ?, ?, ?)",
        ("Test note", "info", "ai", "info")
    )
    conn.execute(
        "INSERT INTO files (path, language) VALUES (?, ?)",
        ("src/main.py", "Python")
    )
    conn.commit()
    conn.close()
    return db_path


def _create_old_prefs_db(aimfp_dir, version="1.2"):
    """Create a user_preferences.db at current version (no migration needed)."""
    db_path = os.path.join(aimfp_dir, "user_preferences.db")
    conn = sqlite3.connect(db_path)
    schema_path = _get_schema_path("user_preferences.sql")
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    conn.executescript(schema_sql)
    conn.close()
    return db_path


# ============================================================================
# _get_db_version Tests
# ============================================================================

def test_get_db_version_returns_version():
    tmp = tempfile.mkdtemp()
    try:
        aimfp_dir = _setup_project(tmp)
        db_path = _create_old_project_db(aimfp_dir, "1.6")
        assert _get_db_version(db_path) == "1.6"
    finally:
        shutil.rmtree(tmp)


def test_get_db_version_unversioned_db_returns_default():
    """A database that genuinely predates schema versioning has tables but no
    schema_version table. That — and only that — reads as '0.0'."""
    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "ancient.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
        conn.commit()
        conn.close()
        assert _get_db_version(db_path) == "0.0"
    finally:
        shutil.rmtree(tmp)


def test_get_db_version_unreadable_raises_not_zero():
    """Unreadable is not un-versioned. Returning '0.0' here would make a
    current database look pending and route it into a destructive rebuild."""
    with pytest.raises(sqlite3.Error):
        _get_db_version("/nonexistent/dir/path.db")


def test_get_db_version_corrupt_raises_not_zero():
    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "corrupt.db")
        with open(db_path, "wb") as f:
            f.write(b"this is emphatically not a sqlite database" * 16)
        with pytest.raises(sqlite3.Error):
            _get_db_version(db_path)
    finally:
        shutil.rmtree(tmp)


def test_get_db_version_locked_raises_not_zero():
    """The threat model: a concurrent writer holds the write lock while the
    session-start migration check probes the version."""
    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "locked.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version TEXT)")
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, '1.12')")
        conn.commit()

        holder = sqlite3.connect(db_path, timeout=0.1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                _get_db_version(db_path, timeout=0.1)
        finally:
            holder.rollback()
            holder.close()
            conn.close()
    finally:
        shutil.rmtree(tmp)


# ============================================================================
# _check_pending_migrations Tests
# ============================================================================

def test_check_pending_detects_old_version():
    tmp = tempfile.mkdtemp()
    try:
        aimfp_dir = _setup_project(tmp)
        _create_old_project_db(aimfp_dir, "1.6")
        _create_old_prefs_db(aimfp_dir)

        result = _check_pending_migrations(tmp, ".aimfp-project")
        assert result['checked'] is True
        # project.db (created at old 1.6) should be pending vs current schema
        pending_names = [p['db_name'] for p in result['pending']]
        assert 'project' in pending_names
        # user_preferences should be up_to_date (1.2 == 1.2)
        up_to_date_names = [u['db_name'] for u in result['up_to_date']]
        assert 'user_preferences' in up_to_date_names
    finally:
        shutil.rmtree(tmp)


def test_check_pending_skips_missing_db():
    tmp = tempfile.mkdtemp()
    try:
        aimfp_dir = _setup_project(tmp)
        _create_old_project_db(aimfp_dir, "1.6")
        # No user_preferences.db or user_directives.db

        result = _check_pending_migrations(tmp, ".aimfp-project")
        assert result['checked'] is True
        skipped_names = [s['db_name'] for s in result['skipped']]
        # user_directives should be skipped (file missing)
        assert 'user_directives' in skipped_names
    finally:
        shutil.rmtree(tmp)


def test_check_pending_never_reports_unreadable_as_pending():
    """THE regression guard for the data-loss chain: an unreadable project.db
    must land in `skipped`, never in `pending`. Anything in `pending` is what
    the session-start return statement tells the AI to hand to
    migrate_databases, which rebuilds it and has the AI overwrite the original.
    """
    tmp = tempfile.mkdtemp()
    try:
        aimfp_dir = _setup_project(tmp)
        _create_old_prefs_db(aimfp_dir)
        # project.db present but unreadable — the shape a lock or corruption
        # presents to the probe.
        with open(os.path.join(aimfp_dir, "project.db"), "wb") as f:
            f.write(b"not a database" * 64)

        result = _check_pending_migrations(tmp, ".aimfp-project")
        assert result['checked'] is True
        assert 'project' not in [p['db_name'] for p in result['pending']]
        assert 'project' not in [u['db_name'] for u in result['up_to_date']]
        skipped = {s['db_name']: s['reason'] for s in result['skipped']}
        assert 'project' in skipped
        assert 'unreadable' in skipped['project'].lower()
    finally:
        shutil.rmtree(tmp)


def test_check_pending_all_current():
    tmp = tempfile.mkdtemp()
    try:
        aimfp_dir = _setup_project(tmp)
        # Create project.db straight from the schema (already at current version)
        db_path = os.path.join(aimfp_dir, "project.db")
        conn = sqlite3.connect(db_path)
        schema_path = _get_schema_path("project.sql")
        with open(schema_path, 'r') as f:
            conn.executescript(f.read())
        conn.close()
        _create_old_prefs_db(aimfp_dir)

        result = _check_pending_migrations(tmp, ".aimfp-project")
        assert result['checked'] is True
        assert len(result['pending']) == 0
        up_to_date_names = [u['db_name'] for u in result['up_to_date']]
        assert 'project' in up_to_date_names
        assert 'user_preferences' in up_to_date_names
    finally:
        shutil.rmtree(tmp)


# ============================================================================
# Backstop: migrate_databases refuses an impossible '0.0'
# ============================================================================

def test_migrate_refuses_db_with_schema_version_table_but_no_row():
    """A schema_version table with no row is damage, not an old database.

    Rebuilding on that premise would discard the current contents, so the
    rebuild is refused and the database is left for inspection.
    """
    tmp = tempfile.mkdtemp()
    try:
        aimfp_dir = _setup_project(tmp)
        _create_old_prefs_db(aimfp_dir)

        db_path = os.path.join(aimfp_dir, "project.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version TEXT);"
            "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);"
        )
        conn.commit()
        conn.close()
        before = open(db_path, "rb").read()

        assert _get_db_version(db_path) == "0.0"
        assert _impossible_version_reason(db_path) is not None

        set_project_root(tmp)
        try:
            result = migrate_databases()
        finally:
            clear_project_root_cache()

        assert result.success is True
        assert 'project' not in [m['db_name'] for m in result.data['migrated']]
        skipped = {s['db_name']: s['reason'] for s in result.data['skipped']}
        assert 'project' in skipped
        assert 'refusing to migrate' in skipped['project'].lower()
        # Untouched on disk.
        assert open(db_path, "rb").read() == before
    finally:
        shutil.rmtree(tmp)


def test_impossible_version_reason_flags_populated_db_without_schema_version():
    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "populated.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            "CREATE TABLE files (id INTEGER PRIMARY KEY);"
            "CREATE TABLE notes (id INTEGER PRIMARY KEY);"
        )
        conn.commit()
        conn.close()
        reason = _impossible_version_reason(db_path)
        assert reason is not None and "no schema_version" in reason
    finally:
        shutil.rmtree(tmp)


def test_impossible_version_reason_allows_a_genuinely_empty_db():
    """An empty database is not a contradiction — nothing to protect."""
    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "empty.db")
        sqlite3.connect(db_path).close()
        assert _impossible_version_reason(db_path) is None
    finally:
        shutil.rmtree(tmp)


# ============================================================================
# migrate_databases Tests
# ============================================================================

def test_migrate_databases_nothing_pending():
    tmp = tempfile.mkdtemp()
    try:
        set_project_root(tmp)
        aimfp_dir = _setup_project(tmp)
        # Create at current versions
        db_path = os.path.join(aimfp_dir, "project.db")
        conn = sqlite3.connect(db_path)
        with open(_get_schema_path("project.sql"), 'r') as f:
            conn.executescript(f.read())
        conn.close()
        _create_old_prefs_db(aimfp_dir)

        result = migrate_databases()
        assert result.success is True
        assert len(result.data['migrated']) == 0
        assert result.data['message'] == 'All databases up to date'
    finally:
        clear_project_root_cache()
        shutil.rmtree(tmp)


def test_migrate_databases_upgrades_project():
    tmp = tempfile.mkdtemp()
    try:
        set_project_root(tmp)
        aimfp_dir = _setup_project(tmp)
        _create_old_project_db(aimfp_dir, "1.6")
        _create_old_prefs_db(aimfp_dir)

        result = migrate_databases()
        assert result.success is True
        assert len(result.data['migrated']) == 1

        migrated = result.data['migrated'][0]
        assert migrated['db_name'] == 'project'
        assert migrated['old_version'] == '1.6'
        assert migrated['new_version'] == _expected_version('project')
        assert migrated['backup_temp_path'] is not None
        assert migrated['new_db_temp_path'] is not None
        assert os.path.isfile(migrated['backup_temp_path'])
        assert os.path.isfile(migrated['new_db_temp_path'])

        # Cleanup temp files
        os.remove(migrated['backup_temp_path'])
        os.remove(migrated['new_db_temp_path'])
    finally:
        clear_project_root_cache()
        shutil.rmtree(tmp)


def test_migrate_databases_preserves_data():
    tmp = tempfile.mkdtemp()
    try:
        set_project_root(tmp)
        aimfp_dir = _setup_project(tmp)
        _create_old_project_db(aimfp_dir, "1.6")
        _create_old_prefs_db(aimfp_dir)

        result = migrate_databases()
        migrated = result.data['migrated'][0]

        # Verify data in new DB
        new_conn = sqlite3.connect(migrated['new_db_temp_path'])
        new_conn.row_factory = sqlite3.Row

        # Check project row
        cursor = new_conn.execute("SELECT name, purpose FROM project")
        row = cursor.fetchone()
        assert row['name'] == "TestProject"
        assert row['purpose'] == "Testing migration"

        # Check infrastructure row
        cursor = new_conn.execute("SELECT type, value FROM infrastructure WHERE type='language'")
        row = cursor.fetchone()
        assert row['value'] == "Python"

        # Check note
        cursor = new_conn.execute("SELECT content, note_type FROM notes WHERE content='Test note'")
        row = cursor.fetchone()
        assert row is not None
        assert row['note_type'] == "info"

        # Check file
        cursor = new_conn.execute("SELECT path FROM files WHERE path='src/main.py'")
        row = cursor.fetchone()
        assert row is not None

        # Check version is new
        cursor = new_conn.execute("SELECT version FROM schema_version")
        row = cursor.fetchone()
        assert row['version'] == _expected_version('project')

        new_conn.close()

        # Cleanup
        os.remove(migrated['backup_temp_path'])
        os.remove(migrated['new_db_temp_path'])
    finally:
        clear_project_root_cache()
        shutil.rmtree(tmp)


def test_migrate_databases_preserves_timestamps():
    tmp = tempfile.mkdtemp()
    try:
        set_project_root(tmp)
        aimfp_dir = _setup_project(tmp)
        old_db_path = _create_old_project_db(aimfp_dir, "1.6")

        # Read original timestamp
        old_conn = sqlite3.connect(old_db_path)
        old_conn.row_factory = sqlite3.Row
        cursor = old_conn.execute("SELECT created_at, updated_at FROM project LIMIT 1")
        old_row = cursor.fetchone()
        old_created = old_row['created_at']
        old_updated = old_row['updated_at']
        old_conn.close()

        _create_old_prefs_db(aimfp_dir)

        result = migrate_databases()
        migrated = result.data['migrated'][0]

        # Verify timestamps preserved in new DB
        new_conn = sqlite3.connect(migrated['new_db_temp_path'])
        new_conn.row_factory = sqlite3.Row
        cursor = new_conn.execute("SELECT created_at, updated_at FROM project LIMIT 1")
        new_row = cursor.fetchone()
        assert new_row['created_at'] == old_created
        assert new_row['updated_at'] == old_updated
        new_conn.close()

        # Cleanup
        os.remove(migrated['backup_temp_path'])
        os.remove(migrated['new_db_temp_path'])
    finally:
        clear_project_root_cache()
        shutil.rmtree(tmp)


def test_migrate_databases_verification_match():
    tmp = tempfile.mkdtemp()
    try:
        set_project_root(tmp)
        aimfp_dir = _setup_project(tmp)
        _create_old_project_db(aimfp_dir, "1.6")
        _create_old_prefs_db(aimfp_dir)

        result = migrate_databases()
        migrated = result.data['migrated'][0]
        verification = migrated['verification']

        # All tables with data should show match=True
        for table, info in verification.items():
            assert info['match'] is True, f"Table {table}: {info}"

        # Verify specific tables have expected row counts
        assert verification['project']['old_rows'] == 1
        assert verification['infrastructure']['old_rows'] == 1
        assert verification['notes']['old_rows'] == 1
        assert verification['files']['old_rows'] == 1

        # Cleanup
        os.remove(migrated['backup_temp_path'])
        os.remove(migrated['new_db_temp_path'])
    finally:
        clear_project_root_cache()
        shutil.rmtree(tmp)


def test_migrate_databases_backup_is_valid():
    tmp = tempfile.mkdtemp()
    try:
        set_project_root(tmp)
        aimfp_dir = _setup_project(tmp)
        _create_old_project_db(aimfp_dir, "1.6")
        _create_old_prefs_db(aimfp_dir)

        result = migrate_databases()
        migrated = result.data['migrated'][0]

        # Backup should be a valid SQLite DB with old version
        backup_version = _get_db_version(migrated['backup_temp_path'])
        assert backup_version == "1.6"

        # Cleanup
        os.remove(migrated['backup_temp_path'])
        os.remove(migrated['new_db_temp_path'])
    finally:
        clear_project_root_cache()
        shutil.rmtree(tmp)


def test_migrate_databases_custom_aimfp_folder():
    tmp = tempfile.mkdtemp()
    try:
        set_project_root(tmp)
        aimfp_dir = _setup_project(tmp, aimfp_folder=".custom-aimfp")
        _create_old_project_db(aimfp_dir, "1.6")

        result = migrate_databases(aimfp_folder=".custom-aimfp")
        assert result.success is True
        assert len(result.data['migrated']) == 1
        assert result.data['migrated'][0]['db_name'] == 'project'

        # Cleanup
        for m in result.data['migrated']:
            if m.get('backup_temp_path'):
                os.remove(m['backup_temp_path'])
            if m.get('new_db_temp_path'):
                os.remove(m['new_db_temp_path'])
    finally:
        clear_project_root_cache()
        shutil.rmtree(tmp)
