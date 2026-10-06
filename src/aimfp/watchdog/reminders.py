"""
AIMFP Watchdog - Reminder Generation and JSON Management

Pure functions for creating reminder data structures,
effect functions for reading/writing the reminders JSON file.
"""

import time
from typing import Any, Dict, Optional, Tuple

from ..wrappers.file_ops import (
    _effect_read_json,
    _effect_write_json_atomic,
)
from .config import REMINDER_STRUCTURE_PREFIX, STRUCTURE_REMINDERS


# ============================================================================
# Pure Functions
# ============================================================================

def create_reminder(
    reminder_type: str,
    severity: str,
    file_path: str,
    message: str,
) -> Dict[str, str]:
    """Pure: Create a single reminder dict."""
    return {
        'type': reminder_type,
        'severity': severity,
        'file': file_path,
        'message': message,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def build_reminders_document(
    reminders: Tuple[Dict[str, str], ...],
) -> Dict[str, Any]:
    """Pure: Build the full reminders JSON document structure."""
    return {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'reminders': list(reminders),
    }


def build_empty_document() -> Dict[str, Any]:
    """Pure: Build an empty reminders document."""
    return build_reminders_document(())


def merge_reminders(
    existing: Tuple[Dict[str, str], ...],
    new: Tuple[Dict[str, str], ...],
) -> Tuple[Dict[str, str], ...]:
    """Pure: Merge existing reminders with new ones."""
    return existing + new


def build_structure_reminders(health: Dict[str, Any]) -> Tuple[Dict[str, str], ...]:
    """
    Pure: One reminder per reported structure gap item, plus an 'N more' reminder
    per gap whose list was capped.

    Args:
        health: structure_health gaps (build_structure_gaps output)

    Returns:
        Tuple of reminder dicts (types prefixed REMINDER_STRUCTURE_PREFIX)
    """
    reminders = []
    for gap, (reminder_type, severity, template) in STRUCTURE_REMINDERS.items():
        entry = health.get(gap) or {}
        items = entry.get('items') or ()
        reminders.extend(
            create_reminder(reminder_type, severity, item.get('path', ''), template.format(**item))
            for item in items
        )
        hidden = (entry.get('total') or 0) - len(items)
        if hidden > 0:
            reminders.append(create_reminder(
                reminder_type, severity, '',
                f"...and {hidden} more {gap.replace('_', ' ')}. Call get_structure_health() for the full list."))
    return tuple(reminders)


def replace_reminders_by_prefix(
    existing: Tuple[Dict[str, str], ...],
    prefix: str,
    new: Tuple[Dict[str, str], ...],
) -> Tuple[Dict[str, str], ...]:
    """Pure: Drop existing reminders whose type starts with prefix, then append new."""
    return tuple(r for r in existing if not str(r.get('type', '')).startswith(prefix)) + new


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_read_reminders(reminders_path: str) -> Tuple[Dict[str, str], ...]:
    """
    Effect: Read reminders from JSON file.

    Returns empty tuple if file doesn't exist or is invalid.
    """
    data = _effect_read_json(reminders_path)
    if data is None:
        return ()
    raw_reminders = data.get('reminders', [])
    if not isinstance(raw_reminders, list):
        return ()
    return tuple(raw_reminders)


def _effect_write_reminders(
    reminders_path: str,
    reminders: Tuple[Dict[str, str], ...],
) -> bool:
    """
    Effect: Write reminders to JSON file atomically.

    Returns True on success.
    """
    doc = build_reminders_document(reminders)
    return _effect_write_json_atomic(reminders_path, doc)


def _effect_clear_reminders(reminders_path: str) -> bool:
    """
    Effect: Clear the reminders file (write empty document).

    Returns True on success.
    """
    doc = build_empty_document()
    return _effect_write_json_atomic(reminders_path, doc)


def _effect_append_reminders(
    reminders_path: str,
    new_reminders: Tuple[Dict[str, str], ...],
) -> bool:
    """
    Effect: Read existing reminders, append new ones, write back atomically.

    Returns True on success.
    """
    existing = _effect_read_reminders(reminders_path)
    merged = merge_reminders(existing, new_reminders)
    return _effect_write_reminders(reminders_path, merged)


def _effect_replace_structure_reminders(
    reminders_path: str,
    structure_reminders: Tuple[Dict[str, str], ...],
) -> bool:
    """
    Effect: Swap the structure_* reminder set for a freshly computed one, leaving
    every other reminder untouched. Structural gaps therefore persist across
    clear_watchdog until they are fixed, and vanish once they are.

    Returns True on success.
    """
    existing = _effect_read_reminders(reminders_path)
    merged = replace_reminders_by_prefix(existing, REMINDER_STRUCTURE_PREFIX, structure_reminders)
    if merged == existing:
        return True
    return _effect_write_reminders(reminders_path, merged)
