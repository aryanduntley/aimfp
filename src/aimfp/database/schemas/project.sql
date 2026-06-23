-- project.db Schema
-- Version: 1.11
-- Purpose: Track project-specific data, including files, functions, themes, flows, and completion paths
-- Changelog v1.11:
--   - Added stable `entity_key` column to functions and types (Stage 2 of semantic changeset merge)
--   - entity_key is a nullable UNIQUE identity minted at reservation (format: <fn|ty>-<name>-<uuid8>)
--   - Purpose: makes the merge identity of code entities immutable, so rename (name change) and move
--     (file change) become 'modify' ops instead of delete+add — enabling clean cross-clone rename
--     reconciliation for InterCommAIMFP. file+name become mutable attributes.
--   - Nullable + natural-key fallback: when entity_key is NULL the tools fall back to (file path,name),
--     preserving v1.10 behavior. Backfill on main before multi-agent work to populate them.
-- Changelog v1.10:
--   - Added stable `slug` column to work-hierarchy tables: milestones, tasks, subtasks, sidequests, items
--   - slug is a nullable UNIQUE identity minted at creation (format: <kind>-<name-slug>-<uuid8>)
--   - Purpose: stable cross-clone identity for semantic changeset merge (multi-agent parallel work
--     via InterCommAIMFP). Names are not unique and integer PKs collide across independently-minting
--     clones, so the slug is the merge identity for these rows.
--   - Nullable so the recreate-and-copy migration succeeds with empty values on existing rows; old
--     rows are slugged lazily on first changeset export (or via the optional backfill tool). Projects
--     not using InterCommAIMFP can leave slugs NULL with zero impact.
-- Changelog v1.9:
--   - Added modules table for reusable code boundary tracking
--   - Added module_files junction table for explicit file-to-module assignment
--   - Modules are folder-based: path guides organization, module_files is authoritative membership
--   - external_dependencies JSON field tracks wrapped libraries/services
--   - Added FTS5 index (modules_fts) for ranked search across name/purpose/description
--   - Added update trigger and indexes for modules table
-- Changelog v1.6:
--   - Added send_with_directive BOOLEAN field to notes table
--   - When TRUE and directive_name set, note is included in directive retrieval
--   - Added partial index idx_notes_directive_send for efficient queries
--   - Fixed VALID_NOTE_TYPES in Python code to match SQL CHECK constraint
-- Changelog v1.5:
--   - Removed UNIQUE constraint on name from functions and types tables
--   - Language already enforces per-file name uniqueness; DB constraint was redundant
--   - Allows same function/type name in different files (e.g., main() in multiple __main__.py)
--   - get_function_by_name now returns list of results with file data
-- Changelog v1.4:
--   - Added id_in_name BOOLEAN field to files, functions, types tables
--   - Tracks whether entity name contains _id_XX pattern or skipped ID naming
--   - Enables reserve/finalize workflow with optional ID embedding
-- Changelog v1.3:
--   - Added reservation system: is_reserved field to files, functions, types
--   - Added file_name to files table
--   - Added file_id to types table for file tracking
--   - Added returns field to functions table
--   - Made function names UNIQUE (FP flat namespace - no overloading)
--   - Removed linked_function_id from types table (use types_functions junction instead)
--   - Removed project_id from all tables (1 project per database)
--   - Added types_functions junction table for many-to-many type-function relationships
--   - Added comprehensive timestamp triggers for all mutable tables
--   - Added CHECK constraints to all status/priority/select fields
--   - Standardized priority fields from INTEGER to TEXT ('low', 'medium', 'high', 'critical')
-- Changelog v1.2: Added flow_ids (JSON array) to tasks and sidequests tables for flow-based work context linking

