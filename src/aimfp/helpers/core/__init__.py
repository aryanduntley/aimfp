"""
AIMFP Helper Functions - Core Database Helpers

Helpers for querying the aimfp_core.db database (read-only, global).

This package contains:
- validation.py: CHECK constraint parsing (1 helper)
- schema.py: Schema introspection and generic queries (6 helpers)
- directives_1.py: Directive queries, search, intent keywords, and content (14 helpers)
- directives_2.py: Helper queries and category operations (9 helpers)
- flows.py: Directive flow navigation and routing (5 helpers)

Total: 35 helpers

All helpers follow FP principles:
- Pure functions where possible
- Effects clearly isolated and named
- Immutable data structures (frozen dataclasses, tuples)
- Explicit parameters
"""

# Validation
from .validation import (
    core_allowed_check_constraints,
    CheckConstraintResult,
)

# Schema
from .schema import (
    get_core_tables,
    get_core_fields,
    get_core_schema,
    get_from_core,
    get_from_core_where,
    query_core,
    TablesResult,
    FieldInfo,
    FieldsResult,
    TableSchema,
    SchemaResult,
    QueryResult,
)

# Directives Part 1
from .directives_1 import (
    get_directive_by_name,
    get_directive_content,
    get_all_directives,
    search_directives,
    find_directive_by_intent,
    find_directives_by_intent_keyword,
    get_directives_with_intent_keywords,
    get_directive_keywords,
    get_all_directive_keywords,
    get_all_intent_keywords_with_counts,
    get_all_directive_names,
    get_directives_by_category,
    get_directives_by_type,
    get_fp_directive_index,
    DirectiveResult,
    DirectivesResult,
    IntentMatchResult,
    KeywordsResult,
    KeywordCountsResult,
    DirectiveNamesResult,
    DirectiveIndexResult,
)

# Directives Part 2
from .directives_2 import (
    get_helper_by_name,
    get_helpers_by_database,
    get_helpers_are_tool,
    get_hooks,
    get_helpers_are_sub,
    get_helpers_for_directive,
    get_directives_for_helper,
    get_category_by_name,
    get_categories,
    HelperResult,
    HelpersResult,
    CategoryResult,
    CategoriesResult,
    DirectiveHelpersResult,
)

# Flows
from .flows import (
    get_flows_from_directive,
    get_flows_to_directive,
    get_completion_loop_target,
    get_directive_flows,
    get_wildcard_flows,
    FlowResult,
    FlowsResult,
)

# Common utilities and data structures
from ._common import (
    DirectiveRecord,
    HelperRecord,
    FlowRecord,
    CategoryRecord,
    IntentKeywordRecord,
)

__all__ = [
    # Validation
    'core_allowed_check_constraints',
    'CheckConstraintResult',

    # Schema
    'get_core_tables',
    'get_core_fields',
    'get_core_schema',
    'get_from_core',
    'get_from_core_where',
    'query_core',
    'TablesResult',
    'FieldInfo',
    'FieldsResult',
    'TableSchema',
    'SchemaResult',
    'QueryResult',

    # Directives Part 1
    'get_directive_by_name',
    'get_directive_content',
    'get_all_directives',
    'search_directives',
    'find_directive_by_intent',
    'find_directives_by_intent_keyword',
    'get_directives_with_intent_keywords',
    'get_directive_keywords',
    'get_all_directive_keywords',
    'get_all_intent_keywords_with_counts',
    'get_all_directive_names',
    'get_directives_by_category',
    'get_directives_by_type',
    'get_fp_directive_index',
    'DirectiveResult',
    'DirectivesResult',
    'IntentMatchResult',
    'KeywordsResult',
    'KeywordCountsResult',
    'DirectiveNamesResult',
    'DirectiveIndexResult',

    # Directives Part 2
    'get_helper_by_name',
    'get_helpers_by_database',
    'get_helpers_are_tool',
    'get_hooks',
    'get_helpers_are_sub',
    'get_helpers_for_directive',
    'get_directives_for_helper',
    'get_category_by_name',
    'get_categories',
    'HelperResult',
    'HelpersResult',
    'CategoryResult',
    'CategoriesResult',
    'DirectiveHelpersResult',

    # Flows
    'get_flows_from_directive',
    'get_flows_to_directive',
    'get_completion_loop_target',
    'get_directive_flows',
    'get_wildcard_flows',
    'FlowResult',
    'FlowsResult',

    # Common data structures
    'DirectiveRecord',
    'HelperRecord',
    'FlowRecord',
    'CategoryRecord',
    'IntentKeywordRecord',
]
