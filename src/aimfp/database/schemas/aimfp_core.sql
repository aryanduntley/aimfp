-- aimfp_core.db Schema
-- Version: 2.0
-- Purpose: Defines MCP-level directives (read-only) and helper functions
-- This database is immutable once deployed; AI reads it but never modifies it.

CREATE TABLE IF NOT EXISTS directives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,                      -- e.g., 'aimfp_run', 'init_project'
    type TEXT NOT NULL CHECK (type IN ('fp', 'project', 'git', 'user_system', 'user_preference')),
    level INTEGER DEFAULT NULL,                     -- 0–4 for 'project' directives only
    parent_directive TEXT REFERENCES directives(name), -- Optional link for hierarchy
    description TEXT,
    workflow JSON NOT NULL,                         -- JSON with trunk/branches/error_handling
    md_file_path TEXT,                              -- e.g., 'directives/aimfp_run.md'
    roadblocks_json TEXT,                           -- JSON array of issues/resolutions
    confidence_threshold REAL DEFAULT 0.5           -- 0–1 threshold for matching/escalation
);

-- ===============================================================
-- Intent Keywords for Directive Search (Normalized)
-- ===============================================================

-- Intent Keywords Table (master list of all keywords)
CREATE TABLE IF NOT EXISTS intent_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE                    -- e.g., 'authentication', 'purity', 'task'
);

-- Index for keyword lookups
CREATE INDEX IF NOT EXISTS idx_intent_keyword_name ON intent_keywords(keyword);

-- Linking Table: Directive <-> Keywords (many-to-many)
CREATE TABLE IF NOT EXISTS directives_intent_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    directive_id INTEGER NOT NULL,
    keyword_id INTEGER NOT NULL,
    UNIQUE(directive_id, keyword_id),               -- Prevent duplicate keyword per directive
    FOREIGN KEY (directive_id) REFERENCES directives(id) ON DELETE CASCADE,
    FOREIGN KEY (keyword_id) REFERENCES intent_keywords(id) ON DELETE CASCADE
);

-- Index for directive -> keywords lookup
CREATE INDEX IF NOT EXISTS idx_directive_keywords ON directives_intent_keywords(directive_id);

-- Index for keyword -> directives lookup
CREATE INDEX IF NOT EXISTS idx_keyword_directives ON directives_intent_keywords(keyword_id);

-- ===============================================================
-- Categories and Directive-Category Linking
-- ===============================================================

-- Categories Table
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,                       -- e.g., 'purity', 'immutability', 'task_management'
    description TEXT                                 -- Optional human-readable explanation
);

-- Linking Table (many-to-many)
CREATE TABLE IF NOT EXISTS directive_categories (
    directive_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (directive_id, category_id),
    FOREIGN KEY (directive_id) REFERENCES directives(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

-- Enforce that FP directives cannot have level values
CREATE TRIGGER IF NOT EXISTS enforce_level_on_fp
BEFORE INSERT ON directives
FOR EACH ROW
WHEN NEW.type = 'fp' AND NEW.level IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'FP directives cannot have a level value.');
END;

-- ===============================================================
-- Directive Flow: Status-Driven Decision Tree
-- ===============================================================
-- See docs/DIRECTIVE_NAVIGATION_SYSTEM.md for complete documentation

CREATE TABLE IF NOT EXISTS directive_flow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_directive TEXT NOT NULL,
    to_directive TEXT NOT NULL,

    -- Flow category (NEW in v2.0)
    flow_category TEXT CHECK (flow_category IN (
        'project',           -- Project management workflows
        'fp',                -- FP reference consultation patterns
        'user_preferences',  -- User settings and preferences
        'git'                -- Git collaboration workflows
    )) NOT NULL DEFAULT 'project',

    -- Flow classification
    flow_type TEXT CHECK (flow_type IN (
        'status_branch',         -- Branch from status based on project state
        'completion_loop',       -- Return to status after completing action
        'conditional',           -- Conditional next step during work execution
        'error',                 -- Error handling path
        'reference_consultation',-- FP directive lookup (consult when needed)
        'canonical',             -- Standard workflow step (always follows)
        'error_handler',         -- Error handling redirect
        'utility'                -- Utility/helper operation
    )) NOT NULL DEFAULT 'conditional',

    -- Condition for this transition
    condition_key TEXT,              -- JSONPath-like key: "project.initialized", "task.has_items"
    condition_value TEXT,            -- Expected value: "false", "true", "exists", or specific value
    condition_description TEXT,      -- Human readable: "if project not initialized"

    priority INTEGER DEFAULT 0,      -- Higher = preferred when multiple conditions match
    description TEXT,                -- Why this transition exists

    FOREIGN KEY (from_directive) REFERENCES directives(name),
    FOREIGN KEY (to_directive) REFERENCES directives(name)
);