-- Project Table: High-level project overview and evolution
CREATE TABLE IF NOT EXISTS project (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,              -- e.g., 'MatrixCalculator'
    purpose TEXT NOT NULL,                  -- e.g., 'Build a pure functional matrix math library'
    goals_json TEXT NOT NULL,               -- JSON array, e.g., '["Fast computation", "No OOP"]'
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'abandoned')),
    version INTEGER DEFAULT 1,              -- Tracks idea evolution
    user_directives_status TEXT DEFAULT NULL CHECK (user_directives_status IN (NULL, 'pending_discovery', 'pending_parse', 'in_progress', 'active', 'disabled')),
                                            -- NULL: Case 1 (no user directives), 'pending_discovery': Case 2 selected during discovery,
                                            -- 'pending_parse': waiting for directive files, 'in_progress': building automation code,
                                            -- 'active': directives running, 'disabled': paused
    last_known_git_hash TEXT,               -- Last Git commit hash processed by AIMFP (for external change detection)
    last_git_sync DATETIME,                 -- Last time Git state was synchronized
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Infrastructure Table: Project setup (no project_id needed - 1 project per database)
CREATE TABLE IF NOT EXISTS infrastructure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                    -- e.g., 'language', 'package', 'testing'
    value TEXT,                            -- e.g., 'Python 3.12', 'numpy'
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Files Table: Project file inventory with reservation system
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,                              -- File name (e.g., 'calculator_id_42.py' or 'calculator.py' if id_in_name=0)
    path TEXT NOT NULL UNIQUE,              -- Full file path
    language TEXT,                          -- Guessed or set (e.g., 'Python')
    is_reserved BOOLEAN DEFAULT 0,          -- TRUE during reservation, FALSE after finalization
    id_in_name BOOLEAN DEFAULT 1,           -- TRUE if name contains _id_XX pattern, FALSE if ID naming skipped
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Functions Table: Per-file details with reservation system
CREATE TABLE IF NOT EXISTS functions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key TEXT UNIQUE,                 -- Stable cross-clone identity for semantic merge (minted at creation)
    name TEXT NOT NULL,                     -- Function name (not unique — language enforces per-file uniqueness)
    file_id INTEGER NOT NULL,
    purpose TEXT,
    parameters JSON,                        -- e.g., '["param1: str", "param2: int"]'
    returns JSON,                           -- e.g., '{"type": "int", "description": "Sum of inputs"}'
    is_reserved BOOLEAN DEFAULT 0,          -- TRUE during reservation, FALSE after finalization
    id_in_name BOOLEAN DEFAULT 1,           -- TRUE if name contains _id_XX pattern, FALSE if ID naming skipped
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

-- Types Table: For algebraic data types (directive_fp_adt) with reservation system
CREATE TABLE IF NOT EXISTS types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key TEXT UNIQUE,                     -- Stable cross-clone identity for semantic merge (minted at creation)
    name TEXT NOT NULL,
    file_id INTEGER,                            -- File where type is defined
    definition_json TEXT NOT NULL,              -- JSON schema for ADT (e.g., {"type": "enum", "variants": ["A", "B"]})
    description TEXT,
    links TEXT,                                 -- JSON string for type relationships/links
    is_reserved BOOLEAN DEFAULT 0,              -- TRUE during reservation, FALSE after finalization
    id_in_name BOOLEAN DEFAULT 1,               -- TRUE if name contains _id_XX pattern, FALSE if ID naming skipped
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

-- Types-Functions Junction Table: Many-to-many relationships between types and functions
CREATE TABLE IF NOT EXISTS types_functions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id INTEGER NOT NULL,
    function_id INTEGER NOT NULL,
    role TEXT CHECK (role IN ('factory', 'transformer', 'operator', 'pattern_matcher', 'accessor', 'validator', 'combinator')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(type_id, function_id, role),
    FOREIGN KEY (type_id) REFERENCES types(id) ON DELETE CASCADE,
    FOREIGN KEY (function_id) REFERENCES functions(id) ON DELETE CASCADE
);

-- Interactions Table: For function dependencies and chaining (directive_fp_dependency_tracking, directive_fp_chaining)
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_function_id INTEGER NOT NULL,
    target_function_id INTEGER NOT NULL,
    interaction_type TEXT NOT NULL CHECK (interaction_type IN ('call', 'chain', 'borrow', 'compose', 'pipe')),
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_function_id) REFERENCES functions(id) ON DELETE CASCADE,
    FOREIGN KEY (target_function_id) REFERENCES functions(id) ON DELETE CASCADE
);

