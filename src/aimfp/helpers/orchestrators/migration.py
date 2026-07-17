"""
AIMFP Helper Functions - Database Migration

Handles schema migrations for project databases (project.db, user_preferences.db,
user_directives.db). Core DB is read-only and ships with updated schemas — no
migrations needed.

Migration approach: no migration files. Creates fresh DBs from new schema SQL,
transfers data from old DBs via ATTACH, returns temp paths for AI to verify
and place.

Functions:
- _check_pending_migrations: Internal checker (not a tool)
- migrate_databases: MCP tool — performs all pending migrations
"""

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from ._common import (
    get_core_db_path,
    get_aimfp_project_dir,
    resolve_project_root,
    database_exists,
    _get_table_names,
    get_return_statements,
    Result,
    AIMFP_PROJECT_DIR,
)


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


def _get_db_version(db_path: str) -> str:
    """Effect: Read schema_version from a database. Returns '0.0' if unreadable."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT version FROM schema_version WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        return row['version'] if row else '0.0'
    except Exception:
        return '0.0'


def _check_pending_migrations(project_root: str, aimfp_folder: str) -> Dict[str, Any]:
    """
    Internal: Check which project databases need migration.

    Reads expected_schema_versions from aimfp_core.db, compares against
    actual versions in user's databases.

    Args:
        project_root: Absolute path to project root
        aimfp_folder: AIMFP project directory name (e.g., '.aimfp-project')

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
        conn = sqlite3.connect(core_db_path)
        conn.row_factory = sqlite3.Row
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

    aimfp_dir = os.path.join(project_root, aimfp_folder)
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

        current_version = _get_db_version(db_path)

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


def migrate_databases(
    aimfp_folder: str = ".aimfp-project"
) -> Result:
    """
    Migrate all pending project databases to current schema versions.

    For each pending DB:
    1. Backs up old DB to temp file
    2. Creates new DB from current schema SQL
    3. Transfers data via ATTACH (shared columns only)
    4. Verifies row counts
    5. Returns temp paths for AI to verify and place

    Args:
        aimfp_folder: AIMFP project directory name (default '.aimfp-project')

    Returns:
        Result with migrated DB temp paths and verification data
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
        return Result(
            success=True,
            data={
                'migrated': [],
                'skipped': migration_check['skipped'],
                'already_current': migration_check['up_to_date'],
                'message': 'All databases up to date',
            },
            return_statements=get_return_statements("migrate_databases"),
        )

    migrated = []

    for db_info in pending:
        db_name = db_info['db_name']
        old_db_path = db_info['db_path']
        old_version = db_info['current_version']
        expected_version = db_info['expected_version']

        config = _DB_CONFIG[db_name]
        schema_file = config['schema_file']

        try:
            # Step 1: Backup old DB to temp file
            backup_fd, backup_temp_path = tempfile.mkstemp(
                suffix='.db', prefix=f'aimfp_backup_{db_name}_'
            )
            os.close(backup_fd)
            shutil.copy2(old_db_path, backup_temp_path)

            # Step 2: Create new DB from schema SQL
            new_fd, new_temp_path = tempfile.mkstemp(
                suffix='.db', prefix=f'aimfp_new_{db_name}_'
            )
            os.close(new_fd)

            schema_path = _get_schema_path(schema_file)
            with open(schema_path, 'r') as f:
                schema_sql = f.read()

            new_conn = sqlite3.connect(new_temp_path)
            new_conn.row_factory = sqlite3.Row
            new_conn.executescript(schema_sql)

            # Step 3: Transfer data via ATTACH
            new_conn.execute(f"ATTACH DATABASE '{old_db_path}' AS old_db")
            new_conn.execute("PRAGMA foreign_keys = OFF")

            old_tables = _get_old_table_names(new_conn, 'old_db')
            new_tables = set(_get_table_names(new_conn))

            verification = {}
            warnings = []

            for table in old_tables:
                if table == 'schema_version':
                    # Skip — new DB already has correct version
                    continue

                if '_fts' in table:
                    # Skip FTS5 virtual tables and their internal tables — auto-populated by triggers
                    continue

                if table not in new_tables:
                    warnings.append(f"Table '{table}' exists in old DB but not in new schema — skipped")
                    continue

                shared_cols = _get_shared_columns(new_conn, table, 'old_db')
                if not shared_cols:
                    warnings.append(f"Table '{table}' has no shared columns — skipped")
                    continue

                cols_str = ", ".join(shared_cols)

                # Count old rows
                cursor = new_conn.execute(f"SELECT COUNT(*) FROM old_db.{table}")
                old_count = cursor.fetchone()[0]

                # Transfer data
                new_conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({cols_str}) "
                    f"SELECT {cols_str} FROM old_db.{table}"
                )

                # Count new rows
                cursor = new_conn.execute(f"SELECT COUNT(*) FROM {table}")
                new_count = cursor.fetchone()[0]

                verification[table] = {
                    'old_rows': old_count,
                    'new_rows': new_count,
                    'match': old_count == new_count,
                }

            new_conn.commit()
            new_conn.execute("DETACH DATABASE old_db")
            new_conn.close()

            # Step 4: Read new version
            new_version = _get_db_version(new_temp_path)

            migrated.append({
                'db_name': db_name,
                'old_version': old_version,
                'new_version': new_version,
                'backup_temp_path': backup_temp_path,
                'new_db_temp_path': new_temp_path,
                'original_db_path': old_db_path,
                'verification': verification,
                'warnings': warnings,
            })

        except Exception as e:
            # Clean up temp files on failure for this DB
            for path in [backup_temp_path, new_temp_path]:
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    pass

            migrated.append({
                'db_name': db_name,
                'old_version': old_version,
                'new_version': None,
                'error': str(e),
                'backup_temp_path': None,
                'new_db_temp_path': None,
                'original_db_path': old_db_path,
                'verification': {},
                'warnings': [],
            })

    return Result(
        success=True,
        data={
            'migrated': migrated,
            'skipped': migration_check['skipped'],
            'already_current': migration_check['up_to_date'],
        },
        return_statements=get_return_statements("migrate_databases"),
    )
