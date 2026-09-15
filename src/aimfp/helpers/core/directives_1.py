"""
AIMFP Helper Functions - Core Directives (Part 1)

Directive queries, search, and intent keyword operations for aimfp_core.db.

Helpers in this file:
- get_directive_by_name: Get specific directive by name
- get_all_directives: Load all directives for caching
- search_directives: Search directives with filters
- find_directive_by_intent: Map user intent to directives using keyword matching
- find_directives_by_intent_keyword: Find directive IDs by intent keywords
- get_directives_with_intent_keywords: Search by keywords with full directive objects
- add_directive_intent_keyword: Add intent keyword to directive
- remove_directive_intent_keyword: Remove intent keyword from directive
- get_directive_keywords: Get all keywords for a directive
- get_all_directive_keywords: Get list of all unique keywords
- get_all_intent_keywords_with_counts: Get keywords with usage counts
- get_all_directive_names: Get list of all directive names
- get_directives_by_category: Get all directives in a category
- get_directives_by_type: Get all directives by type
- get_fp_directive_index: Get lightweight FP directive index grouped by category
- get_directive_content: Read directive MD documentation file by name

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# Import core utilities
from ._common import (
    _open_core_connection,
    _open_preferences_connection,
    _open_project_connection,
    get_return_statements,
    rows_to_tuple,
    row_to_dict,
    DirectiveRecord,
    row_to_directive,
    json_to_tuple,
)

from ..utils import Result
from ..shared.fts_query import tokenize_search_terms, build_fts_match_expression, build_like_clause


# ============================================================================
# Constants
# ============================================================================

REFERENCE_DIR: str = str(
    Path(__file__).parent.parent.parent / "reference"
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class DirectiveResult:
    """Result of single directive lookup."""
    success: bool
    directive: Optional[DirectiveRecord] = None
    preferences: Tuple[Dict[str, Any], ...] = ()  # Directive-specific preferences from user_preferences.db
    linked_notes: Tuple[Dict[str, Any], ...] = ()  # Notes with send_with_directive=1 from project.db
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DirectivesResult:
    """Result of multiple directive query."""
    success: bool
    directives: Tuple[DirectiveRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentMatchResult:
    """Result of intent matching operation."""
    success: bool
    matches: Tuple[Dict[str, Any], ...] = ()  # {directive_id, name, confidence, matched_keywords}
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class KeywordsResult:
    """Result of keyword query operations."""
    success: bool
    keywords: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class KeywordCountsResult:
    """Result of keyword counts query."""
    success: bool
    keyword_counts: Tuple[Dict[str, Any], ...] = ()  # {keyword, count}
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DirectiveNamesResult:
    """Result of directive names query."""
    success: bool
    names: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DirectiveIndexResult:
    """Result of FP directive index query."""
    success: bool
    index: Dict[str, Tuple[str, ...]] = None  # {category: [directive_names]}
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()

    def __post_init__(self):
        # Handle None for frozen dataclass
        if self.index is None:
            object.__setattr__(self, 'index', {})


@dataclass(frozen=True)
class SimpleResult:
    """Result of simple operations (add/remove keyword)."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Effect Functions (Database Queries)
# ============================================================================

def _get_directive_preferences(project_root: str, directive_name: str) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Get directive-specific preferences from user_preferences.db.

    Queries the directive_preferences table in user_preferences.db by directive_name.
    Returns empty tuple if user_preferences.db does not exist or query fails.

    Args:
        project_root: Project root directory (for locating user_preferences.db)
        directive_name: Directive name to look up preferences for

    Returns:
        Tuple of preference dicts
    """
    try:
        conn = _open_preferences_connection(project_root)
        try:
            cursor = conn.execute(
                "SELECT * FROM directive_preferences WHERE directive_name = ? AND active = 1",
                (directive_name,)
            )
            return rows_to_tuple(cursor.fetchall())
        finally:
            conn.close()
    except (FileNotFoundError, Exception):
        return ()


def _get_directive_linked_notes(project_root: str, directive_name: str) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Get notes linked to a directive via send_with_directive flag.

    Queries project.db for notes where directive_name matches and
    send_with_directive=1. These notes travel with their directive
    as contextual reminders.

    Args:
        project_root: Project root directory (for locating project.db)
        directive_name: Directive name to look up linked notes for

    Returns:
        Tuple of note dicts (id, content, note_type, severity, created_at)
    """
    try:
        conn = _open_project_connection(project_root)
        try:
            cursor = conn.execute(
                """
                SELECT id, content, note_type, severity, created_at
                FROM notes
                WHERE directive_name = ?
                  AND send_with_directive = 1
                ORDER BY severity DESC, created_at DESC
                """,
                (directive_name,)
            )
            return rows_to_tuple(cursor.fetchall())
        finally:
            conn.close()
    except (FileNotFoundError, Exception):
        return ()


