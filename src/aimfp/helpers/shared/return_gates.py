"""
AIMFP Helper Functions - Return Statement Gates

A return statement should reach the AI only when the data it talks about is
in the result. Statements opt in with a gate tag at the start:

    [when:notices] Deliver each notice ...            -> data['notices'] present and non-empty
    [when:scheduled_backup.due] PROMPT the user ...  -> nested path, truthy
    [when:tier=quick] Lost context? ...              -> case-insensitive equality

Untagged statements always pass, so every helper that has no tags (and every
custom user statement) behaves exactly as before. Gating is applied by the
helper that builds the payload, never by the transport, so embedding hosts
that call helpers in-process get the same statements as MCP clients.

A bundle (aimfp_run) carries its own statements plus, for each bundled
section actually present, that section's owning tool's statements gated
against the section's own data: one call, its own statements, nothing more.
"""

import re
from typing import Any, Dict, Final, Optional, Tuple


# ============================================================================
# Constants
# ============================================================================

_GATE_PATTERN: Final = re.compile(r'^\s*\[when:([A-Za-z0-9_.]+)(?:=([^\]]*))?\]\s*')


# ============================================================================
# Pure Functions
# ============================================================================

def parse_gate(statement: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Pure: Split a statement into (path, expected value, text).

    Args:
        statement: Return statement, optionally starting with a gate tag

    Returns:
        (path or None, expected or None, statement text without the tag)
    """
    match = _GATE_PATTERN.match(statement or '')
    if not match:
        return (None, None, statement)
    return (match.group(1), match.group(2), statement[match.end():])


def value_at(data: Any, path: str) -> Any:
    """Pure: Value at a dotted path through nested dicts; None if any segment is missing."""
    current = data
    for segment in path.split('.'):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def gate_open(data: Any, path: str, expected: Optional[str] = None) -> bool:
    """
    Pure: Whether a gate passes for a payload.

    Without an expected value the value must be present and non-empty (None,
    False, 0, '' and empty containers fail). With one, the value's string form
    must equal it, ignoring case (so 'false' matches False).
    """
    value = value_at(data, path)
    if expected is None:
        return bool(value)
    return value is not None and str(value).strip().lower() == expected.strip().lower()


def gate_return_statements(statements: Tuple[str, ...], data: Any) -> Tuple[str, ...]:
    """
    Pure: The statements whose gate passes for this payload, tags stripped.

    Args:
        statements: Statements as stored (gated or not)
        data: The result payload they accompany

    Returns:
        Statements to send, in order
    """
    return tuple(
        text
        for path, expected, text in (parse_gate(s) for s in statements or ())
        if path is None or gate_open(data, path, expected)
    )


def section_return_statements(
    data: Dict[str, Any],
    owners: Tuple[Tuple[str, str, Tuple[str, ...]], ...],
) -> Tuple[str, ...]:
    """
    Pure: Owning tools' statements for the bundled sections that are present.

    Each section's statements are gated against that section's own data, so a
    tool's '[when:...]' tags keep the meaning they have when it is called alone.

    Args:
        data: The bundle payload
        owners: (section path, owning helper name, that helper's statements)

    Returns:
        Statements in owner order, duplicates dropped
    """
    collected = tuple(
        statement
        for path, _helper, statements in owners
        for section in (value_at(data, path),)
        if section
        for statement in gate_return_statements(
            statements, section if isinstance(section, dict) else {path: section})
    )
    return tuple(dict.fromkeys(collected))
