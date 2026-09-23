"""
AIMFP Helper Functions - User Preferences Management

Specialized operations for user_preferences database.
These are high-frequency, typed operations for specific tables.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- load_directive_preferences: Load all active preferences for a directive
- add_directive_preference: Add new directive preference
- update_directive_preference: Update existing directive preference
- get_user_setting: Get single user setting by key
- add_user_setting: Add new user setting
- update_user_setting: Update existing user setting
- get_user_settings: Get all user settings
- get_tracking_settings: Get all tracking feature flags
- toggle_tracking_feature: Enable/disable tracking feature
- add_tracking_note: Add tracking note (when tracking enabled)
- get_tracking_notes: Get tracking notes with optional filters
- search_tracking_notes: Search tracking note content
- set_custom_return_statement: Add custom return statement for a helper (tool)
- delete_custom_return_statement: Delete custom return statement(s) (tool)
- get_custom_return_statements: Get custom return statements for a helper (sub-helper)
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

from ..utils import get_return_statements, rows_to_tuple

# Import common user_preferences utilities (DRY principle)
from ._common import (
    _open_connection,
    get_cached_project_root,
    _open_preferences_connection,
    _check_setting_exists,
    _check_directive_preference_exists,
    _check_tracking_feature_exists,
    _validate_tracking_feature,
    _validate_tracking_note_type,
    _validate_severity,
    _validate_scope,
    VALID_TRACKING_FEATURES,
    VALID_TRACKING_NOTE_TYPES
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class DirectivePreference:
    """Immutable directive preference record."""
    id: int
    directive_name: str
    preference_key: str
    preference_value: str
    active: bool
    description: Optional[str] = None


@dataclass(frozen=True)
class DirectivePreferencesResult:
    """Result of load_directive_preferences."""
    success: bool
    preferences: Tuple[DirectivePreference, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UserSetting:
    """Immutable user setting record."""
    setting_key: str
    setting_value: str
    description: Optional[str] = None
    scope: str = 'project'


@dataclass(frozen=True)
class UserSettingResult:
    """Result of get_user_setting."""
    success: bool
    setting: Optional[UserSetting] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UserSettingsResult:
    """Result of get_user_settings."""
    success: bool
    settings: Tuple[UserSetting, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TrackingSetting:
    """Immutable tracking setting record."""
    feature_name: str
    enabled: bool
    description: Optional[str] = None


@dataclass(frozen=True)
class TrackingSettingsResult:
    """Result of get_tracking_settings."""
    success: bool
    settings: Tuple[TrackingSetting, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TrackingNote:
    """Immutable tracking note record."""
    id: int
    content: str
    note_type: str
    severity: str
    reference_type: Optional[str] = None
    reference_name: Optional[str] = None
    reference_id: Optional[int] = None
    directive_name: Optional[str] = None
    metadata_json: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class TrackingNotesResult:
    """Result of get_tracking_notes and search_tracking_notes."""
    success: bool
    notes: Tuple[TrackingNote, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MutationResult:
    """Result of add/update/toggle operations."""
    success: bool
    message: Optional[str] = None
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()
    token_overhead_warning: Optional[str] = None  # For toggle_tracking_feature


# ============================================================================
# Directive Preferences Helpers
# ============================================================================

def load_directive_preferences(
    directive_name: str
) -> DirectivePreferencesResult:
    """
    Load all active preferences for a directive (high-frequency).

    Args:
        directive_name: Directive name to load preferences for

    Returns:
        DirectivePreferencesResult with active preferences

    Example:
        >>> result = load_directive_preferences(
        ...     "project_file_write"
        ... )
        >>> for pref in result.preferences:
        ...     print(f"{pref.preference_key}: {pref.preference_value}")
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        cursor = conn.execute(
            """
            SELECT id, directive_name, preference_key, preference_value,
                   active, description
            FROM directive_preferences
            WHERE directive_name = ? AND active = 1
            ORDER BY preference_key
            """,
            (directive_name,)
        )
        rows = cursor.fetchall()
        conn.close()

        preferences = tuple(
            DirectivePreference(
                id=row['id'],
                directive_name=row['directive_name'],
                preference_key=row['preference_key'],
                preference_value=row['preference_value'],
                active=bool(row['active']),
                description=row['description']
            )
            for row in rows
        )

        return_stmts = get_return_statements("load_directive_preferences")

        return DirectivePreferencesResult(
            success=True,
            preferences=preferences,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return DirectivePreferencesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def add_directive_preference(
    directive_name: str,
    preference_key: str,
    preference_value: str,
    active: bool = True,
    description: Optional[str] = None
) -> MutationResult:
    """
    Add new directive preference (or update if exists).

    Args:
        directive_name: Directive name
        preference_key: Preference key
        preference_value: Preference value
        active: Whether preference is active (default True)
        description: Optional description

    Returns:
        MutationResult with success/error

    Note:
        Uses INSERT OR REPLACE for upsert behavior.
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Use INSERT OR REPLACE for upsert behavior
        cursor = conn.execute(
            """
            INSERT OR REPLACE INTO directive_preferences
                (directive_name, preference_key, preference_value, active, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (directive_name, preference_key, preference_value, 1 if active else 0, description)
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        return_stmts = get_return_statements("add_directive_preference")

        return MutationResult(
            success=True,
            id=new_id,
            message=f"Preference '{preference_key}' added for directive '{directive_name}'",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_directive_preference(
    directive_name: str,
    preference_key: str,
    preference_value: Optional[str] = None,
    active: Optional[bool] = None,
    description: Optional[str] = None
) -> MutationResult:
    """
    Update existing directive preference.

    Args:
        directive_name: Directive name
        preference_key: Preference key to update (must exist)
        preference_value: New preference value (optional)
        active: Update active status (optional)
        description: Update description (optional)

    Returns:
        MutationResult with success/error

    Note:
        Only updates fields that are provided (non-None).
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if preference exists
        if not _check_directive_preference_exists(conn, directive_name, preference_key):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Preference '{preference_key}' for directive '{directive_name}' not found"
            )

        # Build SET clause with only provided fields
        set_parts = []
        values = []

        if preference_value is not None:
            set_parts.append("preference_value = ?")
            values.append(preference_value)

        if active is not None:
            set_parts.append("active = ?")
            values.append(1 if active else 0)

        if description is not None:
            set_parts.append("description = ?")
            values.append(description)

        if not set_parts:
            conn.close()
            return MutationResult(
                success=False,
                error="No fields to update"
            )

        set_clause = ", ".join(set_parts)
        values.extend([directive_name, preference_key])

        conn.execute(
            f"""
            UPDATE directive_preferences
            SET {set_clause}
            WHERE directive_name = ? AND preference_key = ?
            """,
            values
        )
        conn.commit()
        conn.close()

        return_stmts = get_return_statements("update_directive_preference")

        return MutationResult(
            success=True,
            message=f"Preference '{preference_key}' updated for directive '{directive_name}'",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# User Settings Helpers
# ============================================================================

def get_user_setting(
    setting_key: str
) -> UserSettingResult:
    """
    Get project-wide user setting by key (fairly frequent).

    Args:
        setting_key: Setting key to retrieve

    Returns:
        UserSettingResult with setting or null if not found
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        cursor = conn.execute(
            """
            SELECT setting_key, setting_value, description, scope
            FROM user_settings
            WHERE setting_key = ?
            """,
            (setting_key,)
        )
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return UserSettingResult(
                success=True,
                setting=None,
                return_statements=get_return_statements("get_user_setting")
            )

        setting = UserSetting(
            setting_key=row['setting_key'],
            setting_value=row['setting_value'],
            description=row['description'],
            scope=row['scope'] or 'project'
        )

        return UserSettingResult(
            success=True,
            setting=setting,
            return_statements=get_return_statements("get_user_setting")
        )

    except Exception as e:
        conn.close()
        return UserSettingResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def add_user_setting(
    setting_key: str,
    setting_value: str,
    description: Optional[str] = None,
    scope: str = 'project'
) -> MutationResult:
    """
    Add new project-wide user setting to user_settings table.

    Args:
        setting_key: Setting key (must be unique)
        setting_value: Setting value
        description: Description of the setting (optional)
        scope: Setting scope (default 'project')

    Returns:
        MutationResult with success/error

    Note:
        Returns error if setting_key already exists (use update_user_setting instead).
    """
    # Validate scope
    if not _validate_scope(scope):
        return MutationResult(
            success=False,
            error=f"Invalid scope: {scope}. Must be one of: project, global"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if setting already exists
        if _check_setting_exists(conn, setting_key):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Setting with key '{setting_key}' already exists"
            )

        cursor = conn.execute(
            """
            INSERT INTO user_settings (setting_key, setting_value, description, scope)
            VALUES (?, ?, ?, ?)
            """,
            (setting_key, setting_value, description, scope)
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        return_stmts = get_return_statements("add_user_setting")

        return MutationResult(
            success=True,
            id=new_id,
            message=f"Setting '{setting_key}' added",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_user_setting(
    setting_key: str,
    setting_value: Optional[str] = None,
    description: Optional[str] = None,
    scope: Optional[str] = None
) -> MutationResult:
    """
    Update existing project-wide user setting in user_settings table.

    Args:
        setting_key: Setting key to update (must exist)
        setting_value: New setting value (optional)
        description: New description (optional)
        scope: New scope (optional)

    Returns:
        MutationResult with success/error

    Note:
        Only updates fields that are provided (non-None).
        Returns error if setting_key not found (use add_user_setting instead).
    """
    # Validate scope if provided
    if scope is not None and not _validate_scope(scope):
        return MutationResult(
            success=False,
            error=f"Invalid scope: {scope}. Must be one of: project, global"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if setting exists
        if not _check_setting_exists(conn, setting_key):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Setting with key '{setting_key}' not found"
            )

        # Build SET clause with only provided fields
        set_parts = []
        values = []

        if setting_value is not None:
            set_parts.append("setting_value = ?")
            values.append(setting_value)

        if description is not None:
            set_parts.append("description = ?")
            values.append(description)

        if scope is not None:
            set_parts.append("scope = ?")
            values.append(scope)

        if not set_parts:
            conn.close()
            return MutationResult(
                success=False,
                error="No fields to update"
            )

        set_clause = ", ".join(set_parts)
        values.append(setting_key)

        conn.execute(
            f"UPDATE user_settings SET {set_clause} WHERE setting_key = ?",
            values
        )
        conn.commit()
        conn.close()

        return_stmts = get_return_statements("update_user_setting")

        return MutationResult(
            success=True,
            message=f"Setting '{setting_key}' updated",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_user_settings() -> UserSettingsResult:
    """
    Get all user settings from user_settings table.

    Returns:
        UserSettingsResult with all settings

    Note:
        Complements get_user_setting (singular) which gets one by key.
        Used for bundling all settings at session startup.
        Typically only 3-5 project-wide settings.
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        cursor = conn.execute(
            """
            SELECT setting_key, setting_value, description, scope
            FROM user_settings
            ORDER BY setting_key
            """
        )
        rows = cursor.fetchall()
        conn.close()

        settings = tuple(
            UserSetting(
                setting_key=row['setting_key'],
                setting_value=row['setting_value'],
                description=row['description'],
                scope=row['scope'] or 'project'
            )
            for row in rows
        )

        return_stmts = get_return_statements("get_user_settings")

        return UserSettingsResult(
            success=True,
            settings=settings,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return UserSettingsResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# Tracking Settings Helpers
# ============================================================================

def get_tracking_settings() -> TrackingSettingsResult:
    """
    Get all tracking feature flags.

    Returns:
        TrackingSettingsResult with all tracking settings

    Note:
        All tracking features are disabled by default.
        Features: fp_flow_tracking, ai_interaction_log, helper_function_logging,
                  issue_reports, compliance_checking
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        cursor = conn.execute(
            """
            SELECT feature_name, enabled, description
            FROM tracking_settings
            ORDER BY feature_name
            """
        )
        rows = cursor.fetchall()
        conn.close()

        settings = tuple(
            TrackingSetting(
                feature_name=row['feature_name'],
                enabled=bool(row['enabled']),
                description=row['description']
            )
            for row in rows
        )

        return_stmts = get_return_statements("get_tracking_settings")

        return TrackingSettingsResult(
            success=True,
            settings=settings,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return TrackingSettingsResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def toggle_tracking_feature(
    feature_name: str,
    enabled: bool
) -> MutationResult:
    """
    Enable/disable tracking feature.

    Args:
        feature_name: Tracking feature name
        enabled: Enable or disable

    Returns:
        MutationResult with success/error and token_overhead_warning when enabling

    Note:
        When enabling, returns token_overhead_warning so AI can present
        the cost information to the user before they commit to enabling.
    """
    # Validate feature name
    if not _validate_tracking_feature(feature_name):
        return MutationResult(
            success=False,
            error=f"Invalid feature: {feature_name}. Must be one of: {', '.join(sorted(VALID_TRACKING_FEATURES))}"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if feature exists in table
        if not _check_tracking_feature_exists(conn, feature_name):
            # Insert it (may happen on fresh database)
            conn.execute(
                """
                INSERT INTO tracking_settings (feature_name, enabled)
                VALUES (?, ?)
                """,
                (feature_name, 1 if enabled else 0)
            )
        else:
            # Update existing
            conn.execute(
                """
                UPDATE tracking_settings
                SET enabled = ?
                WHERE feature_name = ?
                """,
                (1 if enabled else 0, feature_name)
            )

        conn.commit()
        conn.close()

        # Prepare response
        action = "enabled" if enabled else "disabled"
        message = f"Tracking feature '{feature_name}' {action}"

        # Token overhead warning when enabling
        token_warning = None
        if enabled:
            token_warning = (
                f"TRACKING ENABLED: '{feature_name}' will consume additional tokens. "
                "This feature logs data for debugging/analysis purposes. "
                "Consider disabling when not actively needed to reduce token usage."
            )

        return_stmts = get_return_statements("toggle_tracking_feature")

        return MutationResult(
            success=True,
            message=message,
            token_overhead_warning=token_warning,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# Tracking Notes Helpers
# ============================================================================

def add_tracking_note(
    content: str,
    note_type: str,
    reference_type: Optional[str] = None,
    reference_name: Optional[str] = None,
    reference_id: Optional[int] = None,
    directive_name: Optional[str] = None,
    severity: str = 'info',
    metadata_json: Optional[str] = None
) -> MutationResult:
    """
    Add tracking note to user_preferences.db (only when tracking enabled).

    Args:
        content: Note content/message
        note_type: 'fp_analysis', 'user_interaction', 'validation', 'performance', 'debug'
        reference_type: Type of reference (e.g., 'function', 'file', 'directive')
        reference_name: Name of referenced entity
        reference_id: Optional ID if referencing project.db entity
        directive_name: Directive that created this note
        severity: 'info', 'warning', 'error' (default 'info')
        metadata_json: Additional context as JSON string

    Returns:
        MutationResult with success/error
    """
    # Validate note_type
    if not _validate_tracking_note_type(note_type):
        return MutationResult(
            success=False,
            error=f"Invalid note_type: {note_type}. Must be one of: {', '.join(sorted(VALID_TRACKING_NOTE_TYPES))}"
        )

    # Validate severity
    if not _validate_severity(severity):
        return MutationResult(
            success=False,
            error=f"Invalid severity: {severity}. Must be one of: info, warning, error"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        cursor = conn.execute(
            """
            INSERT INTO tracking_notes (
                content, note_type, severity, reference_type,
                reference_name, reference_id, directive_name, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (content, note_type, severity, reference_type,
             reference_name, reference_id, directive_name, metadata_json)
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        return_stmts = get_return_statements("add_tracking_note")

        return MutationResult(
            success=True,
            id=new_id,
            message="Tracking note added",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_tracking_notes(
    note_type: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_name: Optional[str] = None,
    directive_name: Optional[str] = None,
    severity: Optional[str] = None
) -> TrackingNotesResult:
    """
    Get tracking notes with optional filters.

    Args:
        note_type: Filter by note_type
        reference_type: Filter by reference_type
        reference_name: Filter by reference_name
        directive_name: Filter by directive_name
        severity: Filter by severity

    Returns:
        TrackingNotesResult with matching notes
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Build query with optional filters
        query = """
            SELECT id, content, note_type, severity, reference_type,
                   reference_name, reference_id, directive_name,
                   metadata_json, created_at
            FROM tracking_notes
        """

        where_parts = []
        values = []

        if note_type is not None:
            where_parts.append("note_type = ?")
            values.append(note_type)

        if reference_type is not None:
            where_parts.append("reference_type = ?")
            values.append(reference_type)

        if reference_name is not None:
            where_parts.append("reference_name = ?")
            values.append(reference_name)

        if directive_name is not None:
            where_parts.append("directive_name = ?")
            values.append(directive_name)

        if severity is not None:
            where_parts.append("severity = ?")
            values.append(severity)

        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)

        query += " ORDER BY created_at DESC"

        cursor = conn.execute(query, values)
        rows = cursor.fetchall()
        conn.close()

        notes = tuple(
            TrackingNote(
                id=row['id'],
                content=row['content'],
                note_type=row['note_type'],
                severity=row['severity'],
                reference_type=row['reference_type'],
                reference_name=row['reference_name'],
                reference_id=row['reference_id'],
                directive_name=row['directive_name'],
                metadata_json=row['metadata_json'],
                created_at=row['created_at']
            )
            for row in rows
        )

        return_stmts = get_return_statements("get_tracking_notes")

        return TrackingNotesResult(
            success=True,
            notes=notes,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return TrackingNotesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def search_tracking_notes(
    search_string: str,
    note_type: Optional[str] = None,
    reference_type: Optional[str] = None,
    directive_name: Optional[str] = None
) -> TrackingNotesResult:
    """
    Search tracking note content with optional filters.

    Args:
        search_string: Search string for note content
        note_type: Optional filter by note_type
        reference_type: Optional filter by reference_type
        directive_name: Optional filter by directive_name

    Returns:
        TrackingNotesResult with matching notes
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Build query with search and optional filters
        query = """
            SELECT id, content, note_type, severity, reference_type,
                   reference_name, reference_id, directive_name,
                   metadata_json, created_at
            FROM tracking_notes
            WHERE content LIKE ?
        """

        values = [f"%{search_string}%"]

        if note_type is not None:
            query += " AND note_type = ?"
            values.append(note_type)

        if reference_type is not None:
            query += " AND reference_type = ?"
            values.append(reference_type)

        if directive_name is not None:
            query += " AND directive_name = ?"
            values.append(directive_name)

        query += " ORDER BY created_at DESC"

        cursor = conn.execute(query, values)
        rows = cursor.fetchall()
        conn.close()

        notes = tuple(
            TrackingNote(
                id=row['id'],
                content=row['content'],
                note_type=row['note_type'],
                severity=row['severity'],
                reference_type=row['reference_type'],
                reference_name=row['reference_name'],
                reference_id=row['reference_id'],
                directive_name=row['directive_name'],
                metadata_json=row['metadata_json'],
                created_at=row['created_at']
            )
            for row in rows
        )

        return_stmts = get_return_statements("search_tracking_notes")

        return TrackingNotesResult(
            success=True,
            notes=notes,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return TrackingNotesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# Custom Return Statements — Data Structures
# ============================================================================

@dataclass(frozen=True)
class CustomReturnStatement:
    """Immutable custom return statement record."""
    id: int
    helper_name: str
    statement: str
    active: bool
    description: Optional[str] = None


@dataclass(frozen=True)
class CustomReturnStatementsResult:
    """Result of get_custom_return_statements."""
    success: bool
    statements: Tuple[CustomReturnStatement, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Custom Return Statements Helpers
# ============================================================================

def set_custom_return_statement(
    helper_name: str,
    statement: str,
    description: Optional[str] = None,
    active: bool = True
) -> MutationResult:
    """
    Add a custom return statement for a helper function (MCP tool).

    Custom return statements extend the core return_statements from aimfp_core.db
    with user-defined guidance, context, notes, or preferences. They are merged
    with core statements at runtime via get_return_statements().

    To modify an existing statement: delete it first, then set the new one.

    Args:
        helper_name: Helper function name (should match aimfp_core.db helper_functions.name)
        statement: Custom return statement text
        description: Why this was added (optional context)
        active: Whether statement is active (default True)

    Returns:
        MutationResult with success/error

    Example:
        >>> set_custom_return_statement(
        ...     "reserve_file_name",
        ...     "User requests no IDs in names for files, functions, types",
        ...     description="User preference for clean naming"
        ... )
    """
    if not helper_name or not helper_name.strip():
        return MutationResult(
            success=False,
            error="helper_name is required and cannot be empty"
        )

    if not statement or not statement.strip():
        return MutationResult(
            success=False,
            error="statement is required and cannot be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO custom_return_statements
                (helper_name, statement, active, description)
            VALUES (?, ?, ?, ?)
            """,
            (helper_name.strip(), statement.strip(), 1 if active else 0, description)
        )
        conn.commit()
        new_id = cursor.lastrowid
        inserted = cursor.rowcount > 0
        conn.close()

        if inserted:
            return_stmts = get_return_statements("set_custom_return_statement")
            return MutationResult(
                success=True,
                id=new_id,
                message=f"Custom return statement added for helper '{helper_name}'",
                return_statements=return_stmts
            )
        else:
            return MutationResult(
                success=True,
                message=f"Statement already exists for helper '{helper_name}' (no duplicate created)"
            )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def delete_custom_return_statement(
    helper_name: str,
    statement: Optional[str] = None,
    statement_id: Optional[int] = None
) -> MutationResult:
    """
    Delete custom return statement(s) for a helper function (MCP tool).

    Three modes:
    - If statement_id provided: delete that specific row by ID
    - If statement provided: delete the matching (helper_name, statement) row
    - If neither: delete ALL custom return statements for the helper

    Args:
        helper_name: Helper function name
        statement: Specific statement text to delete (optional)
        statement_id: Specific row ID to delete (optional)

    Returns:
        MutationResult with success/error
    """
    if not helper_name or not helper_name.strip():
        return MutationResult(
            success=False,
            error="helper_name is required and cannot be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        if statement_id is not None:
            # Delete by ID (must also match helper_name for safety)
            conn.execute(
                "DELETE FROM custom_return_statements WHERE id = ? AND helper_name = ?",
                (statement_id, helper_name.strip())
            )
        elif statement is not None:
            # Delete specific statement
            conn.execute(
                "DELETE FROM custom_return_statements WHERE helper_name = ? AND statement = ?",
                (helper_name.strip(), statement.strip())
            )
        else:
            # Delete ALL for this helper
            conn.execute(
                "DELETE FROM custom_return_statements WHERE helper_name = ?",
                (helper_name.strip(),)
            )

        conn.commit()
        conn.close()

        return_stmts = get_return_statements("delete_custom_return_statement")

        return MutationResult(
            success=True,
            message=f"Custom return statement(s) deleted for helper '{helper_name}'",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_custom_return_statements(
    helper_name: str
) -> CustomReturnStatementsResult:
    """
    Get all active custom return statements for a helper function (sub-helper).

    Read-only inspection of what custom return statements exist for a helper.
    The runtime merge into tool results happens in database/connection.py
    get_return_statements(), which queries custom_return_statements directly.

    Args:
        helper_name: Helper function name to get custom statements for

    Returns:
        CustomReturnStatementsResult with active custom statements
    """
    if not helper_name or not helper_name.strip():
        return CustomReturnStatementsResult(
            success=False,
            error="helper_name is required and cannot be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        cursor = conn.execute(
            """
            SELECT id, helper_name, statement, active, description
            FROM custom_return_statements
            WHERE helper_name = ? AND active = 1
            ORDER BY id
            """,
            (helper_name.strip(),)
        )
        rows = cursor.fetchall()
        conn.close()

        statements = tuple(
            CustomReturnStatement(
                id=row['id'],
                helper_name=row['helper_name'],
                statement=row['statement'],
                active=bool(row['active']),
                description=row['description']
            )
            for row in rows
        )

        return_stmts = get_return_statements("get_custom_return_statements")

        return CustomReturnStatementsResult(
            success=True,
            statements=statements,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return CustomReturnStatementsResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
