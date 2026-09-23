"""
AIMFP Helper Functions - User Preferences Common Utilities

Shared utilities used across multiple user_preferences helper files.
Extracted to avoid duplication and improve AI efficiency at scale.

This is the CATEGORY level of the DRY hierarchy:

    utils.py (global)                    <- Database-agnostic shared code
        └── user_preferences/_common.py  <- THIS FILE: User preferences specific
            └── user_preferences/{file}.py <- Individual helpers

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Global constants defined here per AIMFP FP methodology:
- Valid tracking feature names
- Valid note types for tracking_notes
- Valid scope values for user_settings

Imported from utils.py (global):
- _open_connection: Database connection with row factory
- get_user_preferences_db_path: Path resolution
"""

import os
import sqlite3
from typing import Final

# Import global utilities (DRY - avoid duplication)
from ..utils import (  # noqa: F401 - re-exported for convenience
    _open_connection,
    _open_preferences_connection,
    get_cached_project_root,
    get_user_preferences_db_path,
)


# ============================================================================
# Global Constants - Validation Lookup Tables (FP-Compliant)
# ============================================================================
# These match CHECK constraints in user_preferences.sql schema
# Use Final + frozenset for immutability and fast membership testing

# Tracking feature names (tracking_settings table)
VALID_TRACKING_FEATURES: Final[frozenset[str]] = frozenset([
    'fp_flow_tracking',
    'ai_interaction_log',
    'helper_function_logging',
    'issue_reports',
    'compliance_checking'
])

# Note types for tracking_notes table
VALID_TRACKING_NOTE_TYPES: Final[frozenset[str]] = frozenset([
    'fp_analysis',
    'user_interaction',
    'validation',
    'performance',
    'debug'
])

# Severity levels (shared with project notes)
VALID_SEVERITY_LEVELS: Final[frozenset[str]] = frozenset([
    'info', 'warning', 'error'
])

# Scope values for user_settings table
VALID_SETTING_SCOPES: Final[frozenset[str]] = frozenset([
    'project', 'global'
])


# ============================================================================
# Validation Utilities
# ============================================================================

def _validate_tracking_feature(feature_name: str) -> bool:
    """
    Pure: Validate tracking feature name.

    Args:
        feature_name: Feature name to validate

    Returns:
        True if valid, False otherwise
    """
    return feature_name in VALID_TRACKING_FEATURES


def _validate_tracking_note_type(note_type: str) -> bool:
    """
    Pure: Validate tracking note type.

    Args:
        note_type: Note type to validate

    Returns:
        True if valid, False otherwise
    """
    return note_type in VALID_TRACKING_NOTE_TYPES


def _validate_severity(severity: str) -> bool:
    """
    Pure: Validate severity level.

    Args:
        severity: Severity value to validate

    Returns:
        True if valid, False otherwise
    """
    return severity in VALID_SEVERITY_LEVELS


def _validate_scope(scope: str) -> bool:
    """
    Pure: Validate setting scope.

    Args:
        scope: Scope value to validate

    Returns:
        True if valid, False otherwise
    """
    return scope in VALID_SETTING_SCOPES


# ============================================================================
# Entity Existence Check Utilities
# ============================================================================

def _check_setting_exists(conn: sqlite3.Connection, setting_key: str) -> bool:
    """
    Effect: Check if setting key exists in user_settings table.

    Args:
        conn: Database connection
        setting_key: Setting key to check

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(
        "SELECT setting_key FROM user_settings WHERE setting_key = ?",
        (setting_key,)
    )
    return cursor.fetchone() is not None


def _check_directive_preference_exists(
    conn: sqlite3.Connection,
    directive_name: str,
    preference_key: str
) -> bool:
    """
    Effect: Check if directive preference exists.

    Args:
        conn: Database connection
        directive_name: Directive name
        preference_key: Preference key

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(
        """
        SELECT id FROM directive_preferences
        WHERE directive_name = ? AND preference_key = ?
        """,
        (directive_name, preference_key)
    )
    return cursor.fetchone() is not None


def _check_tracking_feature_exists(conn: sqlite3.Connection, feature_name: str) -> bool:
    """
    Effect: Check if tracking feature exists in tracking_settings table.

    Args:
        conn: Database connection
        feature_name: Feature name to check

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(
        "SELECT feature_name FROM tracking_settings WHERE feature_name = ?",
        (feature_name,)
    )
    return cursor.fetchone() is not None


def _is_tracking_enabled(conn: sqlite3.Connection, feature_name: str) -> bool:
    """
    Effect: Check if a tracking feature is enabled.

    Args:
        conn: Database connection
        feature_name: Feature name to check

    Returns:
        True if enabled, False otherwise (including if feature doesn't exist)
    """
    cursor = conn.execute(
        "SELECT enabled FROM tracking_settings WHERE feature_name = ?",
        (feature_name,)
    )
    row = cursor.fetchone()
    return bool(row['enabled']) if row else False

