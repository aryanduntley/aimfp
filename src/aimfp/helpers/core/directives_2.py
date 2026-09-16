"""
AIMFP Helper Functions - Core Directives (Part 2)

Helper function queries and category operations for aimfp_core.db.

Helpers in this file:
- get_helper_by_name: Get specific helper function details
- get_helpers_by_database: Get all helpers for a specific database
- get_helpers_are_tool: Get all MCP tools (is_tool=true)
- get_hooks: Get AIMFP's library surface for code outside AIMFP (never MCP tools)
- get_helpers_are_sub: Get all sub-helpers (is_sub_helper=true)
- get_helpers_for_directive: Get all helpers used by a directive
- get_directives_for_helper: Get all directives that use a helper
- get_category_by_name: Get category by name
- get_categories: Get all directive categories

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.
"""

from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, List

# Import core utilities
from ._common import (
    _open_core_connection,
    get_return_statements,
    rows_to_tuple,
    HelperRecord,
    CategoryRecord,
    DirectiveRecord,
    row_to_helper,
    row_to_category,
    row_to_directive,
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class HelperResult:
    """Result of single helper lookup."""
    success: bool
    helper: Optional[HelperRecord] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpersResult:
    """Result of multiple helper query."""
    success: bool
    helpers: Tuple[HelperRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CategoryResult:
    """Result of single category lookup."""
    success: bool
    category: Optional[CategoryRecord] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CategoriesResult:
    """Result of multiple category query."""
    success: bool
    categories: Tuple[CategoryRecord, ...] = ()
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
class DirectiveHelpersResult:
    """Result of helpers-for-directive query."""
    success: bool
    helpers: Tuple[Dict[str, Any], ...] = ()  # Includes execution_context, sequence_order
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def get_helper_by_name(helper_name: str) -> HelperResult:
    """
    Get specific helper function details (high-frequency).

    Args:
        helper_name: Helper name to look up

    Returns:
        HelperResult with:
        - success: True if found
        - helper: HelperRecord if found
        - error: Error message if not found
        - return_statements: AI guidance for next steps

    Example:
        >>> result = get_helper_by_name("reserve_file")
        >>> result.success
        True
        >>> result.helper.purpose
        'Reserve file ID for naming before creation'
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM helper_functions WHERE name = ?",
                (helper_name,)
            )
            row = cursor.fetchone()

            if row is None:
                return HelperResult(
                    success=False,
                    error=f"Helper '{helper_name}' not found"
                )

            helper = row_to_helper(row)
            return_statements = get_return_statements("get_helper_by_name")

            return HelperResult(
                success=True,
                helper=helper,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return HelperResult(success=False, error=str(e))
    except Exception as e:
        return HelperResult(success=False, error=f"Query failed: {str(e)}")


def get_helpers_by_database(target_database: str) -> HelpersResult:
    """
    Get all helpers for a specific database.

    Args:
        target_database: Database name ('core', 'project', 'user_preferences', 'user_directives')

    Returns:
        HelpersResult with helpers for the specified database

    Example:
        >>> result = get_helpers_by_database("project")
        >>> result.success
        True
        >>> len(result.helpers)
        119
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM helper_functions WHERE target_database = ? ORDER BY name",
                (target_database,)
            )
            rows = cursor.fetchall()
            helpers = tuple(row_to_helper(row) for row in rows)
            return_statements = get_return_statements("get_helpers_by_database")

            return HelpersResult(
                success=True,
                helpers=helpers,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return HelpersResult(success=False, error=str(e))
    except Exception as e:
        return HelpersResult(success=False, error=f"Query failed: {str(e)}")


def get_helpers_are_tool() -> HelpersResult:
    """
    Get all MCP tools (is_tool=true).

    Returns helpers that are exposed as MCP tools for AI invocation.

    Returns:
        HelpersResult with all MCP tool helpers

    Example:
        >>> result = get_helpers_are_tool()
        >>> result.success
        True
        >>> len(result.helpers)
        150  # Approximate MCP tool count
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM helper_functions WHERE is_tool = 1 ORDER BY target_database, name"
            )
            rows = cursor.fetchall()
            helpers = tuple(row_to_helper(row) for row in rows)
            return_statements = get_return_statements("get_helpers_are_tool")

            return HelpersResult(
                success=True,
                helpers=helpers,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return HelpersResult(success=False, error=str(e))
    except Exception as e:
        return HelpersResult(success=False, error=f"Query failed: {str(e)}")


def get_hooks() -> HelpersResult:
    """
    Get AIMFP's library surface for code running OUTSIDE AIMFP.

    Hooks are NOT MCP tools and you cannot call them. They are imported and
    called by code that is not AIMFP - a generated Use Case 2 automation
    runner in the user's project, firing from cron or systemd with no AI
    session open. Your relationship to them is that you WRITE CODE CALLING
    THEM (during user_directive_implement), which is why this returns an
    import path and usage guidance rather than a callable tool listing.

    Queries is_hook = 1 affirmatively. It replaced an earlier query for
    "is_tool = 0 AND is_sub_helper = 0", which described hooks only as the
    absence of two other flags and therefore returned an empty list silently
    for as long as nothing populated that gap.

    Returns:
        HelpersResult with every registered hook

    Example:
        >>> result = get_hooks()
        >>> result.success
        True
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                """
                SELECT * FROM helper_functions
                WHERE is_hook = 1
                ORDER BY target_database, name
                """
            )
            rows = cursor.fetchall()
            helpers = tuple(row_to_helper(row) for row in rows)
            return_statements = get_return_statements("get_hooks")

            return HelpersResult(
                success=True,
                helpers=helpers,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return HelpersResult(success=False, error=str(e))
    except Exception as e:
        return HelpersResult(success=False, error=f"Query failed: {str(e)}")


def get_helpers_are_sub() -> HelpersResult:
    """
    Get all sub-helpers (is_sub_helper=true).

    Returns helpers that are internal implementation details,
    called by other helpers rather than directly by AI.

    Returns:
        HelpersResult with all sub-helpers

    Example:
        >>> result = get_helpers_are_sub()
        >>> result.success
        True
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM helper_functions WHERE is_sub_helper = 1 ORDER BY target_database, name"
            )
            rows = cursor.fetchall()
            helpers = tuple(row_to_helper(row) for row in rows)
            return_statements = get_return_statements("get_helpers_are_sub")

            return HelpersResult(
                success=True,
                helpers=helpers,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return HelpersResult(success=False, error=str(e))
    except Exception as e:
        return HelpersResult(success=False, error=f"Query failed: {str(e)}")


def get_helpers_for_directive(
    directive_name: str,
    include_helpers_data: bool = True
) -> DirectiveHelpersResult:
    """
    Get all helpers used by a directive.

    Args:
        directive_name: Directive name
        include_helpers_data: If true, includes full helper_functions data

    Returns:
        DirectiveHelpersResult with helpers used by the directive

    Example:
        >>> result = get_helpers_for_directive("project_file_write", include_helpers_data=True)
        >>> result.success
        True
        >>> result.helpers[0]['helper_name']
        'reserve_file'
    """
    try:
        conn = _open_core_connection()

        try:
            if include_helpers_data:
                cursor = conn.execute(
                    """
                    SELECT
                        dh.execution_context,
                        dh.sequence_order,
                        dh.is_required,
                        dh.parameters_mapping,
                        dh.description as usage_description,
                        hf.*
                    FROM directive_helpers dh
                    JOIN helper_functions hf ON dh.helper_function_id = hf.id
                    JOIN directives d ON dh.directive_id = d.id
                    WHERE d.name = ?
                    ORDER BY dh.sequence_order
                    """,
                    (directive_name,)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT
                        hf.name as helper_name,
                        dh.execution_context,
                        dh.sequence_order,
                        dh.is_required,
                        dh.description as usage_description
                    FROM directive_helpers dh
                    JOIN helper_functions hf ON dh.helper_function_id = hf.id
                    JOIN directives d ON dh.directive_id = d.id
                    WHERE d.name = ?
                    ORDER BY dh.sequence_order
                    """,
                    (directive_name,)
                )

            helpers = rows_to_tuple(cursor.fetchall())
            return_statements = get_return_statements("get_helpers_for_directive")

            return DirectiveHelpersResult(
                success=True,
                helpers=helpers,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return DirectiveHelpersResult(success=False, error=str(e))
    except Exception as e:
        return DirectiveHelpersResult(success=False, error=f"Query failed: {str(e)}")


def get_directives_for_helper(helper_name: str) -> DirectivesResult:
    """
    Get all directives that use a helper (reciprocal lookup).

    Args:
        helper_name: Helper name

    Returns:
        DirectivesResult with directives using this helper

    Example:
        >>> result = get_directives_for_helper("reserve_file")
        >>> result.success
        True
        >>> [d.name for d in result.directives]
        ['project_file_write', 'project_reserve_finalize', ...]
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                """
                SELECT DISTINCT d.* FROM directives d
                JOIN directive_helpers dh ON d.id = dh.directive_id
                JOIN helper_functions hf ON dh.helper_function_id = hf.id
                WHERE hf.name = ?
                ORDER BY d.type, d.name
                """,
                (helper_name,)
            )
            rows = cursor.fetchall()
            directives = tuple(row_to_directive(row) for row in rows)
            return_statements = get_return_statements("get_directives_for_helper")

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


def get_category_by_name(category_name: str) -> CategoryResult:
    """
    Get category by name (fairly frequent).

    Args:
        category_name: Category name

    Returns:
        CategoryResult with:
        - success: True if found
        - category: CategoryRecord if found
        - error: Error message if not found
        - return_statements: AI guidance for next steps

    Example:
        >>> result = get_category_by_name("purity")
        >>> result.success
        True
        >>> result.category.description
        'Pure function principles and side effect management'
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM categories WHERE name = ?",
                (category_name,)
            )
            row = cursor.fetchone()

            if row is None:
                return CategoryResult(
                    success=False,
                    error=f"Category '{category_name}' not found"
                )

            category = row_to_category(row)
            return_statements = get_return_statements("get_category_by_name")

            return CategoryResult(
                success=True,
                category=category,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return CategoryResult(success=False, error=str(e))
    except Exception as e:
        return CategoryResult(success=False, error=f"Query failed: {str(e)}")


def get_categories() -> CategoriesResult:
    """
    Get all directive categories.

    Returns:
        CategoriesResult with all categories

    Example:
        >>> result = get_categories()
        >>> result.success
        True
        >>> [c.name for c in result.categories]
        ['error_handling', 'functional_composition', 'purity', ...]
    """
    try:
        conn = _open_core_connection()

        try:
            cursor = conn.execute(
                "SELECT * FROM categories ORDER BY name"
            )
            rows = cursor.fetchall()
            categories = tuple(row_to_category(row) for row in rows)
            return_statements = get_return_statements("get_categories")

            return CategoriesResult(
                success=True,
                categories=categories,
                return_statements=return_statements
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return CategoriesResult(success=False, error=str(e))
    except Exception as e:
        return CategoriesResult(success=False, error=f"Query failed: {str(e)}")
