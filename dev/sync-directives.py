#!/usr/bin/env python3
"""
AIMFP Directive Sync Manager — Schema v2.0
------------------------------------------
Synchronizes all directive JSON definitions, helper functions, and directive flows
with the aimfp_core.db database.

Location: dev/sync-directives.py
Run from project root or dev/ directory.

Handles:
- Directives (FP Core + FP Aux + Project + User Preferences + User System + Git)
- Categories (from directive JSON) → categories table → directive_categories junction
- Intent Keywords (from directive JSON) → intent_keywords table → directives_intent_keywords junction
- Helper Functions (from dev/helpers-json/*.json) → helper_functions table
- Directive-Helper Mappings (from helper's used_by_directives field) → directive_helpers junction
- Directive Flows (from directive_flow_*.json) → directive_flow table
- Parent relationships
- Integrity Validation (post-sync verification)

Updated: 2026-02-04
- Relocated to dev/ directory (permanent dev staging area)
- Updated paths: dev/directives-json/, dev/helpers-json/, dev/logs/
- All paths now use __file__ based resolution for reliability

Total Directives: 125+ (30 FP Core + 36 FP Aux + 36 Project + 7 User Pref + 9 User System + 6 Git + ...)

This version aligns with the full schema (v2.0) for aimfp_core.db.
"""

import os
import re
import sys
import json
import sqlite3
from typing import List, Dict, Any, Set, Tuple
from pathlib import Path

# ===================================
# PATH RESOLUTION (based on script location)
# ===================================

# Script location: dev/sync-directives.py
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)  # One level up from dev/

# ===================================
# CONFIGURATION
# ===================================

# Database path - configurable via environment variable
# Default: src/aimfp/database/aimfp_core.db (production location)
# Override with AIMFP_CORE_DB_PATH environment variable if needed
DB_PATH = os.environ.get(
    "AIMFP_CORE_DB_PATH",
    os.path.join(PROJECT_ROOT, "src", "aimfp", "database", "aimfp_core.db")
)

# Directive JSON files are in dev/directives-json/
DIRECTIVES_JSON_DIR = os.path.join(SCRIPT_DIR, "directives-json")

FP_DIRECTIVE_FILES = [
    "directives-fp-core.json",
    "directives-fp-aux.json"
]

PROJECT_DIRECTIVE_FILES = [
    "directives-project.json"
]

USER_PREFERENCE_FILES = [
    "directives-user-pref.json"
]

USER_SYSTEM_FILES = [
    "directives-user-system.json"
]

GIT_DIRECTIVE_FILES = [
    "directives-git.json"
]

# Helpers are in dev/helpers-json/
HELPERS_DIR = os.path.join(SCRIPT_DIR, "helpers-json")

# Directive flows are in dev/directives-json/
DIRECTIVE_FLOW_FILES = [
    "directive_flow_fp.json",
    "directive_flow_project.json",
    "directive_flow_user_preferences.json"
]

# One-time system notices in dev/notices-json/
NOTICES_DIR = os.path.join(SCRIPT_DIR, "notices-json")

# Migrations in dev/migrations/
MIGRATIONS_DIR = os.path.join(SCRIPT_DIR, "migrations")

# Sync report in dev/logs/
SYNC_REPORT_FILE = os.path.join(SCRIPT_DIR, "logs", "sync_report.json")

CORE_SCHEMA_SQL = os.path.join(
    PROJECT_ROOT, "src", "aimfp", "database", "schemas", "aimfp_core.sql"
)


def _read_schema_version_from_sql() -> str:
    """
    Read the core schema version straight from aimfp_core.sql.

    ensure_schema() already treats that file as the source of truth, so the
    version is derived from it rather than restated here. A hardcoded copy
    silently drifts the moment the SQL is bumped without this file — and the
    tail of run_migrations() would then stamp the DB back to the stale value.
    """
    with open(CORE_SCHEMA_SQL, 'r', encoding='utf-8') as f:
        sql = f.read()

    match = re.search(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+schema_version\s*\([^)]*\)\s*VALUES\s*\(\s*1\s*,\s*'([^']+)'\s*\)",
        sql,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            f"Could not find a schema_version seed in {CORE_SCHEMA_SQL}. "
            f"Expected: INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, 'X.Y');"
        )
    return match.group(1)


CURRENT_SCHEMA_VERSION = _read_schema_version_from_sql()
DRY_RUN = False


# ===================================
# DB CONNECTION & SCHEMA CHECK
# ===================================

def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection):
    """Load and execute schema from external SQL file (source of truth)."""
    schema_path = os.path.join(PROJECT_ROOT, 'src', 'aimfp', 'database', 'schemas', 'aimfp_core.sql')

    if not os.path.exists(schema_path):
        print(f"❌ Schema file not found: {schema_path}")
        print(f"   Please ensure src/aimfp/database/schemas/aimfp_core.sql exists")
        raise FileNotFoundError(f"Schema file missing: {schema_path}")

    print(f"📄 Loading schema from: {schema_path}")

    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()

    conn.executescript(schema_sql)
    conn.commit()
    print("✅ Schema loaded successfully")


# ===================================
# MIGRATION SYSTEM
# ===================================

def get_current_db_version(conn: sqlite3.Connection) -> str:
    """Get current schema version from database."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT version FROM schema_version WHERE id = 1")
        row = cur.fetchone()
        return row['version'] if row else "1.0"
    except sqlite3.OperationalError:
        # schema_version table doesn't exist yet
        return "1.0"


def set_db_version(conn: sqlite3.Connection, version: str):
    """Update schema version in database."""
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO schema_version (id, version, updated_at)
        VALUES (1, ?, CURRENT_TIMESTAMP)
    """, (version,))
    conn.commit()


