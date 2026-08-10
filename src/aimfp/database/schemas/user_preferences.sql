-- user_preferences.db Schema
-- Version: 1.2
-- Purpose: Store user-specific AI behavior preferences, customizations, and opt-in tracking
-- Location: .aimfp-project/user_preferences.db
-- Changelog v1.2:
--   - Added custom_return_statements table for user-defined helper guidance extensions
--   - Added indexes and trigger for custom_return_statements
-- Changelog v1.1:
--   - Renamed directive_context to directive_name in ai_interaction_log table
--   - Added CHECK constraints for status fields
--   - Added comprehensive timestamp triggers

-- ===============================================================
-- Core Settings
-- ===============================================================

-- User Settings: Project-specific AI behavior configurations
CREATE TABLE IF NOT EXISTS user_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key TEXT NOT NULL UNIQUE,           -- e.g., 'fp_strictness_level', 'prefer_explicit_returns'
    setting_value TEXT NOT NULL,                -- JSON value or simple string
    description TEXT,
    scope TEXT DEFAULT 'project' CHECK (scope IN ('project', 'global')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===============================================================
-- Directive Preferences (Atomic Key-Value Structure)
-- ===============================================================

-- Directive Preferences: Per-directive behavior customizations
CREATE TABLE IF NOT EXISTS directive_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    directive_name TEXT NOT NULL,               -- e.g., 'project_file_write'
    preference_key TEXT NOT NULL,               -- e.g., 'always_add_docstrings', 'max_function_length'
    preference_value TEXT NOT NULL,             -- Atomic value (JSON for complex structures)
    active BOOLEAN DEFAULT 1,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(directive_name, preference_key)      -- Allows multiple preferences per directive, one value per key
);

-- ===============================================================
-- AI Interaction Logging (Disabled by Default)
-- ===============================================================

-- AI Interaction Log: Track user corrections to learn preferences over time
CREATE TABLE IF NOT EXISTS ai_interaction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_type TEXT NOT NULL CHECK (interaction_type IN ('preference_learned', 'correction', 'clarification')),
    directive_name TEXT,                        -- Directive being executed (renamed from directive_context)
    user_feedback TEXT NOT NULL,                -- What user said/corrected
    ai_interpretation TEXT,                     -- How AI interpreted it
    applied_to_preferences BOOLEAN DEFAULT 0,   -- Whether this updated preferences
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===============================================================
-- FP Compliance Tracking (Disabled by Default)
-- ===============================================================

-- FP Flow Tracking: Track FP directive compliance history for improvement analysis
CREATE TABLE IF NOT EXISTS fp_flow_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    function_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    fp_directives_applied TEXT NOT NULL,        -- JSON array of directive names
    compliance_score REAL DEFAULT 1.0,          -- 0-1 score
    issues_json TEXT,                           -- JSON array of compliance issues
    user_overrides TEXT,                        -- JSON of user-approved exceptions
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===============================================================
-- Tracking Notes (Disabled by Default)
-- ===============================================================

-- Tracking Notes: General-purpose notes for tracking features (FP analysis, validation, debugging)
-- Similar to project.db notes table but specifically for opt-in tracking purposes
CREATE TABLE IF NOT EXISTS tracking_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    note_type TEXT NOT NULL CHECK (note_type IN (
        'fp_analysis',      -- FP compliance, patterns, refactorings, optimizations
        'user_interaction', -- User corrections, preferences, feedback patterns
        'validation',       -- Validation results, checks, approvals
        'performance',      -- Performance metrics, bottlenecks, profiling
        'debug'             -- Debug traces, reasoning traces, experiments
    )),
    reference_type TEXT,                        -- e.g., 'function', 'file', 'directive'
    reference_name TEXT,                        -- e.g., function name, file path, directive name
    reference_id INTEGER,                       -- Optional ID if referencing project.db entity
    directive_name TEXT,                        -- Directive that created this note
    severity TEXT DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'error')),
    metadata_json TEXT,                         -- Additional context (JSON)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===============================================================
-- Issue Reporting (Disabled by Default)
-- ===============================================================

-- Issue Reports: Allow users to compile context and submit issues with full logs
CREATE TABLE IF NOT EXISTS issue_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL CHECK (report_type IN ('bug', 'feature_request', 'directive_issue')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    directive_name TEXT,                        -- Related directive if applicable
    context_log_ids TEXT,                       -- JSON array of ai_interaction_log IDs
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'resolved')),
    submitted_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===============================================================