-- Themes Table: AI-generated groupings (no project_id needed - 1 project per database)
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    ai_generated BOOLEAN DEFAULT 1,
    confidence_score REAL DEFAULT 0.0,      -- 0-1
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Flows Table: Sequences within themes (no project_id needed - 1 project per database)
CREATE TABLE IF NOT EXISTS flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    ai_generated BOOLEAN DEFAULT 1,
    confidence_score REAL DEFAULT 0.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Junction Tables for Many-to-Many
CREATE TABLE IF NOT EXISTS flow_themes (
    flow_id INTEGER,
    theme_id INTEGER,
    PRIMARY KEY (flow_id, theme_id),
    FOREIGN KEY (flow_id) REFERENCES flows(id) ON DELETE CASCADE,
    FOREIGN KEY (theme_id) REFERENCES themes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS file_flows (
    file_id INTEGER,
    flow_id INTEGER,
    PRIMARY KEY (file_id, flow_id),
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    FOREIGN KEY (flow_id) REFERENCES flows(id) ON DELETE CASCADE
);

-- Completion Path Table: High-level, standalone roadmap (no project_id needed - 1 project per database)
CREATE TABLE IF NOT EXISTS completion_path (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- e.g., 'setup', 'core dev', 'finish'
    order_index INTEGER NOT NULL,           -- For explicit ordering (1, 2, 3...)
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Milestones Table: Standalone overviews under completion_path
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,                       -- Stable cross-clone identity for semantic merge (minted at creation)
    completion_path_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'blocked')),
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (completion_path_id) REFERENCES completion_path(id) ON DELETE CASCADE
);

-- Tasks Table: Detailed breakdowns under milestones
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,                       -- Stable cross-clone identity for semantic merge (minted at creation)
    milestone_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'blocked')),
    priority TEXT DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    description TEXT,
    flow_ids TEXT,                          -- JSON array: [1, 3, 5] - flows this task works on
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (milestone_id) REFERENCES milestones(id) ON DELETE CASCADE
);

-- Subtasks Table: Potential breakdown of tasks
CREATE TABLE IF NOT EXISTS subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,                       -- Stable cross-clone identity for semantic merge (minted at creation)
    parent_task_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'blocked')),
    priority TEXT DEFAULT 'high' CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Sidequests Table: Priority deviations that pause tasks
CREATE TABLE IF NOT EXISTS sidequests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,                          -- Stable cross-clone identity for semantic merge (minted at creation)
    paused_task_id INTEGER NOT NULL,
    paused_subtask_id INTEGER,                 -- Optional: for finer-grained interruption tracking
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
    priority TEXT DEFAULT 'critical' CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    description TEXT,
    flow_ids TEXT,                             -- JSON array: [1, 3, 5] - flows this sidequest works on
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paused_task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (paused_subtask_id) REFERENCES subtasks(id) ON DELETE SET NULL
);

-- Items Table: Lowest-level actions for tasks/sidequests
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,                       -- Stable cross-clone identity for semantic merge (minted at creation)
    reference_table TEXT NOT NULL CHECK (reference_table IN ('tasks', 'subtasks', 'sidequests')),
    reference_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Notes Table: Clarifications for project entities
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    note_type TEXT NOT NULL CHECK (note_type IN (
        -- Original types (kept for compatibility)
        'clarification', 'pivot', 'research', 'entry_deletion', 'warning', 'error', 'info', 'auto_summary',
        -- New semantic types for better categorization
        'decision',        -- User decisions, confirmations (broader than clarification)
        'evolution',       -- Project direction changes, architecture shifts (broader than pivot)
        'analysis',        -- AI research, findings, reasoning (broader than research)
        'task_context',    -- Task/milestone/sidequest lifecycle context
        'external',        -- Git changes, dependency updates, file system events
        'summary',         -- Manual summaries (distinct from auto_summary which is automated)
        -- Deferred work tracking
        'deferred',        -- Placeholder code, TODOs, stubs, intentionally incomplete work
        'completed',       -- Resolved deferred items, closed follow-ups
        'obsolete'         -- Deferred items no longer relevant (code refactored, task re-scoped)
    )),
    reference_table TEXT,                   -- e.g., 'items', 'files', 'completion_path'
    reference_id INTEGER,
    source TEXT DEFAULT 'ai' CHECK (source IN ('ai', 'user', 'directive')),
    directive_name TEXT,                    -- Optional: directive context if note relates to directive execution
    severity TEXT DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'error')),
    send_with_directive BOOLEAN DEFAULT 0,  -- If TRUE and directive_name set, include note in directive retrieval
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===============================================================
-- Modules Table: Reusable code boundaries (domain logic units)
-- ===============================================================
-- Modules are folder-based: a module IS a directory containing related files.
-- File membership tracked explicitly via module_files junction table (DB is source of truth).
-- modules.path guides where files should go; module_files is the authoritative assignment.
-- Functions/types inherit module membership through their file_id.
-- Cross-module dependencies derived from existing interactions table.