def _get_directive_categories(conn, directive_id: int) -> Tuple[str, ...]:
    """
    Effect: Get category names for a directive.

    Args:
        conn: Database connection
        directive_id: Directive ID

    Returns:
        Tuple of category names
    """
    cursor = conn.execute(
        """
        SELECT c.name FROM categories c
        JOIN directive_categories dc ON c.id = dc.category_id
        WHERE dc.directive_id = ?
        """,
        (directive_id,)
    )
    return tuple(row['name'] for row in cursor.fetchall())


def _get_directive_intent_keywords(conn, directive_id: int) -> Tuple[str, ...]:
    """
    Effect: Get intent keywords for a directive.

    Args:
        conn: Database connection
        directive_id: Directive ID

    Returns:
        Tuple of keyword strings
    """
    cursor = conn.execute(
        """
        SELECT ik.keyword FROM intent_keywords ik
        JOIN directives_intent_keywords dik ON ik.id = dik.keyword_id
        WHERE dik.directive_id = ?
        """,
        (directive_id,)
    )
    return tuple(row['keyword'] for row in cursor.fetchall())


# ============================================================================
# Pure Helper Functions
# ============================================================================

def _calculate_intent_confidence(
    user_request: str,
    directive_name: str,
    directive_description: Optional[str],
    intent_keywords: Tuple[str, ...]
) -> Tuple[float, Tuple[str, ...]]:
    """
    Pure: Calculate confidence score for intent matching.

    Uses simple keyword matching (case-insensitive).

    Args:
        user_request: User's natural language request
        directive_name: Directive name
        directive_description: Directive description
        intent_keywords: Directive's intent keywords

    Returns:
        Tuple of (confidence_score, matched_keywords)
    """
    request_lower = user_request.lower()
    matched = []

    # Check directive name
    if directive_name.lower() in request_lower:
        matched.append(directive_name)

    # Check description words
    if directive_description:
        desc_words = directive_description.lower().split()
        for word in desc_words:
            if len(word) > 3 and word in request_lower:
                matched.append(word)

    # Check intent keywords
    for keyword in intent_keywords:
        if keyword.lower() in request_lower:
            matched.append(keyword)

    # Calculate confidence (simple scoring)
    if not matched:
        return (0.0, ())

    # Higher confidence for more matches and keyword matches
    keyword_matches = len([m for m in matched if m in intent_keywords])
    base_score = min(len(matched) * 0.2, 0.6)
    keyword_bonus = min(keyword_matches * 0.15, 0.4)

    return (base_score + keyword_bonus, tuple(set(matched)))


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def get_directive_by_name(
    directive_name: str,
    project_root: Optional[str] = None
) -> DirectiveResult:
    """
    Get specific directive by name (high-frequency operation).

    When project_root is provided, also retrieves:
    - Directive-specific preferences from user_preferences.db
    - Linked notes (send_with_directive=1) from project.db

    Args:
        directive_name: Directive name to look up
        project_root: Optional project root for cross-DB queries.
            When provided, queries user_preferences.db for preferences
            and project.db for linked notes (send_with_directive=1).

    Returns:
        DirectiveResult with:
        - success: True if found
        - directive: DirectiveRecord if found
        - preferences: Tuple of preference dicts (from user_preferences.db)
        - linked_notes: Tuple of note dicts (from project.db, send_with_directive=1)
        - error: Error message if not found
        - return_statements: AI guidance for next steps

    Example:
        >>> result = get_directive_by_name("fp_purity")
        >>> result.success
        True
        >>> result.directive.type
        'fp'
        >>> result = get_directive_by_name("fp_purity", project_root="/path/to/project")
        >>> result.preferences  # from user_preferences.db
        >>> result.linked_notes  # from project.db where send_with_directive=1
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM directives WHERE name = ?",
                (directive_name,)
            )
            row = cursor.fetchone()

            if row is None:
                return DirectiveResult(
                    success=False,
                    error=f"Directive '{directive_name}' not found"
                )

            directive = row_to_directive(row)
            return_statements = get_return_statements("get_directive_by_name")

            # Cross-DB queries when project_root provided
            preferences: Tuple[Dict[str, Any], ...] = ()
            linked_notes: Tuple[Dict[str, Any], ...] = ()
            if project_root:
                preferences = _get_directive_preferences(project_root, directive_name)
                linked_notes = _get_directive_linked_notes(project_root, directive_name)

            return DirectiveResult(
                success=True,
                directive=directive,
                preferences=preferences,
                linked_notes=linked_notes,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectiveResult(success=False, error=str(e))
    except Exception as e:
        return DirectiveResult(success=False, error=f"Query failed: {str(e)}")


def get_all_directives() -> DirectivesResult:
    """
    Load all directives for caching (special orchestrator).

    Returns all directive records. Useful for startup caching.

    Returns:
        DirectivesResult with:
        - success: True if query succeeded
        - directives: Tuple of DirectiveRecord objects
        - error: Error message if failed
        - return_statements: AI guidance for next steps

    Example:
        >>> result = get_all_directives()
        >>> result.success
        True
        >>> len(result.directives)
        45
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                """SELECT id, name, type, level, parent_directive, description,
                          md_file_path, confidence_threshold
                   FROM directives ORDER BY type, name"""
            )
            rows = cursor.fetchall()
            directives = tuple(row_to_directive(row) for row in rows)
            return_statements = get_return_statements("get_all_directives")
            return_statements = (
                *return_statements,
                "Workflow and roadblocks JSON excluded for token efficiency. "
                "Query individual directives by name for full workflow details.",
            )

            return DirectivesResult(
                success=True,
                directives=directives,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectivesResult(success=False, error=str(e))
    except Exception as e:
        return DirectivesResult(success=False, error=f"Query failed: {str(e)}")


def search_directives(
    keyword: Optional[str] = None,
    type: Optional[str] = None,
    category: Optional[str] = None
) -> DirectivesResult:
    """
    Search directives with filters (orchestrator).

    Searches directive names, descriptions, and intent_keywords.

    Args:
        keyword: Keyword to search in names, descriptions, intent_keywords
        type: Directive type filter ('fp', 'project', 'git', 'user_system', 'user_preference')
        category: Directive category filter (e.g., 'purity', 'error_handling')

    Returns:
        DirectivesResult with:
        - success: True if query succeeded
        - directives: Tuple of matching DirectiveRecord objects
        - error: Error message if failed
        - return_statements: AI guidance for next steps

    Example:
        >>> result = search_directives(keyword="purity")
        >>> result.success
        True
        >>> [d.name for d in result.directives]
        ['fp_purity', ...]

        >>> result = search_directives(type="fp", category="error_handling")
    """
    try:
        conn = _open_core_connection()

        try:
            # Build query based on filters
            conditions = []
            params = []
            joins = ""

            # Join with categories if category filter
            if category:
                joins += """
                    JOIN directive_categories dc ON d.id = dc.directive_id
                    JOIN categories c ON dc.category_id = c.id
                """
                conditions.append("c.name = ?")
                params.append(category)

            # Add type filter
            if type:
                conditions.append("d.type = ?")
                params.append(type)

            # Keyword search: OR-joined FTS5 terms over name/description, plus
            # intent keywords; per-term LIKE when the FTS5 table is missing
            terms = tokenize_search_terms(keyword) if keyword else ()
            if keyword and not terms:
                rows = []
            else:
                keyword_sql, keyword_params = "", ()
                if terms:
                    ik_sql, ik_params = build_like_clause(('ik.keyword',), terms)
                    ik_subquery = (
                        "d.id IN (SELECT dik.directive_id FROM directives_intent_keywords dik "
                        f"JOIN intent_keywords ik ON dik.keyword_id = ik.id WHERE {ik_sql})"
                    )
                    keyword_sql = (
                        "(d.id IN (SELECT rowid FROM directives_fts WHERE directives_fts MATCH ?) "
                        f"OR {ik_subquery})"
                    )
                    keyword_params = (build_fts_match_expression(terms), *ik_params)

                def _run(kw_sql: str, kw_params: tuple, ranked: bool) -> list:
                    # Ranked: order by bm25 relevance of name/description, keyword-only hits last
                    rank_join, rank_params, order = "", (), "d.type, d.name"
                    if ranked:
                        rank_join = (
                            " LEFT JOIN (SELECT rowid AS fts_id, bm25(directives_fts, 10.0, 1.0) AS fts_rank FROM directives_fts "
                            "WHERE directives_fts MATCH ?) fr ON fr.fts_id = d.id"
                        )
                        rank_params = (build_fts_match_expression(terms),)
                        order = "fr.fts_rank IS NULL, fr.fts_rank, d.type, d.name"
                    all_conditions = conditions + ([kw_sql] if kw_sql else [])
                    query = f"SELECT DISTINCT d.* FROM directives d{joins}{rank_join}"
                    if all_conditions:
                        query += " WHERE " + " AND ".join(all_conditions)
                    query += f" ORDER BY {order}"
                    return conn.execute(query, (*rank_params, *params, *kw_params)).fetchall()

                try:
                    rows = _run(keyword_sql, keyword_params, ranked=bool(terms))
                except sqlite3.OperationalError:
                    if not terms:
                        raise
                    like_sql, like_params = build_like_clause(('d.name', 'd.description'), terms)
                    rows = _run(f"({like_sql} OR {ik_subquery})", (*like_params, *ik_params), ranked=False)

            directives = tuple(row_to_directive(row) for row in rows)
            return_statements = get_return_statements("search_directives")

            return DirectivesResult(
                success=True,
                directives=directives,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectivesResult(success=False, error=str(e))
    except Exception as e:
        return DirectivesResult(success=False, error=f"Query failed: {str(e)}")


def find_directive_by_intent(
    user_request: str,
    threshold: float = 0.7
) -> IntentMatchResult:
    """
    Map user intent to directives using NLP/keyword matching.

    Analyzes user's natural language request to find matching directives.

    Args:
        user_request: User's natural language request describing behavior
        threshold: Confidence threshold (default 0.7)

    Returns:
        IntentMatchResult with:
        - success: True if query succeeded
        - matches: Tuple of match dicts {directive_id, name, confidence, matched_keywords}
        - error: Error message if failed
        - return_statements: AI guidance for next steps

    Example:
        >>> result = find_directive_by_intent("always add docstrings")
        >>> result.success
        True
        >>> result.matches[0]['name']
        'user_preferences_update'
    """
    try:
        conn = _open_core_connection()

        try:
            # Get all directives with their keywords
            cursor = conn.execute("SELECT * FROM directives")
            directives = cursor.fetchall()

            matches = []
            for directive in directives:
                keywords = _get_directive_intent_keywords(conn, directive['id'])

                confidence, matched = _calculate_intent_confidence(
                    user_request,
                    directive['name'],
                    directive['description'],
                    keywords
                )

                if confidence >= threshold:
                    matches.append({
                        'directive_id': directive['id'],
                        'name': directive['name'],
                        'confidence': round(confidence, 2),
                        'matched_keywords': matched
                    })

            # Sort by confidence descending
            matches.sort(key=lambda x: x['confidence'], reverse=True)
            return_statements = get_return_statements("find_directive_by_intent")

            return IntentMatchResult(
                success=True,
                matches=tuple(matches),
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return IntentMatchResult(success=False, error=str(e))
    except Exception as e:
        return IntentMatchResult(success=False, error=f"Query failed: {str(e)}")


def find_directives_by_intent_keyword(
    keywords: List[str],
    match_mode: str = "any"
) -> DirectiveNamesResult:
    """
    Find directive IDs matching one or more intent keywords (direct lookup).

    Args:
        keywords: Single keyword or array of keywords
        match_mode: "any" for OR logic, "all" for AND logic

    Returns:
        DirectiveNamesResult with directive names matching keywords

    Example:
        >>> result = find_directives_by_intent_keyword(["purity", "immutable"])
        >>> result.names
        ('fp_purity', 'fp_immutability', ...)
    """
    try:
        conn = _open_core_connection()

        try:
            if match_mode == "all":
                # AND logic - directive must have ALL keywords
                placeholders = ",".join("?" * len(keywords))
                cursor = conn.execute(
                    f"""
                    SELECT d.name FROM directives d
                    JOIN directives_intent_keywords dik ON d.id = dik.directive_id
                    JOIN intent_keywords ik ON dik.keyword_id = ik.id
                    WHERE ik.keyword IN ({placeholders})
                    GROUP BY d.id
                    HAVING COUNT(DISTINCT ik.keyword) = ?
                    """,
                    tuple(keywords) + (len(keywords),)
                )
            else:
                # OR logic (any) - directive has ANY of the keywords
                placeholders = ",".join("?" * len(keywords))
                cursor = conn.execute(
                    f"""
                    SELECT DISTINCT d.name FROM directives d
                    JOIN directives_intent_keywords dik ON d.id = dik.directive_id
                    JOIN intent_keywords ik ON dik.keyword_id = ik.id
                    WHERE ik.keyword IN ({placeholders})
                    """,
                    tuple(keywords)
                )

            names = tuple(row['name'] for row in cursor.fetchall())
            return_statements = get_return_statements("find_directives_by_intent_keyword")

            return DirectiveNamesResult(
                success=True,
                names=names,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectiveNamesResult(success=False, error=str(e))
    except Exception as e:
        return DirectiveNamesResult(success=False, error=f"Query failed: {str(e)}")


def get_directives_with_intent_keywords(
    keywords: List[str],
    match_mode: str = "any",
    include_keyword_matches: bool = False
) -> DirectivesResult:
    """
    Search directives by intent keywords and return full directive objects.

    Args:
        keywords: Single keyword or array of keywords
        match_mode: "any" for OR logic, "all" for AND logic
        include_keyword_matches: Include which keywords matched

    Returns:
        DirectivesResult with full directive objects

    Example:
        >>> result = get_directives_with_intent_keywords(["purity"], match_mode="any")
        >>> result.success
        True
        >>> len(result.directives)
        3
    """
    try:
        conn = _open_core_connection()

        try:
            if match_mode == "all":
                placeholders = ",".join("?" * len(keywords))
                cursor = conn.execute(
                    f"""
                    SELECT d.* FROM directives d
                    JOIN directives_intent_keywords dik ON d.id = dik.directive_id
                    JOIN intent_keywords ik ON dik.keyword_id = ik.id
                    WHERE ik.keyword IN ({placeholders})
                    GROUP BY d.id
                    HAVING COUNT(DISTINCT ik.keyword) = ?
                    """,
                    tuple(keywords) + (len(keywords),)
                )
            else:
                placeholders = ",".join("?" * len(keywords))
                cursor = conn.execute(
                    f"""
                    SELECT DISTINCT d.* FROM directives d
                    JOIN directives_intent_keywords dik ON d.id = dik.directive_id
                    JOIN intent_keywords ik ON dik.keyword_id = ik.id
                    WHERE ik.keyword IN ({placeholders})
                    """,
                    tuple(keywords)
                )

            rows = cursor.fetchall()
            directives = tuple(row_to_directive(row) for row in rows)
            return_statements = get_return_statements("get_directives_with_intent_keywords")

            return DirectivesResult(
                success=True,
                directives=directives,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectivesResult(success=False, error=str(e))
    except Exception as e:
        return DirectivesResult(success=False, error=f"Query failed: {str(e)}")




def get_directive_keywords(directive_id: int) -> KeywordsResult:
    """
    Get all intent keywords for a specific directive.

    Args:
        directive_id: Directive ID

    Returns:
        KeywordsResult with tuple of keyword strings

    Example:
        >>> result = get_directive_keywords(1)
        >>> result.keywords
        ('purity', 'pure function', 'side effects', ...)
    """
    try:
        conn = _open_core_connection()

        try:
            keywords = _get_directive_intent_keywords(conn, directive_id)
            return_statements = get_return_statements("get_directive_keywords")

            return KeywordsResult(
                success=True,
                keywords=keywords,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return KeywordsResult(success=False, error=str(e))
    except Exception as e:
        return KeywordsResult(success=False, error=f"Query failed: {str(e)}")


def get_all_directive_keywords() -> KeywordsResult:
    """
    Get list of all unique intent keywords available for searching.

    Returns:
        KeywordsResult with tuple of all unique keywords

    Example:
        >>> result = get_all_directive_keywords()
        >>> result.keywords
        ('purity', 'immutability', 'error handling', ...)
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT DISTINCT keyword FROM intent_keywords ORDER BY keyword"
            )
            keywords = tuple(row['keyword'] for row in cursor.fetchall())
            return_statements = get_return_statements("get_all_directive_keywords")

            return KeywordsResult(
                success=True,
                keywords=keywords,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return KeywordsResult(success=False, error=str(e))
    except Exception as e:
        return KeywordsResult(success=False, error=f"Query failed: {str(e)}")


def get_all_intent_keywords_with_counts() -> KeywordCountsResult:
    """
    Get all unique intent keywords with usage counts for analytics.

    Returns:
        KeywordCountsResult with tuple of {keyword, count} dicts

    Example:
        >>> result = get_all_intent_keywords_with_counts()
        >>> result.keyword_counts
        ({'keyword': 'purity', 'count': 5}, ...)
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                """
                SELECT ik.keyword, COUNT(dik.directive_id) as count
                FROM intent_keywords ik
                LEFT JOIN directives_intent_keywords dik ON ik.id = dik.keyword_id
                GROUP BY ik.id
                ORDER BY count DESC, ik.keyword
                """
            )
            counts = tuple(
                {'keyword': row['keyword'], 'count': row['count']}
                for row in cursor.fetchall()
            )
            return_statements = get_return_statements("get_all_intent_keywords_with_counts")

            return KeywordCountsResult(
                success=True,
                keyword_counts=counts,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return KeywordCountsResult(success=False, error=str(e))
    except Exception as e:
        return KeywordCountsResult(success=False, error=f"Query failed: {str(e)}")


def get_all_directive_names(
    types: Optional[List[str]] = None
) -> DirectiveNamesResult:
    """
    Get list of all directive names (or filtered by types).

    Returns names only, NOT full directive data. AI queries by name when needs details.

    Args:
        types: Optional array of types to filter: ['fp', 'project', 'git', ...]
               If not provided, returns ALL directive names.

    Returns:
        DirectiveNamesResult with tuple of directive names

    Example:
        >>> result = get_all_directive_names()
        >>> result.names
        ('aimfp_run', 'aimfp_status', 'fp_purity', ...)

        >>> result = get_all_directive_names(types=['fp'])
        >>> result.names
        ('fp_purity', 'fp_immutability', ...)
    """
    try:
        conn = _open_core_connection()

        try:
            if types:
                placeholders = ",".join("?" * len(types))
                cursor = conn.execute(
                    f"SELECT name FROM directives WHERE type IN ({placeholders}) ORDER BY type, name",
                    tuple(types)
                )
            else:
                cursor = conn.execute(
                    "SELECT name FROM directives ORDER BY type, name"
                )

            names = tuple(row['name'] for row in cursor.fetchall())
            return_statements = get_return_statements("get_all_directive_names")

            return DirectiveNamesResult(
                success=True,
                names=names,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectiveNamesResult(success=False, error=str(e))
    except Exception as e:
        return DirectiveNamesResult(success=False, error=f"Query failed: {str(e)}")


def get_directives_by_category(category_name: str) -> DirectivesResult:
    """
    Get all directives in a category.

    Args:
        category_name: Category name (e.g., 'purity', 'error_handling')

    Returns:
        DirectivesResult with directives in the category

    Example:
        >>> result = get_directives_by_category("purity")
        >>> result.success
        True
        >>> [d.name for d in result.directives]
        ['fp_purity', 'fp_immutability', ...]
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                """
                SELECT d.* FROM directives d
                JOIN directive_categories dc ON d.id = dc.directive_id
                JOIN categories c ON dc.category_id = c.id
                WHERE c.name = ?
                ORDER BY d.type, d.name
                """,
                (category_name,)
            )
            rows = cursor.fetchall()
            directives = tuple(row_to_directive(row) for row in rows)
            return_statements = get_return_statements("get_directives_by_category")

            return DirectivesResult(
                success=True,
                directives=directives,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectivesResult(success=False, error=str(e))
    except Exception as e:
        return DirectivesResult(success=False, error=f"Query failed: {str(e)}")


def get_directives_by_type(
    type: str,
    include_md_content: bool = False
) -> DirectivesResult:
    """
    Get all directives filtered by type.

    Args:
        type: One of: 'fp', 'project', 'git', 'user_system', 'user_preference'
        include_md_content: Include full MD file content (default False)

    Returns:
        DirectivesResult with directives of the specified type

    Example:
        >>> result = get_directives_by_type("fp")
        >>> result.success
        True
        >>> len(result.directives)
        15
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM directives WHERE type = ? ORDER BY name",
                (type,)
            )
            rows = cursor.fetchall()
            directives = tuple(row_to_directive(row) for row in rows)
            return_statements = get_return_statements("get_directives_by_type")

            return DirectivesResult(
                success=True,
                directives=directives,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectivesResult(success=False, error=str(e))
    except Exception as e:
        return DirectivesResult(success=False, error=f"Query failed: {str(e)}")


def get_fp_directive_index() -> DirectiveIndexResult:
    """
    Get lightweight FP directive index grouped by category (names only).

    Returns FP directives organized by category for quick reference.
    Use search_directives(type='fp') or get_directive_by_name(name) for full details.

    Returns:
        DirectiveIndexResult with:
        - success: True if query succeeded
        - index: Dict of {category_name: (directive_names...)}
        - return_statements: AI guidance about FP directives being reference material

    Example:
        >>> result = get_fp_directive_index()
        >>> result.success
        True
        >>> result.index
        {'error_handling': ('fp_optionals', 'fp_result_types'), 'purity': ('fp_purity', ...)}
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                """
                SELECT d.name, c.name as category FROM directives d
                JOIN directive_categories dc ON d.id = dc.directive_id
                JOIN categories c ON dc.category_id = c.id
                WHERE d.type = 'fp'
                ORDER BY c.name, d.name
                """
            )

            # Group by category
            index: Dict[str, List[str]] = {}
            for row in cursor.fetchall():
                category = row['category']
                if category not in index:
                    index[category] = []
                index[category].append(row['name'])

            # Convert lists to tuples for immutability
            immutable_index = {k: tuple(v) for k, v in index.items()}
            return_statements = get_return_statements("get_fp_directive_index")

            return DirectiveIndexResult(
                success=True,
                index=immutable_index,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectiveIndexResult(success=False, error=str(e))
    except Exception as e:
        return DirectiveIndexResult(success=False, error=f"Query failed: {str(e)}")


def get_directive_content(directive_name: str) -> Result:
    """
    Read and return the full MD documentation for a directive.

    Looks up the directive in aimfp_core.db to get its md_file_path,
    resolves to an absolute path within the installed package, reads
    the file, and returns both content and absolute path.

    Args:
        directive_name: Directive name (e.g., 'fp_purity', 'aimfp_init')

    Returns:
        Result with data={
            content: str (full markdown text),
            absolute_path: str (resolved file path on disk),
            token_estimate: int (approximate token count),
            directive_name: str (echoed back for reference)
        }

    On error:
        Result with error message if directive not found, md_file_path
        is null, or file cannot be read.

    Example:
        >>> result = get_directive_content("fp_purity")
        >>> result.success
        True
        >>> result.data['token_estimate']
        4200
        >>> result.data['absolute_path']
        '/path/to/site-packages/aimfp/reference/directives/fp_purity.md'
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT md_file_path FROM directives WHERE name = ?",
                (directive_name,)
            )
            row = cursor.fetchone()

            if row is None:
                return Result(
                    success=False,
                    error=f"Directive '{directive_name}' not found in aimfp_core.db",
                )

            md_file_path = row['md_file_path']

            if not md_file_path:
                return Result(
                    success=False,
                    error=f"Directive '{directive_name}' has no md_file_path set",
                )

        finally:
            conn.close()

        # Resolve relative path to absolute using package location
        absolute_path = Path(REFERENCE_DIR) / md_file_path

        if not absolute_path.is_file():
            return Result(
                success=False,
                error=f"MD file not found at: {absolute_path}. "
                      f"Expected relative path '{md_file_path}' under src/aimfp/reference/",
            )

        content = absolute_path.read_text(encoding="utf-8")
        token_estimate = len(content) // 4

        return Result(
            success=True,
            data={
                'content': content,
                'absolute_path': str(absolute_path),
                'token_estimate': token_estimate,
                'directive_name': directive_name,
            },
            return_statements=get_return_statements("get_directive_content"),
        )

    except Exception as e:
        return Result(
            success=False,
            error=f"Error reading directive content: {str(e)}",
        )