CREATE INDEX IF NOT EXISTS idx_directive_flow_from ON directive_flow(from_directive);
CREATE INDEX IF NOT EXISTS idx_directive_flow_type ON directive_flow(flow_type);
CREATE INDEX IF NOT EXISTS idx_directive_flow_category ON directive_flow(flow_category);

-- ===============================================================
-- Helper Functions (used by directives)
-- ===============================================================

CREATE TABLE IF NOT EXISTS helper_functions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,               -- e.g., 'init_project_db', 'get_task', 'get_current_progress'
    file_path TEXT NOT NULL,                 -- e.g., 'helpers/project/task_tools.py'
    parameters JSON,                         -- JSON array of parameter objects: [{"name": "task_id", "type": "int", "required": true}]
    purpose TEXT NOT NULL,                   -- Clear description of what this helper does
    error_handling TEXT,                     -- How errors are handled (e.g., 'Return None if not found', 'Raise ValidationError')
    is_tool BOOLEAN NOT NULL DEFAULT 0,      -- TRUE if exposed as MCP tool (AI can call directly via MCP)
    is_sub_helper BOOLEAN NOT NULL DEFAULT 0,-- TRUE if internal utility (only called by other helpers, no direct AI access)
    return_statements JSON,                  -- JSON array of AI guidance after execution (e.g., next steps, validation checks)
    target_database TEXT CHECK (target_database IN (
        'core',              -- aimfp_core.db (directives, helpers, directive_flow)
        'project',           -- project.db (single-database CRUD operations)
        'user_preferences',  -- user_preferences.db (settings and preferences)
        'user_directives',   -- user_directives.db (user-defined automation)
        'multi_db',          -- Multi-database operations (orchestrators that coordinate across databases)
        'no_db'              -- Non-database operations (git, filesystem, validation utilities)
    )) NOT NULL                              -- AI uses get_helpers_by_database(target_database) to find helpers, not custom SQL queries
);

-- ===============================================================
-- Directive-Helper Mapping: Many-to-many relationship
-- ===============================================================

CREATE TABLE IF NOT EXISTS directive_helpers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    directive_id INTEGER NOT NULL,
    helper_function_id INTEGER NOT NULL,
    execution_context TEXT,                  -- e.g., 'workflow_step_3', 'error_handler', 'validation'
    sequence_order INTEGER DEFAULT 0,        -- Order of execution if multiple helpers in workflow
    is_required BOOLEAN DEFAULT 1,           -- TRUE if helper must execute, FALSE if optional/conditional
    parameters_mapping JSON,                 -- Optional: maps directive workflow params to helper params
    description TEXT,                        -- Brief note on why this helper is used
    UNIQUE(directive_id, helper_function_id, execution_context),
    FOREIGN KEY (directive_id) REFERENCES directives(id) ON DELETE CASCADE,
    FOREIGN KEY (helper_function_id) REFERENCES helper_functions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_directive_helpers_directive ON directive_helpers (directive_id);
CREATE INDEX IF NOT EXISTS idx_directive_helpers_helper ON directive_helpers (helper_function_id);

-- ===============================================================
-- FTS5 Full-Text Search Indexes
-- ===============================================================

-- Directives FTS (search by name and description)
CREATE VIRTUAL TABLE IF NOT EXISTS directives_fts USING fts5(
    name,
    description,
    content='directives',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS directives_fts_insert AFTER INSERT ON directives BEGIN
    INSERT INTO directives_fts(rowid, name, description) VALUES (new.id, new.name, COALESCE(new.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS directives_fts_delete AFTER DELETE ON directives BEGIN
    INSERT INTO directives_fts(directives_fts, rowid, name, description) VALUES('delete', old.id, old.name, COALESCE(old.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS directives_fts_update AFTER UPDATE OF name, description ON directives BEGIN
    INSERT INTO directives_fts(directives_fts, rowid, name, description) VALUES('delete', old.id, old.name, COALESCE(old.description, ''));
    INSERT INTO directives_fts(rowid, name, description) VALUES (new.id, new.name, COALESCE(new.description, ''));
END;

-- ===============================================================
-- Schema Version Tracking
-- ===============================================================

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),      -- Only one row allowed
    version TEXT NOT NULL,                      -- e.g., '1.6'
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS expected_schema_versions (
    db_name TEXT PRIMARY KEY,
    expected_version TEXT NOT NULL,
    minimum_version TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR REPLACE INTO expected_schema_versions (db_name, expected_version, minimum_version) VALUES
    ('project', '1.11', '1.0'),
    ('user_preferences', '1.2', '1.0'),
    ('user_directives', '1.2', '1.0');

INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, '2.2');