CREATE TABLE IF NOT EXISTS modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,                  -- e.g., 'wallet_connection', 'validation', 'cart_management'
    path TEXT NOT NULL UNIQUE,                  -- Directory path relative to source root (e.g., 'src/wallet_connection/')
    description TEXT,                           -- What this module does
    purpose TEXT,                               -- Domain concern this module owns (one sentence)
    external_dependencies JSON,                 -- Libraries/services this module wraps (e.g., '["walletconnect", "stripe"]')
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Module-Files Junction: Explicit file-to-module assignment (same pattern as file_flows)
CREATE TABLE IF NOT EXISTS module_files (
    module_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    PRIMARY KEY (module_id, file_id),
    FOREIGN KEY (module_id) REFERENCES modules(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

-- ===============================================================
-- Git Integration Tables (Multi-User Collaboration)
-- ===============================================================
-- Note: Current Git state (branch, hash) is queried from Git directly via commands.
-- Only AIMFP-specific collaboration metadata is stored in database.
-- External change detection uses project.last_known_git_hash field.

-- Work Branches Table: Track user/AI work branches for collaboration
CREATE TABLE IF NOT EXISTS work_branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_name TEXT UNIQUE NOT NULL,           -- e.g., 'aimfp-alice-001', 'aimfp-ai-claude-001'
    user_name TEXT NOT NULL,                    -- e.g., 'alice', 'ai-claude'
    purpose TEXT NOT NULL,                      -- e.g., 'Implement matrix operations'
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'merged', 'abandoned')),
    created_from TEXT DEFAULT 'main',           -- Parent branch (usually 'main')
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    merged_at DATETIME NULL,                    -- When branch was merged
    merge_conflicts_count INTEGER DEFAULT 0,    -- Number of conflicts during merge
    merge_resolution_strategy TEXT,             -- JSON: how conflicts were resolved (FP purity, tests, manual)
    metadata_json TEXT                          -- Additional branch metadata (themes, flows, tasks)
);

-- Merge History Table: Full audit trail of branch merges and conflict resolutions
CREATE TABLE IF NOT EXISTS merge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_branch TEXT NOT NULL,                -- Branch being merged (e.g., 'aimfp-alice-001')
    target_branch TEXT DEFAULT 'main',          -- Branch being merged into (usually 'main')
    merge_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    conflicts_detected INTEGER DEFAULT 0,       -- Total conflicts detected
    conflicts_auto_resolved INTEGER DEFAULT 0,  -- Conflicts AI auto-resolved using FP purity
    conflicts_manual_resolved INTEGER DEFAULT 0,-- Conflicts user manually resolved
    resolution_details TEXT,                    -- JSON: detailed resolution log (function-by-function)
    merged_by TEXT,                             -- User or AI instance that performed merge
    merge_commit_hash TEXT                      -- Git commit hash of merge
);

-- Indexes for Git collaboration tables
CREATE INDEX IF NOT EXISTS idx_work_branches_user ON work_branches(user_name);
CREATE INDEX IF NOT EXISTS idx_work_branches_status ON work_branches(status);
CREATE INDEX IF NOT EXISTS idx_merge_history_timestamp ON merge_history(merge_timestamp);
CREATE INDEX IF NOT EXISTS idx_merge_history_source ON merge_history(source_branch);