def get_migration_files() -> List[tuple]:
    """
    Get all migration files from migrations directory.
    Returns list of (version, filepath) tuples sorted by version.

    Migration files should be named: migration_1.2_to_1.3.sql
    """
    if not os.path.exists(MIGRATIONS_DIR):
        return []

    migrations = []
    for filename in os.listdir(MIGRATIONS_DIR):
        if filename.endswith('.sql') and filename.startswith('migration_'):
            # Parse version from filename: migration_1.2_to_1.3.sql -> 1.3
            try:
                parts = filename.replace('.sql', '').split('_to_')
                if len(parts) == 2:
                    target_version = parts[1]
                    migrations.append((target_version, os.path.join(MIGRATIONS_DIR, filename)))
            except Exception:
                print(f"⚠️  Could not parse migration filename: {filename}")

    # Sort by version
    migrations.sort(key=lambda x: [int(n) for n in x[0].split('.')])
    return migrations


def run_migrations(conn: sqlite3.Connection):
    """
    Run all pending migrations to bring database to current schema version.
    Ensures backward compatibility when schema changes.
    """
    current_version = get_current_db_version(conn)
    print(f"📊 Current database schema version: {current_version}")

    if current_version == CURRENT_SCHEMA_VERSION:
        print(f"✅ Database already at current version ({CURRENT_SCHEMA_VERSION})")
        return

    print(f"🔄 Migrating database from {current_version} to {CURRENT_SCHEMA_VERSION}")

    migrations = get_migration_files()
    if not migrations:
        print("⚠️  No migration files found. Using ensure_schema() for initialization.")
        return

    # Run migrations that are newer than current version
    for target_version, migration_file in migrations:
        # Compare versions
        current_parts = [int(n) for n in current_version.split('.')]
        target_parts = [int(n) for n in target_version.split('.')]

        # Only run if target > current
        if target_parts > current_parts:
            print(f"  → Applying migration to {target_version}: {os.path.basename(migration_file)}")

            try:
                with open(migration_file, 'r', encoding='utf-8') as f:
                    migration_sql = f.read()

                conn.executescript(migration_sql)
                set_db_version(conn, target_version)
                print(f"  ✅ Migration to {target_version} complete")

            except Exception as e:
                print(f"  ❌ Migration to {target_version} failed: {e}")
                if not DRY_RUN:
                    conn.rollback()
                raise

    # Update to current version — never downgrade. ensure_schema() has already
    # seeded the version from the SQL; if the DB sits ahead of the target, that
    # is the newer truth and stamping it back would fake a pending migration.
    final_version = get_current_db_version(conn)
    if final_version != CURRENT_SCHEMA_VERSION:
        if [int(n) for n in final_version.split('.')] > [int(n) for n in CURRENT_SCHEMA_VERSION.split('.')]:
            print(f"⚠️  Database at {final_version} is ahead of target {CURRENT_SCHEMA_VERSION} — leaving as is")
            return
        set_db_version(conn, CURRENT_SCHEMA_VERSION)

    print(f"✅ Database migrated to version {CURRENT_SCHEMA_VERSION}")


# ===================================
# CORE HELPERS
# ===================================

