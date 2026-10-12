"""
AIMFP Helper Functions - Database Migration

Handles schema migrations for project databases (project.db, user_preferences.db,
user_directives.db). Core DB is read-only and ships with updated schemas — no
migrations needed.

Migration approach: no migration files. Creates fresh DBs from new schema SQL,
transfers data from old DBs via ATTACH, verifies row counts, and swaps each
rebuilt DB in place, keeping a pre-migration copy in <project folder>/backups/.

Functions:
- _check_pending_migrations: Internal checker (not a tool)
- migrate_databases: MCP tool — performs all pending migrations
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, FrozenSet, List, Optional

from ._common import (
    DEFAULT_BUSY_TIMEOUT,
    _open_connection,
    get_core_db_path,
    get_aimfp_project_dir,
    resolve_project_root,
    database_exists,
    _get_table_names,
    get_return_statements,
    Result,
    AIMFP_PROJECT_DIR,
    resolve_project_relative,
)
from ..shared.return_gates import gate_return_statements


# ============================================================================
# Internal: Schema Path Resolution
# ============================================================================

def _get_schema_path(schema_file: str) -> str:
    """Pure: Get path to a database schema file."""
    helpers_dir = Path(__file__).parent.parent  # src/aimfp/helpers/
    return str(helpers_dir.parent / "database" / "schemas" / schema_file)


# ============================================================================
# Internal: Pending Migration Checker
# ============================================================================

# Maps DB names to their schema SQL files and file paths within aimfp_folder
_DB_CONFIG: Dict[str, Dict[str, str]] = {
    'project': {
        'schema_file': 'project.sql',
        'db_file': 'project.db',
    },
    'user_preferences': {
        'schema_file': 'user_preferences.sql',
        'db_file': 'user_preferences.db',
    },
    'user_directives': {
        'schema_file': 'user_directives.sql',
        'db_file': 'user_directives.db',
    },
}


# A database predating schema versioning has no schema_version table. That
# absence is the ONLY condition that legitimately reads as un-versioned.
PRE_VERSIONING_VERSION = '0.0'


def _get_db_version(db_path: str, timeout: float = DEFAULT_BUSY_TIMEOUT) -> str:
    """
    Effect: Read schema_version from a database.

    Returns PRE_VERSIONING_VERSION only for a database that genuinely predates
    schema versioning — one with no schema_version table. Every other failure
    (locked, corrupt, permission denied) propagates to the caller.

    Reporting an unreadable database as '0.0' is not a safe default, it is a
    data-loss path. A current project.db that happened to be locked by a
    concurrent writer would read as un-versioned, land in `pending`, and the
    session-start return statement instructs the AI to call migrate_databases.
    That rebuilds the database from an ATTACH snapshot and hands the AI a
    return statement telling it to move the replacement over the original —
    discarding everything the other writer committed in between. Callers
    exclude absence with an isfile guard upstream, so the only cases reaching
    here are readable, un-versioned, or broken.

    Args:
        db_path: Path to the database to probe
        timeout: Busy timeout in seconds. Tests pass a small value to make
            lock contention fail fast.

    Returns:
        Version string, or PRE_VERSIONING_VERSION when schema_version is absent

    Raises:
        sqlite3.Error: database unreadable for any reason other than a missing
            schema_version table
    """
    conn = _open_connection(db_path, timeout=timeout, readonly=True)
    try:
        try:
            row = conn.execute(
                "SELECT version FROM schema_version WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError as e:
            if 'no such table' in str(e).lower():
                return PRE_VERSIONING_VERSION
            raise
        return row['version'] if row else PRE_VERSIONING_VERSION
    finally:
        conn.close()


def _impossible_version_reason(db_path: str) -> Optional[str]:
    """
    Effect: Explain why a database reporting '0.0' cannot really be un-versioned.

    Backstop for the destructive chain _get_db_version guards against. That
    probe now raises instead of reporting an unreadable database as '0.0', so
    this should be unreachable through it — which is the point. It holds even
    if some future exception path reintroduces the swallow, and it is the last
    check before migrate_databases rebuilds a database and hands the AI a
    return statement telling it to move the replacement over the original.

    A database carrying a schema_version table is a versioned database by
    definition, so reading '0.0' off one is a contradiction: the table is
    present but its row is missing or empty, which is damage rather than age.
    A populated database with no schema_version table at all is the same kind
    of contradiction in practice — AIMFP's schemas have created that table
    since the beginning, so there is no era of AIMFP databases that legitimately
    lack it.

    Refusing is recoverable and leaves the database for inspection. Rebuilding
    on a wrong premise is not.

    Args:
        db_path: Path to the database that reported PRE_VERSIONING_VERSION

    Returns:
        Reason string when the rebuild must be refused, else None
    """
    try:
        conn = _open_connection(db_path, readonly=True)
    except sqlite3.Error as e:
        return f"Database could not be reopened for verification: {e}"

    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    except sqlite3.Error as e:
        return f"Database table list could not be read: {e}"
    finally:
        conn.close()

    if 'schema_version' in tables:
        return (
            "Database has a schema_version table but no readable version row. "
            "That is damage, not an un-versioned database, and rebuilding would "
            "discard the current contents. Refusing to migrate."
        )

    user_tables = tables - {'schema_version'}
    if user_tables:
        return (
            f"Database has {len(user_tables)} tables but no schema_version "
            "table. AIMFP schemas have always created one, so this is not an "
            "old database. Refusing to migrate — inspect it first."
        )

    return None


def _check_pending_migrations(project_root: str, aimfp_folder: str) -> Dict[str, Any]:
    """
    Internal: Check which project databases need migration.

    Reads expected_schema_versions from aimfp_core.db, compares against
    actual versions in user's databases.

    Args:
        project_root: Absolute path to project root
        aimfp_folder: AIMFP project directory name (e.g., '.aimfp-project').
            The default name is re-rooted onto the root's actual project
            folder (resolve_project_relative), so a project-dir override holds.

    Returns:
        {
            checked: bool,
            pending: [{db_name, current_version, expected_version, db_path}],
            up_to_date: [{db_name, version}],
            skipped: [{db_name, reason}]
        }
    """
    core_db_path = get_core_db_path()

    if not database_exists(core_db_path):
        return {
            'checked': False,
            'pending': [],
            'up_to_date': [],
            'skipped': [],
            'error': 'aimfp_core.db not found',
        }

    # Read expected versions from core DB
    try:
        conn = _open_connection(core_db_path, immutable=True)
        cursor = conn.execute("SELECT db_name, expected_version, minimum_version FROM expected_schema_versions")
        expected_rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        return {
            'checked': False,
            'pending': [],
            'up_to_date': [],
            'skipped': [],
            'error': f'Failed to read expected_schema_versions: {str(e)}',
        }

    if not expected_rows:
        return {
            'checked': True,
            'pending': [],
            'up_to_date': [],
            'skipped': [],
        }

    aimfp_dir = resolve_project_relative(project_root, aimfp_folder)
    pending = []
    up_to_date = []
    skipped = []

    for row in expected_rows:
        db_name = row['db_name']
        expected_version = row['expected_version']

        config = _DB_CONFIG.get(db_name)
        if not config:
            skipped.append({'db_name': db_name, 'reason': f'Unknown database: {db_name}'})
            continue

        db_path = os.path.join(aimfp_dir, config['db_file'])

        if not os.path.isfile(db_path):
            skipped.append({'db_name': db_name, 'reason': 'Database file does not exist'})
            continue

        try:
            current_version = _get_db_version(db_path)
        except sqlite3.Error as e:
            # Unreadable is not un-versioned. Never report a database whose
            # version could not be read as pending — that routes it into the
            # rebuild path in migrate_databases.
            skipped.append({
                'db_name': db_name,
                'reason': f'Schema version unreadable, migration status unknown: {e}',
            })
            continue

        if current_version == expected_version:
            up_to_date.append({'db_name': db_name, 'version': current_version})
        else:
            pending.append({
                'db_name': db_name,
                'current_version': current_version,
                'expected_version': expected_version,
                'db_path': db_path,
            })

    return {
        'checked': True,
        'pending': pending,
        'up_to_date': up_to_date,
        'skipped': skipped,
    }


# ============================================================================
# Public Embedding API
# ============================================================================

def check_pending_migrations(project_root: Optional[str] = None) -> Result:
    """
    Effect: Check which project databases need schema migration.

    Public embedding API — the same read-only check aimfp_run performs on a
    new session, exposed à la carte. Applying migrations remains the job of
    migrate_databases.

    Args:
        project_root: Explicit root for embedding hosts; defaults to the
            cached/discovered session root

    Returns:
        Result with data={checked, pending, up_to_date, skipped}
        (the _check_pending_migrations payload)
    """
    try:
        root = project_root or resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))
    return Result(
        success=True,
        data=_check_pending_migrations(root, AIMFP_PROJECT_DIR),
    )


# ============================================================================
# MCP Tool: migrate_databases
# ============================================================================

def _get_shared_columns(conn: sqlite3.Connection, table: str, old_db_alias: str) -> List[str]:
    """
    Effect: Get column names that exist in both old and new versions of a table.

    Args:
        conn: Connection to new database (with old attached as old_db_alias)
        table: Table name
        old_db_alias: ATTACH alias for old database

    Returns:
        List of shared column names
    """
    # Get new DB columns
    cursor = conn.execute(f"PRAGMA table_info({table})")
    new_cols = {row[1] for row in cursor.fetchall()}

    # Get old DB columns
    cursor = conn.execute(f"PRAGMA {old_db_alias}.table_info({table})")
    old_cols = {row[1] for row in cursor.fetchall()}

    return sorted(new_cols & old_cols)


def _get_old_table_names(conn: sqlite3.Connection, old_db_alias: str) -> List[str]:
    """
    Effect: Get table names from the old attached database.

    Args:
        conn: Connection with old DB attached
        old_db_alias: ATTACH alias

    Returns:
        List of table names (excludes sqlite internals)
    """
    cursor = conn.execute(
        f"SELECT name FROM {old_db_alias}.sqlite_master "
        f"WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return [row[0] for row in cursor.fetchall()]


def pre_migration_backup_name(db_name: str, old_version: str, stamp: str, taken: FrozenSet[str]) -> str:
    """
    Pure: File name for a database's pre-migration copy in backups/:
    <db>_pre_migration_v<old>_<YYYYMMDD>.db, suffixed -2, -3 ... when a copy
    of that name is already there (never overwrites an earlier copy).
    """
    base = f"{db_name}_pre_migration_v{old_version}_{stamp}"
    candidates = (f"{base}.db",) + tuple(f"{base}-{n}.db" for n in range(2, len(taken) + 3))
    return next(name for name in candidates if name not in taken)


def verification_mismatches(verification: Dict[str, Dict[str, Any]]) -> List[str]:
    """Pure: Tables whose row count changed in the rebuild (old -> new)."""
    return [
        f"{table}: {counts['old_rows']} -> {counts['new_rows']}"
        for table, counts in sorted(verification.items()) if not counts['match']
    ]


def _effect_rebuild_into(new_path: str, old_db_path: str, schema_sql: str) -> Dict[str, Any]:
    """
    Effect: Create a database at new_path from schema_sql and copy every shared
    column of every old table into it via ATTACH.

    Returns:
        {verification: {table: {old_rows, new_rows, match}}, warnings: [..]}
    """
    # DELIBERATELY NOT _open_connection. This is a freshly created,
    # exclusively-owned file that ATTACHes the old database and sets
    # PRAGMA foreign_keys = OFF two lines below — an opener that forces
    # foreign_keys = ON would conflict with the rebuild. It stays in rollback
    # journal mode, so swapping it in leaves no WAL sidecars behind.
    new_conn = sqlite3.connect(new_path)
    try:
        new_conn.row_factory = sqlite3.Row
        new_conn.executescript(schema_sql)
        new_conn.execute("ATTACH DATABASE ? AS old_db", (old_db_path,))
        new_conn.execute("PRAGMA foreign_keys = OFF")

        old_tables = _get_old_table_names(new_conn, 'old_db')
        new_tables = set(_get_table_names(new_conn))
        verification: Dict[str, Dict[str, Any]] = {}
        warnings: List[str] = []

        for table in old_tables:
            if table == 'schema_version' or '_fts' in table:
                # New DB already has the right version; FTS5 tables are
                # repopulated by triggers
                continue
            if table not in new_tables:
                warnings.append(f"Table '{table}' exists in old DB but not in new schema — skipped")
                continue
            shared_cols = _get_shared_columns(new_conn, table, 'old_db')
            if not shared_cols:
                warnings.append(f"Table '{table}' has no shared columns — skipped")
                continue
            cols_str = ", ".join(shared_cols)
            old_count = new_conn.execute(f"SELECT COUNT(*) FROM old_db.{table}").fetchone()[0]
            new_conn.execute(
                f"INSERT OR IGNORE INTO {table} ({cols_str}) SELECT {cols_str} FROM old_db.{table}"
            )
            new_count = new_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            verification[table] = {'old_rows': old_count, 'new_rows': new_count, 'match': old_count == new_count}

        new_conn.commit()
        new_conn.execute("DETACH DATABASE old_db")
        return {'verification': verification, 'warnings': warnings}
    finally:
        new_conn.close()


def _effect_remove_quietly(path: Optional[str]) -> None:
    """Effect: Delete a file if it exists, ignoring errors."""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _effect_migrate_one(db_info: Dict[str, Any], stamp: str) -> Dict[str, Any]:
    """
    Effect: Migrate one database in place.

    1. Take the database out of WAL mode (journal_mode=DELETE). That needs
       exclusive access, so it proves no other connection has the file open,
       and it folds the WAL into the main file and removes the -wal/-shm
       sidecars a swapped-in file must never inherit.
    2. Hold a write lock (BEGIN IMMEDIATE) from here until the swap: other
       writers wait, so nothing commits to a file about to be replaced.
    3. Copy it to backups/<db>_pre_migration_v<old>_<date>.db (SQLite backup
       API through a separate read connection: a consistent copy).
    4. Rebuild into a temp file in the same folder and verify row counts.
    5. Counts match: os.replace the temp file over the original (atomic on one
       filesystem). Mismatch: remove the temp file, leave the original alone.

    Returns:
        The migrated[] entry (placed, backup_path, verification, ...)
    """
    db_name = db_info['db_name']
    old_db_path = db_info['db_path']
    old_version = db_info['current_version']
    entry: Dict[str, Any] = {
        'db_name': db_name, 'old_version': old_version, 'new_version': None,
        'placed': False, 'backup_path': None, 'original_db_path': old_db_path,
        'verification': {}, 'warnings': [],
    }
    folder = os.path.dirname(old_db_path)
    backups_dir = os.path.join(folder, 'backups')
    new_path = os.path.join(folder, f".{os.path.basename(old_db_path)}.migrating")
    _effect_remove_quietly(new_path)

    guard = sqlite3.connect(old_db_path, timeout=DEFAULT_BUSY_TIMEOUT, isolation_level=None)
    try:
        try:
            mode = guard.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        except sqlite3.OperationalError:
            mode = 'locked'
        if str(mode).lower() != 'delete':
            return {**entry, 'error': (
                f"{db_name}.db is open in another connection (journal mode stayed '{mode}'): "
                "close other AIMFP sessions and stop the watchdog, then run migrate_databases again. "
                "Nothing was changed.")}
        guard.execute("BEGIN IMMEDIATE")

        os.makedirs(backups_dir, exist_ok=True)
        backup_path = os.path.join(backups_dir, pre_migration_backup_name(
            db_name, old_version, stamp, frozenset(os.listdir(backups_dir))))
        # The backup reads through its own connection: a read lock coexists
        # with the guard's write lock, while backing up from the guard itself
        # would wait on its own open transaction forever.
        reader = sqlite3.connect(old_db_path, timeout=DEFAULT_BUSY_TIMEOUT)
        backup_conn = sqlite3.connect(backup_path)
        try:
            reader.backup(backup_conn)
        finally:
            backup_conn.close()
            reader.close()
        entry = {**entry, 'backup_path': backup_path}

        with open(_get_schema_path(_DB_CONFIG[db_name]['schema_file']), 'r') as f:
            rebuilt = _effect_rebuild_into(new_path, old_db_path, f.read())
        entry = {**entry, **rebuilt, 'new_version': _get_db_version(new_path)}

        mismatches = verification_mismatches(rebuilt['verification'])
        if mismatches:
            _effect_remove_quietly(new_path)
            return {**entry, 'new_version': None, 'error': (
                "Row counts changed in the rebuild, so the new database was NOT placed and the "
                f"original is untouched: {'; '.join(mismatches)}. The pre-migration copy is at backup_path.")}

        try:
            os.replace(new_path, old_db_path)
        except PermissionError:
            # Windows refuses to replace a file that is open: release the
            # guard (the lock window closes for an instant) and swap.
            guard.execute("ROLLBACK")
            guard.close()
            os.replace(new_path, old_db_path)
        return {**entry, 'placed': True}
    except Exception as e:
        _effect_remove_quietly(new_path)
        return {**entry, 'new_version': None, 'placed': False,
                'error': f"{e} — the original database is untouched"}
    finally:
        try:
            guard.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        guard.close()


def migrate_databases(
    aimfp_folder: str = ".aimfp-project"
) -> Result:
    """
    Migrate all pending project databases to current schema versions, in place.

    For each pending DB (_effect_migrate_one): checks nothing else has it open,
    write-locks it, copies it to <project folder>/backups/
    <db>_pre_migration_v<old>_<YYYYMMDD>.db, rebuilds it from the current
    schema SQL (shared columns via ATTACH), verifies row counts and atomically
    swaps the rebuilt file in. A count mismatch or any error leaves the
    original untouched. Nothing is left in temp folders.

    Args:
        aimfp_folder: AIMFP project directory name (default '.aimfp-project',
            which resolves to the project-dir override when one is set)

    Returns:
        Result with data={migrated: [{db_name, old_version, new_version,
        placed, backup_path, original_db_path, verification, warnings,
        error?}], not_placed: [db_name], skipped, already_current}
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    migration_check = _check_pending_migrations(project_root, aimfp_folder)

    if not migration_check.get('checked'):
        return Result(
            success=False,
            error=f"Migration check failed: {migration_check.get('error', 'unknown')}",
        )

    pending = migration_check['pending']

    if not pending:
        current = {
            'migrated': [],
            'not_placed': [],
            'skipped': migration_check['skipped'],
            'already_current': migration_check['up_to_date'],
            'message': 'All databases up to date',
        }
        return Result(
            success=True,
            data=current,
            return_statements=gate_return_statements(get_return_statements("migrate_databases"), current),
        )

    stamp = datetime.now().strftime('%Y%m%d')
    migrated = []
    skipped = list(migration_check['skipped'])

    for db_info in pending:
        # Last gate before a destructive rebuild: a database reporting '0.0'
        # must actually look un-versioned, not merely unreadable.
        if db_info['current_version'] == PRE_VERSIONING_VERSION:
            refusal = _impossible_version_reason(db_info['db_path'])
            if refusal:
                skipped.append({'db_name': db_info['db_name'], 'reason': refusal})
                continue
        migrated.append(_effect_migrate_one(db_info, stamp))

    data = {
        'migrated': migrated,
        'not_placed': [m['db_name'] for m in migrated if not m['placed']],
        'skipped': skipped,
        'already_current': migration_check['up_to_date'],
    }
    return Result(
        success=True,
        data=data,
        return_statements=gate_return_statements(get_return_statements("migrate_databases"), data),
    )