-- ===============================================================
-- Triggers and Indexes
-- ===============================================================

-- Comprehensive Timestamp Triggers for All Tables
CREATE TRIGGER IF NOT EXISTS update_project_timestamp
AFTER UPDATE ON project
FOR EACH ROW
BEGIN
    UPDATE project SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_infrastructure_timestamp
AFTER UPDATE ON infrastructure
FOR EACH ROW
BEGIN
    UPDATE infrastructure SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_files_timestamp
AFTER UPDATE ON files
FOR EACH ROW
BEGIN
    UPDATE files SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_functions_timestamp
AFTER UPDATE ON functions
FOR EACH ROW
BEGIN
    UPDATE functions SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_types_timestamp
AFTER UPDATE ON types
FOR EACH ROW
BEGIN
    UPDATE types SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_interactions_timestamp
AFTER UPDATE ON interactions
FOR EACH ROW
BEGIN
    UPDATE interactions SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_themes_timestamp
AFTER UPDATE ON themes
FOR EACH ROW
BEGIN
    UPDATE themes SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_flows_timestamp
AFTER UPDATE ON flows
FOR EACH ROW
BEGIN
    UPDATE flows SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_completion_path_timestamp
AFTER UPDATE ON completion_path
FOR EACH ROW
BEGIN
    UPDATE completion_path SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_milestones_timestamp
AFTER UPDATE ON milestones
FOR EACH ROW
BEGIN
    UPDATE milestones SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_tasks_timestamp
AFTER UPDATE ON tasks
FOR EACH ROW
BEGIN
    UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_subtasks_timestamp
AFTER UPDATE ON subtasks
FOR EACH ROW
BEGIN
    UPDATE subtasks SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_sidequests_timestamp
AFTER UPDATE ON sidequests
FOR EACH ROW
BEGIN
    UPDATE sidequests SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_items_timestamp
AFTER UPDATE ON items
FOR EACH ROW
BEGIN
    UPDATE items SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_notes_timestamp
AFTER UPDATE ON notes
FOR EACH ROW
BEGIN
    UPDATE notes SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_modules_timestamp
AFTER UPDATE ON modules
FOR EACH ROW
BEGIN
    UPDATE modules SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- Triggers for items table cleanup (polymorphic orphan prevention)
CREATE TRIGGER IF NOT EXISTS delete_task_items
AFTER DELETE ON tasks
FOR EACH ROW
BEGIN
    DELETE FROM items WHERE reference_table='tasks' AND reference_id=OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS delete_subtask_items
AFTER DELETE ON subtasks
FOR EACH ROW
BEGIN
    DELETE FROM items WHERE reference_table='subtasks' AND reference_id=OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS delete_sidequest_items
AFTER DELETE ON sidequests
FOR EACH ROW
BEGIN
    DELETE FROM items WHERE reference_table='sidequests' AND reference_id=OLD.id;
END;

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_modules_path ON modules(path);
CREATE INDEX IF NOT EXISTS idx_modules_name ON modules(name);
CREATE INDEX IF NOT EXISTS idx_module_files_file ON module_files(file_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_functions_file_id ON functions(file_id);
CREATE INDEX IF NOT EXISTS idx_completion_path_order ON completion_path(order_index);
CREATE INDEX IF NOT EXISTS idx_items_reference ON items(reference_table, reference_id);
-- Stable-slug lookups for semantic changeset merge (resolve entity by slug across clones)
CREATE INDEX IF NOT EXISTS idx_milestones_slug ON milestones(slug);
CREATE INDEX IF NOT EXISTS idx_tasks_slug ON tasks(slug);
CREATE INDEX IF NOT EXISTS idx_subtasks_slug ON subtasks(slug);
CREATE INDEX IF NOT EXISTS idx_sidequests_slug ON sidequests(slug);
CREATE INDEX IF NOT EXISTS idx_items_slug ON items(slug);
CREATE INDEX IF NOT EXISTS idx_functions_entity_key ON functions(entity_key);
CREATE INDEX IF NOT EXISTS idx_types_entity_key ON types(entity_key);
CREATE INDEX IF NOT EXISTS idx_notes_directive ON notes(directive_name);
CREATE INDEX IF NOT EXISTS idx_notes_severity ON notes(severity);
CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source);
CREATE INDEX IF NOT EXISTS idx_notes_directive_send
    ON notes(directive_name, send_with_directive)
    WHERE send_with_directive = 1 AND directive_name IS NOT NULL;