-- Tracking Settings (Feature Flags for Opt-In Tracking)
-- ===============================================================

-- Tracking Settings: Control which tracking features are enabled
CREATE TABLE IF NOT EXISTS tracking_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_name TEXT NOT NULL UNIQUE,          -- e.g., 'fp_flow_tracking', 'issue_reports'
    enabled BOOLEAN DEFAULT 0,                  -- Default: disabled (opt-in only)
    description TEXT,
    estimated_token_overhead TEXT,              -- e.g., "~5% increase per file write"
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===============================================================
-- Custom Return Statements (User Extensions for Helper Guidance)
-- ===============================================================

-- Custom Return Statements: User-defined return statement extensions for helper functions
-- Allows users to add forward-thinking context, notes, and guidance to any helper's output
-- These merge with core return_statements from aimfp_core.db at runtime
-- Management: Use set_custom_return_statement to add, delete_custom_return_statement to remove
-- No update — delete and recreate to modify existing statements
CREATE TABLE IF NOT EXISTS custom_return_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    helper_name TEXT NOT NULL,              -- Helper function name (matches aimfp_core.db helper_functions.name)
    statement TEXT NOT NULL,                -- Custom return statement text
    active BOOLEAN DEFAULT 1,              -- Whether statement is active
    description TEXT,                       -- Why this was added (optional context)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(helper_name, statement)          -- Prevent duplicate statements for same helper
);

-- ===============================================================
-- Indexes for Performance
-- ===============================================================

CREATE INDEX IF NOT EXISTS idx_directive_preferences_directive ON directive_preferences(directive_name);
CREATE INDEX IF NOT EXISTS idx_directive_preferences_active ON directive_preferences(active);
CREATE INDEX IF NOT EXISTS idx_ai_interaction_log_directive ON ai_interaction_log(directive_name);
CREATE INDEX IF NOT EXISTS idx_fp_flow_tracking_file ON fp_flow_tracking(file_path);
CREATE INDEX IF NOT EXISTS idx_tracking_notes_type ON tracking_notes(note_type);
CREATE INDEX IF NOT EXISTS idx_tracking_notes_directive ON tracking_notes(directive_name);
CREATE INDEX IF NOT EXISTS idx_tracking_notes_reference ON tracking_notes(reference_type, reference_name);
CREATE INDEX IF NOT EXISTS idx_issue_reports_status ON issue_reports(status);
CREATE INDEX IF NOT EXISTS idx_custom_return_statements_helper ON custom_return_statements(helper_name);
CREATE INDEX IF NOT EXISTS idx_custom_return_statements_active ON custom_return_statements(active);

-- ===============================================================
-- Triggers for Timestamp Updates
-- ===============================================================

CREATE TRIGGER IF NOT EXISTS update_user_settings_timestamp
AFTER UPDATE ON user_settings
FOR EACH ROW
BEGIN
    UPDATE user_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_directive_preferences_timestamp
AFTER UPDATE ON directive_preferences
FOR EACH ROW
BEGIN
    UPDATE directive_preferences SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_fp_flow_tracking_timestamp
AFTER UPDATE ON fp_flow_tracking
FOR EACH ROW
BEGIN
    UPDATE fp_flow_tracking SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_tracking_settings_timestamp
AFTER UPDATE ON tracking_settings
FOR EACH ROW
BEGIN
    UPDATE tracking_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS update_custom_return_statements_timestamp
AFTER UPDATE ON custom_return_statements
FOR EACH ROW
BEGIN
    UPDATE custom_return_statements SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- ===============================================================
-- Acknowledged Notices
-- Per-project record of which system_notices (aimfp_core.db) have already
-- been delivered, so a one-time announcement fires once and never nags again.
--
-- Only the acknowledgement lives here; the notice text ships in the read-only
-- core database. A row's presence is what suppresses the notice, so deleting
-- a row deliberately re-arms it.
-- ===============================================================

CREATE TABLE IF NOT EXISTS acknowledged_notices (
    notice_key TEXT PRIMARY KEY,                -- Matches system_notices.notice_key
    acknowledged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    outcome TEXT                                -- Optional: what the user decided
);

-- ===============================================================
-- Schema Version Tracking
-- ===============================================================

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),      -- Only one row allowed
    version TEXT NOT NULL,                      -- e.g., '1.1'
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, '1.3');