def load_json_file(filepath: str) -> List[Dict[str, Any]]:
    if not os.path.exists(filepath):
        print(f"⚠️  Missing file: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            # Handle different JSON structures:
            # - Direct array of directives: [...]
            # - Wrapper object with 'directives' key: {"directives": [...], ...}
            # - Wrapper object with 'helpers' key: {"helpers": [...], ...}
            # - Wrapper object with 'flows' key: {"flows": [...], ...}
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                if 'directives' in data:
                    return data['directives']
                elif 'helpers' in data:
                    return data['helpers']
                elif 'flows' in data:
                    return data['flows']
                else:
                    return [data]
            else:
                return []
        except Exception as e:
            print(f"❌ Error parsing {filepath}: {e}")
            return []


# ===================================
# CATEGORY MANAGEMENT
# ===================================

def extract_categories_from_directives(all_entries: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """
    Extract unique categories from directive entries.
    Returns set of (name, description) tuples.
    """
    categories = set()
    for entry in all_entries:
        if "category" in entry and entry["category"]:
            cat = entry["category"]
            if isinstance(cat, dict):
                name = cat.get("name", "")
                description = cat.get("description", "")
                if name:
                    categories.add((name, description))
    return categories


def sync_categories(conn: sqlite3.Connection, categories: Set[Tuple[str, str]]) -> Dict[str, int]:
    """
    Insert categories into categories table.
    Returns mapping of category_name -> category_id.
    """
    print(f"\n📂 Syncing {len(categories)} categories...")

    cur = conn.cursor()
    category_id_map = {}
    inserted = 0

    # Sort categories alphabetically by name for consistent ordering
    for name, description in sorted(categories, key=lambda x: x[0]):
        # Insert or ignore
        cur.execute("""
            INSERT OR IGNORE INTO categories (name, description)
            VALUES (?, ?)
        """, (name, description))

        # Get the id
        cur.execute("SELECT id FROM categories WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            category_id_map[name] = row['id']
            if cur.rowcount > 0:
                inserted += 1

    conn.commit()
    print(f"✅ Categories synced: {inserted} new, {len(categories) - inserted} existing")
    return category_id_map


def link_directive_categories(conn: sqlite3.Connection, all_entries: List[Dict[str, Any]],
                               category_id_map: Dict[str, int]):
    """
    Link directives to categories via directive_categories junction table.
    """
    print(f"\n🔗 Linking directives to categories...")

    cur = conn.cursor()
    linked = 0

    for entry in all_entries:
        if "category" not in entry or not entry["category"]:
            continue

        directive_name = entry.get("name")
        if not directive_name:
            continue

        # Get directive_id
        cur.execute("SELECT id FROM directives WHERE name = ?", (directive_name,))
        directive_row = cur.fetchone()
        if not directive_row:
            continue

        directive_id = directive_row['id']

        # Get category_id
        cat = entry["category"]
        if isinstance(cat, dict):
            cat_name = cat.get("name", "")
            if cat_name in category_id_map:
                category_id = category_id_map[cat_name]

                # Insert link (ignore if exists)
                cur.execute("""
                    INSERT OR IGNORE INTO directive_categories (directive_id, category_id)
                    VALUES (?, ?)
                """, (directive_id, category_id))

                if cur.rowcount > 0:
                    linked += 1

    conn.commit()
    print(f"✅ Linked {linked} directive-category relationships")


# ===================================
# INTENT KEYWORD MANAGEMENT
# ===================================

def extract_intent_keywords_from_directives(all_entries: List[Dict[str, Any]]) -> Set[str]:
    """
    Extract unique intent keywords from directive entries.
    Returns set of keywords.
    """
    keywords = set()
    for entry in all_entries:
        if "intent_keywords_json" in entry and entry["intent_keywords_json"]:
            kw_list = entry["intent_keywords_json"]
            if isinstance(kw_list, list):
                for kw in kw_list:
                    if isinstance(kw, str) and kw.strip():
                        keywords.add(kw.strip().lower())
    return keywords


def sync_intent_keywords(conn: sqlite3.Connection, keywords: Set[str]) -> Dict[str, int]:
    """
    Insert intent keywords into intent_keywords table.
    Returns mapping of keyword -> keyword_id.
    """
    print(f"\n🔑 Syncing {len(keywords)} intent keywords...")

    cur = conn.cursor()
    keyword_id_map = {}
    inserted = 0

    # Sort keywords alphabetically for consistent ordering
    for keyword in sorted(keywords):
        # Insert or ignore
        cur.execute("""
            INSERT OR IGNORE INTO intent_keywords (keyword)
            VALUES (?)
        """, (keyword,))

        # Get the id
        cur.execute("SELECT id FROM intent_keywords WHERE keyword = ?", (keyword,))
        row = cur.fetchone()
        if row:
            keyword_id_map[keyword] = row['id']
            if cur.rowcount > 0:
                inserted += 1

    conn.commit()
    print(f"✅ Intent keywords synced: {inserted} new, {len(keywords) - inserted} existing")
    return keyword_id_map


def link_directive_intent_keywords(conn: sqlite3.Connection, all_entries: List[Dict[str, Any]],
                                   keyword_id_map: Dict[str, int]):
    """
    Link directives to intent keywords via directives_intent_keywords junction table.
    """
    print(f"\n🔗 Linking directives to intent keywords...")

    cur = conn.cursor()
    linked = 0

    for entry in all_entries:
        if "intent_keywords_json" not in entry or not entry["intent_keywords_json"]:
            continue

        directive_name = entry.get("name")
        if not directive_name:
            continue

        # Get directive_id
        cur.execute("SELECT id FROM directives WHERE name = ?", (directive_name,))
        directive_row = cur.fetchone()
        if not directive_row:
            continue

        directive_id = directive_row['id']

        # Link keywords
        kw_list = entry["intent_keywords_json"]
        if isinstance(kw_list, list):
            for kw in kw_list:
                if isinstance(kw, str) and kw.strip():
                    kw_normalized = kw.strip().lower()
                    if kw_normalized in keyword_id_map:
                        keyword_id = keyword_id_map[kw_normalized]

                        # Insert link (ignore if exists)
                        cur.execute("""
                            INSERT OR IGNORE INTO directives_intent_keywords (directive_id, keyword_id)
                            VALUES (?, ?)
                        """, (directive_id, keyword_id))

                        if cur.rowcount > 0:
                            linked += 1

    conn.commit()
    print(f"✅ Linked {linked} directive-keyword relationships")


# ===================================
# INSERT / UPDATE FUNCTIONS
# ===================================

def upsert_directive(conn, entry: Dict[str, Any]) -> str:
    cur = conn.cursor()
    cur.execute("SELECT id FROM directives WHERE name=?", (entry["name"],))
    existing = cur.fetchone()

    # Note: intent_keywords_json and category are NOT in directives table anymore
    # They are in separate tables with junction tables
    fields = (
        entry["name"],
        entry["type"],
        entry.get("level"),
        entry.get("parent_directive"),
        entry.get("description"),
        json.dumps(entry.get("workflow", {})),
        entry.get("md_file_path"),
        json.dumps(entry.get("roadblocks_json", [])),
        entry.get("confidence_threshold", 0.5)
    )

    if existing:
        cur.execute("""
            UPDATE directives SET
                type=?, level=?, parent_directive=?, description=?, workflow=?,
                md_file_path=?, roadblocks_json=?,
                confidence_threshold=?
            WHERE name=?
        """, fields[1:] + (entry["name"],))
        conn.commit()
        return "updated"
    else:
        cur.execute("""
            INSERT INTO directives
            (name, type, level, parent_directive, description, workflow,
             md_file_path, roadblocks_json, confidence_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, fields)
        conn.commit()
        return "added"


# ===================================
# HELPER FUNCTIONS SYNC
# ===================================

def load_all_helper_files() -> List[Dict[str, Any]]:
    """
    Load all helper JSON files from dev/helpers-json/ directory.
    Returns combined list of all helpers.
    """
    if not os.path.exists(HELPERS_DIR):
        print(f"⚠️  Helpers directory not found: {HELPERS_DIR}")
        return []

    all_helpers = []
    helper_files = sorted(Path(HELPERS_DIR).glob("helpers-*.json"))

    print(f"\n📚 Loading helper files from {HELPERS_DIR}")
    for helper_file in helper_files:
        helpers = load_json_file(str(helper_file))
        if helpers:
            all_helpers.extend(helpers)
            print(f"   📘 Loaded {len(helpers)} helpers from {helper_file.name}")

    return all_helpers


def sync_helper_functions(conn: sqlite3.Connection) -> int:
    """
    Load helper functions from all JSON files in dev/helpers-json/ and populate helper_functions table.

    Populates table with complete helper data:
    - name, file_path, parameters, purpose, error_handling
    - is_tool: Read from JSON (TRUE if exposed as MCP tool)
    - is_sub_helper: Read from JSON (TRUE if internal helper only)
    - target_database: Read from JSON
    - return_statements: Read from JSON

    Source: dev/helpers-json/helpers-*.json
    """
    print("\n🔧 Syncing helper functions from JSON files...")

    helpers = load_all_helper_files()

    if not helpers:
        print("⚠️  No helpers found in JSON files")
        return 0

    print(f"📋 Processing {len(helpers)} helper functions")

    # Insert helper functions into table
    cur = conn.cursor()
    inserted = 0
    updated = 0

    for helper in helpers:
        name = helper.get('name')
        if not name:
            continue

        # Check if helper already exists
        cur.execute("SELECT id FROM helper_functions WHERE name=?", (name,))
        existing = cur.fetchone()

        # Convert parameters to JSON string if it's a list
        parameters = helper.get('parameters')
        if isinstance(parameters, list):
            parameters = json.dumps(parameters)
        elif isinstance(parameters, str):
            parameters = parameters  # Already JSON string
        else:
            parameters = json.dumps([])

        # Convert return_statements to JSON string if it's a list
        return_statements = helper.get('return_statements')
        if isinstance(return_statements, list):
            return_statements = json.dumps(return_statements)
        elif isinstance(return_statements, str):
            return_statements = return_statements
        else:
            return_statements = json.dumps([])

        if existing:
            # Update existing helper with latest data from JSON
            cur.execute("""
                UPDATE helper_functions
                SET file_path = ?,
                    parameters = ?,
                    purpose = ?,
                    error_handling = ?,
                    is_tool = ?,
                    is_sub_helper = ?,
                    return_statements = ?,
                    target_database = ?
                WHERE name = ?
            """, (
                helper.get('file_path'),
                parameters,
                helper.get('purpose'),
                helper.get('error_handling'),
                1 if helper.get('is_tool', False) else 0,
                1 if helper.get('is_sub_helper', False) else 0,
                return_statements,
                helper.get('target_database'),
                name
            ))
            updated += 1
        else:
            # Insert new helper with all available data
            cur.execute("""
                INSERT INTO helper_functions
                (name, file_path, parameters, purpose, error_handling, is_tool, is_sub_helper,
                 return_statements, target_database)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name,
                helper.get('file_path'),
                parameters,
                helper.get('purpose'),
                helper.get('error_handling'),
                1 if helper.get('is_tool', False) else 0,
                1 if helper.get('is_sub_helper', False) else 0,
                return_statements,
                helper.get('target_database')
            ))
            inserted += 1

    conn.commit()
    print(f"✅ Helper functions synced: {inserted} new, {updated} updated")

    # Show module organization summary
    cur.execute("""
        SELECT target_database, COUNT(*) as count
        FROM helper_functions
        GROUP BY target_database
        ORDER BY target_database
    """)
    print("   Database organization:")
    for row in cur.fetchall():
        print(f"     {row['count']:2d} helpers → {row['target_database']}")

    return inserted + updated


def sync_directive_helper_mappings(conn: sqlite3.Connection, all_helpers: List[Dict[str, Any]]):
    """
    Populate directive_helpers junction table using used_by_directives field from helper JSONs.

    Each helper's used_by_directives field contains:
    [
      {
        "directive_name": "aimfp_run",
        "execution_context": "self_invocation",
        "sequence_order": 1,
        "is_required": true,
        "parameters_mapping": {},
        "description": "..."
      }
    ]
    """
    print("\n🔗 Syncing directive-helper mappings from helper JSONs...")

    cur = conn.cursor()
    inserted = 0
    skipped = 0
    errors = 0

    for helper in all_helpers:
        helper_name = helper.get('name')
        if not helper_name:
            continue

        used_by = helper.get('used_by_directives', [])
        if not used_by or not isinstance(used_by, list):
            continue

        # Look up helper_function_id
        cur.execute("SELECT id FROM helper_functions WHERE name=?", (helper_name,))
        helper_row = cur.fetchone()
        if not helper_row:
            print(f"   ⚠️  Helper not found in database: {helper_name}")
            errors += 1
            continue

        helper_id = helper_row['id']

        # Process each directive mapping
        for mapping in used_by:
            directive_name = mapping.get('directive_name')
            if not directive_name:
                skipped += 1
                continue

            # Look up directive_id
            cur.execute("SELECT id FROM directives WHERE name=?", (directive_name,))
            directive_row = cur.fetchone()
            if not directive_row:
                print(f"   ⚠️  Directive not found: {directive_name}")
                errors += 1
                continue

            directive_id = directive_row['id']

            # Extract mapping fields
            execution_context = mapping.get('execution_context', '')
            sequence_order = mapping.get('sequence_order', 0)
            is_required = 1 if mapping.get('is_required', True) else 0
            parameters_mapping = mapping.get('parameters_mapping', {})
            description = mapping.get('description', '')

            # Convert parameters_mapping to JSON string if needed
            if isinstance(parameters_mapping, dict):
                parameters_mapping = json.dumps(parameters_mapping)

            # Check if mapping already exists
            cur.execute("""
                SELECT id FROM directive_helpers
                WHERE directive_id=? AND helper_function_id=? AND execution_context=?
            """, (directive_id, helper_id, execution_context))

            existing = cur.fetchone()

            if existing:
                # Update existing mapping
                cur.execute("""
                    UPDATE directive_helpers
                    SET sequence_order = ?,
                        is_required = ?,
                        parameters_mapping = ?,
                        description = ?
                    WHERE id = ?
                """, (
                    sequence_order,
                    is_required,
                    parameters_mapping,
                    description,
                    existing['id']
                ))
            else:
                # Insert new mapping
                cur.execute("""
                    INSERT INTO directive_helpers
                    (directive_id, helper_function_id, execution_context, sequence_order,
                     is_required, parameters_mapping, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    directive_id,
                    helper_id,
                    execution_context,
                    sequence_order,
                    is_required,
                    parameters_mapping,
                    description
                ))
                inserted += 1

    conn.commit()
    print(f"✅ Directive-helper mappings synced: {inserted} new")
    if errors > 0:
        print(f"   ⚠️  {errors} mappings had errors (directive or helper not found)")
    if skipped > 0:
        print(f"   ⚠️  {skipped} mappings skipped (missing required fields)")

    # Show summary statistics
    cur.execute("""
        SELECT COUNT(*) as total_mappings,
               COUNT(DISTINCT directive_id) as unique_directives,
               COUNT(DISTINCT helper_function_id) as unique_helpers
        FROM directive_helpers
    """)
    stats = cur.fetchone()
    print(f"   Total mappings in database: {stats['total_mappings']}")
    print(f"   Directives with helpers: {stats['unique_directives']}")
    print(f"   Helpers mapped to directives: {stats['unique_helpers']}")


# ===================================
# DIRECTIVE FLOW SYNC
# ===================================

def sync_directive_flows(conn: sqlite3.Connection):
    """
    Load directive flows from directive_flow_*.json files and populate directive_flow table.

    Flow structure:
    {
      "from_directive": "aimfp_run",
      "to_directive": "aimfp_status",
      "flow_type": "status_branch",
      "flow_category": "project",
      "condition_key": "is_new_session",
      "condition_value": "true",
      "condition_description": "...",
      "priority": 100,
      "description": "..."
    }
    """
    print("\n🔄 Syncing directive flows from JSON files...")

    # Always fresh - delete all existing flows before inserting
    cur = conn.cursor()
    cur.execute("DELETE FROM directive_flow")
    deleted_count = cur.rowcount
    if deleted_count > 0:
        print(f"   🗑️  Cleared {deleted_count} existing flows (fresh sync)")

    all_flows = []
    for flow_file in DIRECTIVE_FLOW_FILES:
        flow_path = os.path.join(DIRECTIVES_JSON_DIR, flow_file)
        flows = load_json_file(flow_path)
        if flows:
            all_flows.extend(flows)
            print(f"   📘 Loaded {len(flows)} flows from {flow_file}")

    if not all_flows:
        print("⚠️  No directive flows found")
        return

    print(f"📋 Processing {len(all_flows)} directive flows")

    inserted = 0
    errors = 0

    for flow in all_flows:
        from_directive = flow.get('from_directive')
        to_directive = flow.get('to_directive')
        flow_type = flow.get('flow_type', 'conditional')

        if not to_directive:
            errors += 1
            continue

        # Allow wildcard '*' for reference_consultation and utility flows
        if from_directive == '*':
            if flow_type not in ['reference_consultation', 'utility', 'conditional']:
                print(f"   ⚠️  Wildcard '*' only allowed for reference_consultation, utility, or conditional flows: {to_directive}")
                errors += 1
                continue
            # Wildcard is valid - only verify to_directive exists
            cur.execute("SELECT name FROM directives WHERE name = ?", (to_directive,))
            if not cur.fetchone():
                print(f"   ⚠️  Target directive not found: {to_directive}")
                errors += 1
                continue
        else:
            # Normal flow - verify both directives exist
            if not from_directive:
                errors += 1
                continue

            cur.execute("SELECT name FROM directives WHERE name IN (?, ?)",
                       (from_directive, to_directive))
            found = [row['name'] for row in cur.fetchall()]

            if from_directive not in found:
                print(f"   ⚠️  Source directive not found: {from_directive}")
                errors += 1
                continue

            if to_directive not in found:
                print(f"   ⚠️  Target directive not found: {to_directive}")
                errors += 1
                continue

        # Extract flow fields
        flow_category = flow.get('flow_category', 'project')
        condition_key = flow.get('condition_key')
        condition_value = flow.get('condition_value')
        condition_description = flow.get('condition_description')
        priority = flow.get('priority', 0)
        description = flow.get('description', '')

        # Insert flow (table was cleared at start for fresh sync)
        cur.execute("""
            INSERT INTO directive_flow
            (from_directive, to_directive, flow_category, flow_type,
             condition_key, condition_value, condition_description, priority, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            from_directive,
            to_directive,
            flow_category,
            flow_type,
            condition_key,
            condition_value,
            condition_description,
            priority,
            description
        ))
        inserted += 1

    conn.commit()
    print(f"✅ Directive flows synced: {inserted} inserted (fresh sync)")
    if errors > 0:
        print(f"   ⚠️  {errors} flows had errors (missing directives or fields)")

    # Show summary statistics
    cur.execute("""
        SELECT flow_category, flow_type, COUNT(*) as count
        FROM directive_flow
        GROUP BY flow_category, flow_type
        ORDER BY flow_category, flow_type
    """)
    print("   Flow organization:")
    for row in cur.fetchall():
        print(f"     {row['count']:2d} flows → {row['flow_category']}/{row['flow_type']}")


# ===================================
# SYNC EXECUTION
# ===================================

def sync_directives():
    print("🔄 Starting Full AIMFP Directive Sync (Schema v2.0)")
    print("📦 Including: FP Core, FP Aux, Project, User Prefs, User System, Git Integration")
    print("📦 Including: Categories, Intent Keywords, Helpers, Directive-Helper Mappings, Directive Flows")

    # Ensure database directory exists
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        print(f"📁 Created database directory: {db_dir}")

    conn = get_conn(DB_PATH)
    ensure_schema(conn)

    # Run any pending migrations
    run_migrations(conn)

    cur = conn.cursor()

    # Load all directive files
    all_directive_files = (
        FP_DIRECTIVE_FILES +
        PROJECT_DIRECTIVE_FILES +
        USER_PREFERENCE_FILES +
        USER_SYSTEM_FILES +
        GIT_DIRECTIVE_FILES
    )

    all_entries = []
    for file in all_directive_files:
        file_path = os.path.join(DIRECTIVES_JSON_DIR, file)
        entries = load_json_file(file_path)
        all_entries.extend(entries)
        print(f"📘 Loaded {len(entries)} directives from {file}")

    # Extract and sync categories FIRST
    categories = extract_categories_from_directives(all_entries)
    category_id_map = sync_categories(conn, categories)

    # Extract and sync intent keywords FIRST
    intent_keywords = extract_intent_keywords_from_directives(all_entries)
    keyword_id_map = sync_intent_keywords(conn, intent_keywords)

    # Sync directives
    report = {"added": [], "updated": []}

    for entry in all_entries:
        if not entry.get("name") or not entry.get("type"):
            continue
        result = upsert_directive(conn, entry)
        report[result].append(entry["name"])

    # Second pass: Parent linkage
    for entry in all_entries:
        if entry.get("parent_directive"):
            cur.execute("""
                UPDATE directives SET parent_directive=?
                WHERE name=? AND parent_directive IS NULL
            """, (entry["parent_directive"], entry["name"]))
    conn.commit()

    # Link directives to categories
    link_directive_categories(conn, all_entries, category_id_map)

    # Link directives to intent keywords
    link_directive_intent_keywords(conn, all_entries, keyword_id_map)

    # Sync helper functions from JSON files
    all_helpers = load_all_helper_files()
    sync_helper_functions(conn)

    # Sync directive-helper mappings from helper's used_by_directives field
    sync_directive_helper_mappings(conn, all_helpers)

    # Sync directive flows
    sync_directive_flows(conn)

    # Sync one-time system notices
    notice_count = sync_system_notices(conn)

    if DRY_RUN:
        conn.rollback()
        print("🧪 Dry-run mode: no DB changes committed.")
    else:
        conn.commit()

    print(f"\n✅ Directives: {len(report['added'])} added | {len(report['updated'])} updated")
    print(f"✅ Categories: {len(categories)}")
    print(f"✅ Intent Keywords: {len(intent_keywords)}")
    print(f"✅ Helpers: {len(all_helpers)}")
    print(f"✅ System notices: {notice_count}")

    os.makedirs(os.path.dirname(SYNC_REPORT_FILE), exist_ok=True)
    with open(SYNC_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"📄 Sync report saved to {SYNC_REPORT_FILE}")

    # Run post-sync validation
    validate_integrity(conn)

    conn.close()


# ===================================
# INTEGRITY VALIDATION LAYER
# ===================================

def sync_system_notices(conn) -> int:
    """
    Load one-time system notices from dev/notices-json/*.json into system_notices.

    Notices are replaced wholesale on every sync, exactly like directives and
    helpers — the JSON is the source of truth. Per-project acknowledgements live
    in user_preferences.db and are untouched by this, so re-syncing never re-fires
    a notice a user has already seen.

    Returns the number of notices loaded.
    """
    if not os.path.isdir(NOTICES_DIR):
        return 0

    notices = []
    for path in sorted(Path(NOTICES_DIR).glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠️  Could not parse {path.name}: {e}")
            continue
        notices.extend(data.get("notices", []))

    if not notices:
        return 0

    print("\n📢 Syncing one-time system notices...")
    cur = conn.cursor()
    seen = set()

    for notice in notices:
        key = notice.get("notice_key")
        if not key:
            print("⚠️  Notice missing notice_key — skipped")
            continue
        if key in seen:
            print(f"⚠️  Duplicate notice_key '{key}' — later definition wins")
        seen.add(key)

        cur.execute(
            """
            INSERT OR REPLACE INTO system_notices
                (notice_key, title, message, severity, action_required,
                 applies_to_db, applies_from_version, introduced_in)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                notice.get("title", ""),
                notice.get("message", ""),
                notice.get("severity", "info"),
                1 if notice.get("action_required") else 0,
                notice.get("applies_to_db"),
                notice.get("applies_from_version"),
                notice.get("introduced_in"),
            ),
        )
        scope = (
            f"{notice.get('applies_to_db')} >= {notice.get('applies_from_version')}"
            if notice.get("applies_to_db") else "all projects"
        )
        print(f"   • {key} ({scope})")

    return len(seen)


def validate_integrity(conn):
    """Runs a suite of consistency checks after sync."""
    print("\n🧩 Running Post-Sync Integrity Validation...")
    cur = conn.cursor()
    issues = []

    # 1. Check for FP directives with levels
    cur.execute("SELECT name FROM directives WHERE type='fp' AND level IS NOT NULL;")
    for row in cur.fetchall():
        issues.append(f"❌ FP directive '{row['name']}' has a level assigned.")

    # 2. Check for orphaned parent_directive links
    cur.execute("""
        SELECT name, parent_directive FROM directives
        WHERE parent_directive IS NOT NULL
        AND parent_directive NOT IN (SELECT name FROM directives)
    """)
    for row in cur.fetchall():
        issues.append(f"⚠️ Orphaned parent link: {row['name']} → {row['parent_directive']}")

    # 3. Check for duplicate category links
    cur.execute("""
        SELECT directive_id, category_id, COUNT(*) as c FROM directive_categories
        GROUP BY directive_id, category_id HAVING c > 1
    """)
    for row in cur.fetchall():
        issues.append(f"⚠️ Duplicate directive-category link for directive_id={row['directive_id']}.")

    # 4. Verify helper_functions loaded from JSON with is_tool and is_sub_helper
    cur.execute("SELECT COUNT(*) as tool_count FROM helper_functions WHERE is_tool = 1;")
    tool_count = cur.fetchone()["tool_count"]
    cur.execute("SELECT COUNT(*) as sub_helper_count FROM helper_functions WHERE is_sub_helper = 1;")
    sub_helper_count = cur.fetchone()["sub_helper_count"]

    if tool_count == 0:
        issues.append("⚠️ No helpers marked as is_tool=1. MCP tools may not be properly configured.")
    else:
        print(f"   ✓ Found {tool_count} MCP tools (is_tool=1)")

    if sub_helper_count > 0:
        print(f"   ✓ Found {sub_helper_count} sub-helpers (is_sub_helper=1)")

    # 4b. Verify every is_tool=1 helper has a dispatch entry in the MCP registry.
    # server.py advertises tools from this DB, but dispatch does TOOL_REGISTRY[name] —
    # so a helper marked is_tool=1 with no registry entry is visible to the AI and
    # raises KeyError when called. That drift shipped undetected once
    # (remove_file_from_flow); this check makes it impossible to ship again.
    try:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
        from aimfp.mcp_server.registry import TOOL_REGISTRY  # noqa: E402

        cur.execute("SELECT name FROM helper_functions WHERE is_tool = 1;")
        db_tools = {row["name"] for row in cur.fetchall()}
        registered = set(TOOL_REGISTRY)

        unregistered = sorted(db_tools - registered)
        orphaned = sorted(registered - db_tools)

        if unregistered:
            issues.append(
                "⚠️ is_tool=1 in DB but NOT in TOOL_REGISTRY (AI can see them; "
                f"calling raises KeyError): {', '.join(unregistered)}"
            )
        if orphaned:
            issues.append(
                "⚠️ In TOOL_REGISTRY but not is_tool=1 in DB (unreachable dead "
                f"entries): {', '.join(orphaned)}"
            )
        if not unregistered and not orphaned:
            print(f"   ✓ Registry matches DB exactly ({len(registered)} tools dispatchable)")
    except ImportError as e:
        issues.append(f"⚠️ Could not import TOOL_REGISTRY to cross-check tools: {e}")
    finally:
        if sys.path and sys.path[0] == os.path.join(PROJECT_ROOT, "src"):
            sys.path.pop(0)

    # 4c. Verify every helper named in a directive's use_helpers array actually
    # exists. Unlike used_by_directives (which becomes the FK-backed
    # directive_helpers junction and therefore cannot reference a missing helper),
    # use_helpers is free text inside the workflow JSON and is never checked — so
    # it rots silently. Three dead names sat in project_catalog and three more in
    # project_progression, naming helpers that were renamed create_* -> add_*.
    def _collect_use_helpers(node, acc):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ('use_helpers', 'helpers') and isinstance(value, list):
                    acc.update(h for h in value if isinstance(h, str))
                else:
                    _collect_use_helpers(value, acc)
        elif isinstance(node, list):
            for item in node:
                _collect_use_helpers(item, acc)

    cur.execute("SELECT name FROM helper_functions;")
    known_helpers = {row["name"] for row in cur.fetchall()}
    cur.execute("SELECT name, workflow FROM directives;")

    dangling = {}
    for row in cur.fetchall():
        try:
            workflow = json.loads(row['workflow']) if isinstance(row['workflow'], str) else row['workflow']
        except (json.JSONDecodeError, TypeError):
            continue
        named = set()
        _collect_use_helpers(workflow, named)
        for helper in sorted(named - known_helpers):
            dangling.setdefault(helper, []).append(row['name'])

    if dangling:
        for helper, directive_names in sorted(dangling.items()):
            issues.append(
                f"⚠️ Directive use_helpers names '{helper}', which is not a real "
                f"helper (referenced by: {', '.join(directive_names)})."
            )
    else:
        print("   ✓ All use_helpers references resolve to real helpers")

    # 5. Verify directive_helpers junction table has entries
    cur.execute("SELECT COUNT(*) as mapping_count FROM directive_helpers;")
    mapping_count = cur.fetchone()["mapping_count"]
    if mapping_count == 0:
        issues.append("⚠️ No directive-helper mappings found. Check helper JSONs for used_by_directives field.")
    else:
        print(f"   ✓ Found {mapping_count} directive-helper mappings")

    # 6. Verify directive_flow table has entries
    cur.execute("SELECT COUNT(*) as flow_count FROM directive_flow;")
    flow_count = cur.fetchone()["flow_count"]
    if flow_count == 0:
        issues.append("⚠️ No directive flows found. Check directive_flow_*.json files.")
    else:
        print(f"   ✓ Found {flow_count} directive flows")

    # 7. Verify all directives have valid workflow structure
    cur.execute("SELECT name, workflow FROM directives;")
    for row in cur.fetchall():
        try:
            workflow = json.loads(row['workflow']) if isinstance(row['workflow'], str) else row['workflow']
            if not isinstance(workflow, dict) or 'trunk' not in workflow:
                issues.append(f"⚠️ Directive '{row['name']}' missing required 'trunk' in workflow")
        except (json.JSONDecodeError, TypeError):
            issues.append(f"❌ Directive '{row['name']}' has malformed workflow JSON")

    # 8. Verify MD file paths exist for all directives
    cur.execute("SELECT name, md_file_path FROM directives WHERE md_file_path IS NOT NULL;")
    md_files_checked = 0

    # Use PROJECT_ROOT for reference directory
    reference_dir = os.path.join(PROJECT_ROOT, 'src', 'aimfp', 'reference')

    for row in cur.fetchall():
        # Construct absolute path
        md_path = os.path.join(reference_dir, row['md_file_path'])
        if not os.path.exists(md_path):
            issues.append(f"❌ Directive '{row['name']}' references missing MD file: {row['md_file_path']}")
        md_files_checked += 1

    if md_files_checked > 0:
        print(f"   ✓ Verified {md_files_checked} MD file paths")

    # 9. Verify categories table populated
    cur.execute("SELECT COUNT(*) as cat_count FROM categories;")
    cat_count = cur.fetchone()["cat_count"]
    if cat_count == 0:
        issues.append("⚠️ No categories found. Check directive JSON files for category field.")
    else:
        print(f"   ✓ Found {cat_count} categories")

    # 10. Verify intent_keywords table populated
    cur.execute("SELECT COUNT(*) as kw_count FROM intent_keywords;")
    kw_count = cur.fetchone()["kw_count"]
    if kw_count == 0:
        issues.append("⚠️ No intent keywords found. Check directive JSON files for intent_keywords_json field.")
    else:
        print(f"   ✓ Found {kw_count} intent keywords")

    # 11. Verify directive_categories links
    cur.execute("SELECT COUNT(*) as link_count FROM directive_categories;")
    dc_link_count = cur.fetchone()["link_count"]
    if dc_link_count == 0:
        issues.append("⚠️ No directive-category links found.")
    else:
        print(f"   ✓ Found {dc_link_count} directive-category links")

    # 12. Verify directives_intent_keywords links
    cur.execute("SELECT COUNT(*) as link_count FROM directives_intent_keywords;")
    dik_link_count = cur.fetchone()["link_count"]
    if dik_link_count == 0:
        issues.append("⚠️ No directive-keyword links found.")
    else:
        print(f"   ✓ Found {dik_link_count} directive-keyword links")

    if issues:
        print(f"\n❗ Found {len(issues)} integrity warnings:")
        for i in issues:
            print("   " + i)
    else:
        print("\n✅ Database passed all integrity checks cleanly.")

    print("🔍 Integrity validation complete.\n")


# ===================================
# ENTRY POINT
# ===================================

if __name__ == "__main__":
    try:
        sync_directives()
    except Exception as e:
        print(f"❌ Sync failed: {e}")
        import traceback
        traceback.print_exc()

# ==================================
# MIGRATION GUIDE
# ==================================
# To create a new migration:
# 1. Create file: dev/migrations/migration_1.5_to_2.0.sql
# 2. Include schema changes (ALTER TABLE, CREATE TABLE, etc.)
# 3. Update CURRENT_SCHEMA_VERSION at top of this file
# 4. Run sync script - migrations apply automatically
#
# Example migration file structure:
# -- Migration from v1.5 to v2.0
# -- Description: Add new feature X
#
# ALTER TABLE directives ADD COLUMN new_field TEXT;
# CREATE INDEX IF NOT EXISTS idx_new_field ON directives(new_field);
#
# -- Update version (handled automatically by run_migrations)
#
# File Locations (permanent dev staging area):
# - Script:     dev/sync-directives.py
# - Directives: dev/directives-json/*.json
# - Helpers:    dev/helpers-json/*.json
# - Flows:      dev/directives-json/directive_flow_*.json
# - Migrations: dev/migrations/*.sql
# - Logs:       dev/logs/sync_report.json
# - Target DB:  src/aimfp/database/aimfp_core.db
# ==================================