-- ===============================================================
-- FTS5 Full-Text Search Indexes
-- ===============================================================

-- Notes FTS (search note content)
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    content,
    content='notes',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS notes_fts_insert AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_delete AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_update AFTER UPDATE OF content ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO notes_fts(rowid, content) VALUES (new.id, new.content);
END;

-- Functions FTS (search by name and purpose)
CREATE VIRTUAL TABLE IF NOT EXISTS functions_fts USING fts5(
    name,
    purpose,
    content='functions',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS functions_fts_insert AFTER INSERT ON functions BEGIN
    INSERT INTO functions_fts(rowid, name, purpose) VALUES (new.id, new.name, COALESCE(new.purpose, ''));
END;

CREATE TRIGGER IF NOT EXISTS functions_fts_delete AFTER DELETE ON functions BEGIN
    INSERT INTO functions_fts(functions_fts, rowid, name, purpose) VALUES('delete', old.id, old.name, COALESCE(old.purpose, ''));
END;

CREATE TRIGGER IF NOT EXISTS functions_fts_update AFTER UPDATE OF name, purpose ON functions BEGIN
    INSERT INTO functions_fts(functions_fts, rowid, name, purpose) VALUES('delete', old.id, old.name, COALESCE(old.purpose, ''));
    INSERT INTO functions_fts(rowid, name, purpose) VALUES (new.id, new.name, COALESCE(new.purpose, ''));
END;

-- Types FTS (search by name and description)
CREATE VIRTUAL TABLE IF NOT EXISTS types_fts USING fts5(
    name,
    description,
    content='types',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS types_fts_insert AFTER INSERT ON types BEGIN
    INSERT INTO types_fts(rowid, name, description) VALUES (new.id, new.name, COALESCE(new.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS types_fts_delete AFTER DELETE ON types BEGIN
    INSERT INTO types_fts(types_fts, rowid, name, description) VALUES('delete', old.id, old.name, COALESCE(old.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS types_fts_update AFTER UPDATE OF name, description ON types BEGIN
    INSERT INTO types_fts(types_fts, rowid, name, description) VALUES('delete', old.id, old.name, COALESCE(old.description, ''));
    INSERT INTO types_fts(rowid, name, description) VALUES (new.id, new.name, COALESCE(new.description, ''));
END;

-- Modules FTS (search by name, purpose, description)
CREATE VIRTUAL TABLE IF NOT EXISTS modules_fts USING fts5(
    name,
    purpose,
    description,
    content='modules',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS modules_fts_insert AFTER INSERT ON modules BEGIN
    INSERT INTO modules_fts(rowid, name, purpose, description) VALUES (new.id, new.name, COALESCE(new.purpose, ''), COALESCE(new.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS modules_fts_delete AFTER DELETE ON modules BEGIN
    INSERT INTO modules_fts(modules_fts, rowid, name, purpose, description) VALUES('delete', old.id, old.name, COALESCE(old.purpose, ''), COALESCE(old.description, ''));
END;

CREATE TRIGGER IF NOT EXISTS modules_fts_update AFTER UPDATE OF name, purpose, description ON modules BEGIN
    INSERT INTO modules_fts(modules_fts, rowid, name, purpose, description) VALUES('delete', old.id, old.name, COALESCE(old.purpose, ''), COALESCE(old.description, ''));
    INSERT INTO modules_fts(rowid, name, purpose, description) VALUES (new.id, new.name, COALESCE(new.purpose, ''), COALESCE(new.description, ''));
END;

-- ===============================================================
-- Schema Version Tracking
-- ===============================================================

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),      -- Only one row allowed
    version TEXT NOT NULL,                      -- e.g., '1.3'
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, '1.11');
