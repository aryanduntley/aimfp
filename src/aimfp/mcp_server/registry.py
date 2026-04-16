"""
AIMFP MCP Server - Tool Registry

Static dict mapping tool names to (module_path, function_name) tuples.
Uses importlib for lazy loading — modules are only imported on first call.

226 is_tool=true helpers registered. Generated from aimfp_core.db.

Why static, not DB-driven:
- Predictable tool list, no runtime DB dependency for tool listing
- Easy to test and debug
- file_path field in DB uses relative paths that need prefix resolution
"""

import importlib
from typing import Final, Dict, Tuple, Callable, Any


# ============================================================================
# Tool Registry
# ============================================================================

# Maps tool_name -> (module_path, function_name)
# Module paths are fully qualified for importlib.import_module()
TOOL_REGISTRY: Final[Dict[str, Tuple[str, str]]] = {

    # ── Core: Directives ─────────────────────────────────────────────────
    # helpers/core/directives_1.py (13 tools)
    "find_directive_by_intent": ("aimfp.helpers.core.directives_1", "find_directive_by_intent"),
    "get_all_directive_keywords": ("aimfp.helpers.core.directives_1", "get_all_directive_keywords"),
    "get_all_directive_names": ("aimfp.helpers.core.directives_1", "get_all_directive_names"),
    "get_all_directives": ("aimfp.helpers.core.directives_1", "get_all_directives"),
    "get_all_intent_keywords_with_counts": ("aimfp.helpers.core.directives_1", "get_all_intent_keywords_with_counts"),
    "get_directive_by_name": ("aimfp.helpers.core.directives_1", "get_directive_by_name"),
    "get_directive_content": ("aimfp.helpers.core.directives_1", "get_directive_content"),
    "get_directive_keywords": ("aimfp.helpers.core.directives_1", "get_directive_keywords"),
    "get_directives_by_category": ("aimfp.helpers.core.directives_1", "get_directives_by_category"),
    "get_directives_by_type": ("aimfp.helpers.core.directives_1", "get_directives_by_type"),
    "get_directives_with_intent_keywords": ("aimfp.helpers.core.directives_1", "get_directives_with_intent_keywords"),
    "get_fp_directive_index": ("aimfp.helpers.core.directives_1", "get_fp_directive_index"),
    "search_directives": ("aimfp.helpers.core.directives_1", "search_directives"),
    "find_directives_by_intent_keyword": ("aimfp.helpers.core.directives_1", "find_directives_by_intent_keyword"),
    # helpers/core/directives_2.py (9 tools)
    "get_categories": ("aimfp.helpers.core.directives_2", "get_categories"),
    "get_category_by_name": ("aimfp.helpers.core.directives_2", "get_category_by_name"),
    "get_directives_for_helper": ("aimfp.helpers.core.directives_2", "get_directives_for_helper"),
    "get_helper_by_name": ("aimfp.helpers.core.directives_2", "get_helper_by_name"),
    "get_helpers_are_sub": ("aimfp.helpers.core.directives_2", "get_helpers_are_sub"),
    "get_helpers_are_tool": ("aimfp.helpers.core.directives_2", "get_helpers_are_tool"),
    "get_helpers_by_database": ("aimfp.helpers.core.directives_2", "get_helpers_by_database"),
    "get_helpers_for_directive": ("aimfp.helpers.core.directives_2", "get_helpers_for_directive"),
    "get_helpers_not_tool_not_sub": ("aimfp.helpers.core.directives_2", "get_helpers_not_tool_not_sub"),

    # ── Core: Flows ──────────────────────────────────────────────────────
    # helpers/core/flows.py (5 tools)
    "get_completion_loop_target": ("aimfp.helpers.core.flows", "get_completion_loop_target"),
    "get_directive_flows": ("aimfp.helpers.core.flows", "get_directive_flows"),
    "get_flows_from_directive": ("aimfp.helpers.core.flows", "get_flows_from_directive"),
    "get_flows_to_directive": ("aimfp.helpers.core.flows", "get_flows_to_directive"),
    "get_wildcard_flows": ("aimfp.helpers.core.flows", "get_wildcard_flows"),

    # ── Core: Schema & Validation ────────────────────────────────────────
    # helpers/core/schema.py (6 tools)
    "get_core_fields": ("aimfp.helpers.core.schema", "get_core_fields"),
    "get_core_schema": ("aimfp.helpers.core.schema", "get_core_schema"),
    "get_core_tables": ("aimfp.helpers.core.schema", "get_core_tables"),
    "get_from_core": ("aimfp.helpers.core.schema", "get_from_core"),
    "get_from_core_where": ("aimfp.helpers.core.schema", "get_from_core_where"),
    "query_core": ("aimfp.helpers.core.schema", "query_core"),
    # helpers/core/validation.py (1 tool)
    "core_allowed_check_constraints": ("aimfp.helpers.core.validation", "core_allowed_check_constraints"),

    # ── Git ──────────────────────────────────────────────────────────────
    # helpers/git/operations.py (7 tools)
    "create_user_branch": ("aimfp.helpers.git.operations", "create_user_branch"),
    "detect_conflicts_before_merge": ("aimfp.helpers.git.operations", "detect_conflicts_before_merge"),
    "detect_external_changes": ("aimfp.helpers.git.operations", "detect_external_changes"),
    "execute_merge": ("aimfp.helpers.git.operations", "execute_merge"),
    "get_git_status": ("aimfp.helpers.git.operations", "get_git_status"),
    "list_active_branches": ("aimfp.helpers.git.operations", "list_active_branches"),
    "sync_git_state": ("aimfp.helpers.git.operations", "sync_git_state"),
    "get_current_commit_hash": ("aimfp.helpers.git.operations", "get_current_commit_hash"),
    "get_current_branch": ("aimfp.helpers.git.operations", "get_current_branch"),

    # ── Orchestrators ────────────────────────────────────────────────────
    # helpers/orchestrators/entry_points.py (4 tools)
    "aimfp_end": ("aimfp.helpers.orchestrators.entry_points", "aimfp_end"),
    "clear_watchdog": ("aimfp.helpers.orchestrators.entry_points", "clear_watchdog"),
    "aimfp_init": ("aimfp.helpers.orchestrators.entry_points", "aimfp_init"),
    "aimfp_run": ("aimfp.helpers.orchestrators.entry_points", "aimfp_run"),
    "aimfp_status": ("aimfp.helpers.orchestrators.entry_points", "aimfp_status"),
    # helpers/orchestrators/migration.py (1 tool)
    "migrate_databases": ("aimfp.helpers.orchestrators.migration", "migrate_databases"),
    # helpers/orchestrators/query.py (1 tool)
    "query_project_state": ("aimfp.helpers.orchestrators.query", "query_project_state"),
    "get_files_by_flow_context": ("aimfp.helpers.orchestrators.query", "get_files_by_flow_context"),
    # helpers/orchestrators/state.py (3 tools)
    "batch_update_progress": ("aimfp.helpers.orchestrators.state", "batch_update_progress"),
    "get_current_progress": ("aimfp.helpers.orchestrators.state", "get_current_progress"),
    "update_project_state": ("aimfp.helpers.orchestrators.state", "update_project_state"),
    # helpers/orchestrators/status.py (1 tool)
    "get_task_context": ("aimfp.helpers.orchestrators.status", "get_task_context"),

    # ── Project: CRUD ────────────────────────────────────────────────────
    # helpers/project/crud.py (7 tools)
    "add_project_entry": ("aimfp.helpers.project.crud", "add_project_entry"),
    "delete_project_entry": ("aimfp.helpers.project.crud", "delete_project_entry"),
    "delete_reserved": ("aimfp.helpers.project.crud", "delete_reserved"),
    "get_from_project": ("aimfp.helpers.project.crud", "get_from_project"),
    "get_from_project_where": ("aimfp.helpers.project.crud", "get_from_project_where"),
    "query_project": ("aimfp.helpers.project.crud", "query_project"),
    "update_project_entry": ("aimfp.helpers.project.crud", "update_project_entry"),

    # ── Project: Files ───────────────────────────────────────────────────
    # helpers/project/files_1.py (6 tools)
    "finalize_file": ("aimfp.helpers.project.files_1", "finalize_file"),
    "finalize_files": ("aimfp.helpers.project.files_1", "finalize_files"),
    "get_file_by_name": ("aimfp.helpers.project.files_1", "get_file_by_name"),
    "get_file_by_path": ("aimfp.helpers.project.files_1", "get_file_by_path"),
    "reserve_file": ("aimfp.helpers.project.files_1", "reserve_file"),
    "reserve_files": ("aimfp.helpers.project.files_1", "reserve_files"),
    # helpers/project/files_2.py (3 tools)
    "delete_file": ("aimfp.helpers.project.files_2", "delete_file"),
    "file_has_changed": ("aimfp.helpers.project.files_2", "file_has_changed"),
    "update_file": ("aimfp.helpers.project.files_2", "update_file"),

    # ── Project: Functions ───────────────────────────────────────────────
    # helpers/project/functions_1.py (6 tools)
    "finalize_function": ("aimfp.helpers.project.functions_1", "finalize_function"),
    "finalize_functions": ("aimfp.helpers.project.functions_1", "finalize_functions"),
    "get_function_by_name": ("aimfp.helpers.project.functions_1", "get_function_by_name"),
    "reserve_function": ("aimfp.helpers.project.functions_1", "reserve_function"),
    "reserve_functions": ("aimfp.helpers.project.functions_1", "reserve_functions"),
    "search_functions": ("aimfp.helpers.project.functions_1", "search_functions"),
    # helpers/project/functions_2.py (4 tools)
    "delete_function": ("aimfp.helpers.project.functions_2", "delete_function"),
    "get_functions_by_file": ("aimfp.helpers.project.functions_2", "get_functions_by_file"),
    "update_function": ("aimfp.helpers.project.functions_2", "update_function"),
    "update_functions_for_file": ("aimfp.helpers.project.functions_2", "update_functions_for_file"),
    "update_function_file_location": ("aimfp.helpers.project.functions_2", "update_function_file_location"),

    # ── Project: Interactions ────────────────────────────────────────────
    # helpers/project/interactions.py (6 tools)
    "add_interaction": ("aimfp.helpers.project.interactions", "add_interaction"),
    "add_interactions": ("aimfp.helpers.project.interactions", "add_interactions"),
    "update_interaction": ("aimfp.helpers.project.interactions", "update_interaction"),
    "delete_interaction": ("aimfp.helpers.project.interactions", "delete_interaction"),
    "get_interactions_by_function": ("aimfp.helpers.project.interactions", "get_interactions_by_function"),
    "get_interactions_by_file": ("aimfp.helpers.project.interactions", "get_interactions_by_file"),

    # ── Project: Items & Notes ───────────────────────────────────────────
    # helpers/project/items_notes.py (13 tools)
    "add_item": ("aimfp.helpers.project.items_notes", "add_item"),
    "add_items": ("aimfp.helpers.project.items_notes", "add_items"),
    "add_note": ("aimfp.helpers.project.items_notes", "add_note"),
    "delete_item": ("aimfp.helpers.project.items_notes", "delete_item"),
    "get_incomplete_items": ("aimfp.helpers.project.items_notes", "get_incomplete_items"),
    "get_items_for_sidequest": ("aimfp.helpers.project.items_notes", "get_items_for_sidequest"),
    "get_items_for_subtask": ("aimfp.helpers.project.items_notes", "get_items_for_subtask"),
    "get_items_for_task": ("aimfp.helpers.project.items_notes", "get_items_for_task"),
    "get_notes_comprehensive": ("aimfp.helpers.project.items_notes", "get_notes_comprehensive"),
    "search_notes": ("aimfp.helpers.project.items_notes", "search_notes"),
    "update_item": ("aimfp.helpers.project.items_notes", "update_item"),
    "update_items": ("aimfp.helpers.project.items_notes", "update_items"),
    "update_note": ("aimfp.helpers.project.items_notes", "update_note"),
    "delete_note": ("aimfp.helpers.project.items_notes", "delete_note"),

    # ── Project: Metadata ────────────────────────────────────────────────
    # helpers/project/metadata.py (10 tools)
    "blueprint_has_changed": ("aimfp.helpers.project.metadata", "blueprint_has_changed"),
    "create_project": ("aimfp.helpers.project.metadata", "create_project"),
    "get_all_infrastructure": ("aimfp.helpers.project.metadata", "get_all_infrastructure"),
    "get_infrastructure_by_type": ("aimfp.helpers.project.metadata", "get_infrastructure_by_type"),
    "get_project": ("aimfp.helpers.project.metadata", "get_project"),
    "get_project_root": ("aimfp.helpers.project.metadata", "get_project_root"),
    "get_source_directory": ("aimfp.helpers.project.metadata", "get_source_directory"),
    "update_project": ("aimfp.helpers.project.metadata", "update_project"),
    "update_project_root": ("aimfp.helpers.project.metadata", "update_project_root"),
    "update_source_directory": ("aimfp.helpers.project.metadata", "update_source_directory"),

    # ── Project: Schema & Validation ─────────────────────────────────────
    # helpers/project/schema.py (4 tools)
    "get_project_fields": ("aimfp.helpers.project.schema", "get_project_fields"),
    "get_project_json_parameters": ("aimfp.helpers.project.schema", "get_project_json_parameters"),
    "get_project_schema": ("aimfp.helpers.project.schema", "get_project_schema"),
    "get_project_tables": ("aimfp.helpers.project.schema", "get_project_tables"),
    # helpers/project/state_db.py (2 tools)
    "create_state_database": ("aimfp.helpers.project.state_db", "create_state_database"),
    "get_state_operations_template": ("aimfp.helpers.project.state_db", "get_state_operations_template"),
    # helpers/project/validation.py (1 tool)
    "project_allowed_check_constraints": ("aimfp.helpers.project.validation", "project_allowed_check_constraints"),

    # ── Project: Subtasks & Sidequests ───────────────────────────────────
    # helpers/project/subtasks_sidequests.py (14 tools)
    "add_sidequest": ("aimfp.helpers.project.subtasks_sidequests", "add_sidequest"),
    "add_subtask": ("aimfp.helpers.project.subtasks_sidequests", "add_subtask"),
    "delete_sidequest": ("aimfp.helpers.project.subtasks_sidequests", "delete_sidequest"),
    "delete_subtask": ("aimfp.helpers.project.subtasks_sidequests", "delete_subtask"),
    "get_incomplete_sidequests": ("aimfp.helpers.project.subtasks_sidequests", "get_incomplete_sidequests"),
    "get_incomplete_subtasks": ("aimfp.helpers.project.subtasks_sidequests", "get_incomplete_subtasks"),
    "get_incomplete_subtasks_by_task": ("aimfp.helpers.project.subtasks_sidequests", "get_incomplete_subtasks_by_task"),
    "get_sidequest_files": ("aimfp.helpers.project.subtasks_sidequests", "get_sidequest_files"),
    "get_sidequest_flows": ("aimfp.helpers.project.subtasks_sidequests", "get_sidequest_flows"),
    "get_sidequests_comprehensive": ("aimfp.helpers.project.subtasks_sidequests", "get_sidequests_comprehensive"),
    "get_subtasks_by_task": ("aimfp.helpers.project.subtasks_sidequests", "get_subtasks_by_task"),
    "get_subtasks_comprehensive": ("aimfp.helpers.project.subtasks_sidequests", "get_subtasks_comprehensive"),
    "update_sidequest": ("aimfp.helpers.project.subtasks_sidequests", "update_sidequest"),
    "update_subtask": ("aimfp.helpers.project.subtasks_sidequests", "update_subtask"),

    # ── Project: Tasks & Milestones ──────────────────────────────────────
    # helpers/project/tasks.py (15 tools)
    "add_milestone": ("aimfp.helpers.project.tasks", "add_milestone"),
    "add_task": ("aimfp.helpers.project.tasks", "add_task"),
    "delete_milestone": ("aimfp.helpers.project.tasks", "delete_milestone"),
    "delete_task": ("aimfp.helpers.project.tasks", "delete_task"),
    "get_incomplete_milestones": ("aimfp.helpers.project.tasks", "get_incomplete_milestones"),
    "get_incomplete_tasks": ("aimfp.helpers.project.tasks", "get_incomplete_tasks"),
    "get_incomplete_tasks_by_milestone": ("aimfp.helpers.project.tasks", "get_incomplete_tasks_by_milestone"),
    "get_milestones_by_path": ("aimfp.helpers.project.tasks", "get_milestones_by_path"),
    "get_milestones_by_status": ("aimfp.helpers.project.tasks", "get_milestones_by_status"),
    "get_task_files": ("aimfp.helpers.project.tasks", "get_task_files"),
    "get_task_flows": ("aimfp.helpers.project.tasks", "get_task_flows"),
    "get_tasks_by_milestone": ("aimfp.helpers.project.tasks", "get_tasks_by_milestone"),
    "get_tasks_comprehensive": ("aimfp.helpers.project.tasks", "get_tasks_comprehensive"),
    "update_milestone": ("aimfp.helpers.project.tasks", "update_milestone"),
    "update_task": ("aimfp.helpers.project.tasks", "update_task"),

    # ── Project: Themes & Flows ──────────────────────────────────────────
    # helpers/project/themes_flows_1.py (11 tools)
    "add_flow": ("aimfp.helpers.project.themes_flows_1", "add_flow"),
    "add_theme": ("aimfp.helpers.project.themes_flows_1", "add_theme"),
    "delete_flow": ("aimfp.helpers.project.themes_flows_1", "delete_flow"),
    "delete_theme": ("aimfp.helpers.project.themes_flows_1", "delete_theme"),
    "get_all_flows": ("aimfp.helpers.project.themes_flows_1", "get_all_flows"),
    "get_all_themes": ("aimfp.helpers.project.themes_flows_1", "get_all_themes"),
    "get_file_ids_from_flows": ("aimfp.helpers.project.themes_flows_1", "get_file_ids_from_flows"),
    "get_flow_by_name": ("aimfp.helpers.project.themes_flows_1", "get_flow_by_name"),
    "get_theme_by_name": ("aimfp.helpers.project.themes_flows_1", "get_theme_by_name"),
    "update_flow": ("aimfp.helpers.project.themes_flows_1", "update_flow"),
    "update_theme": ("aimfp.helpers.project.themes_flows_1", "update_theme"),
    # helpers/project/themes_flows_2.py (16 tools)
    "add_completion_path": ("aimfp.helpers.project.themes_flows_2", "add_completion_path"),
    "add_file_to_flow": ("aimfp.helpers.project.themes_flows_2", "add_file_to_flow"),
    "add_file_flows": ("aimfp.helpers.project.themes_flows_2", "add_file_flows"),
    "delete_completion_path": ("aimfp.helpers.project.themes_flows_2", "delete_completion_path"),
    "get_all_completion_paths": ("aimfp.helpers.project.themes_flows_2", "get_all_completion_paths"),
    "get_completion_paths_by_status": ("aimfp.helpers.project.themes_flows_2", "get_completion_paths_by_status"),
    "get_files_by_flow": ("aimfp.helpers.project.themes_flows_2", "get_files_by_flow"),
    "get_flows_for_file": ("aimfp.helpers.project.themes_flows_2", "get_flows_for_file"),
    "get_flows_for_theme": ("aimfp.helpers.project.themes_flows_2", "get_flows_for_theme"),
    "get_incomplete_completion_paths": ("aimfp.helpers.project.themes_flows_2", "get_incomplete_completion_paths"),
    "get_next_completion_path": ("aimfp.helpers.project.themes_flows_2", "get_next_completion_path"),
    "get_themes_for_flow": ("aimfp.helpers.project.themes_flows_2", "get_themes_for_flow"),
    "update_completion_path": ("aimfp.helpers.project.themes_flows_2", "update_completion_path"),
    "reorder_completion_path": ("aimfp.helpers.project.themes_flows_2", "reorder_completion_path"),
    "reorder_all_completion_paths": ("aimfp.helpers.project.themes_flows_2", "reorder_all_completion_paths"),
    "swap_completion_paths_order": ("aimfp.helpers.project.themes_flows_2", "swap_completion_paths_order"),

    # ── Project: Modules ─────────────────────────────────────────────────
    # helpers/project/modules.py (17 tools)
    "add_module": ("aimfp.helpers.project.modules", "add_module"),
    "get_module_by_name": ("aimfp.helpers.project.modules", "get_module_by_name"),
    "get_module_by_path": ("aimfp.helpers.project.modules", "get_module_by_path"),
    "get_all_modules": ("aimfp.helpers.project.modules", "get_all_modules"),
    "update_module": ("aimfp.helpers.project.modules", "update_module"),
    "delete_module": ("aimfp.helpers.project.modules", "delete_module"),
    "add_file_to_module": ("aimfp.helpers.project.modules", "add_file_to_module"),
    "remove_file_from_module": ("aimfp.helpers.project.modules", "remove_file_from_module"),
    "add_files_to_module": ("aimfp.helpers.project.modules", "add_files_to_module"),
    "remove_files_from_module": ("aimfp.helpers.project.modules", "remove_files_from_module"),
    "get_module_files": ("aimfp.helpers.project.modules", "get_module_files"),
    "get_module_functions": ("aimfp.helpers.project.modules", "get_module_functions"),
    "get_module_types": ("aimfp.helpers.project.modules", "get_module_types"),
    "get_module_dependencies": ("aimfp.helpers.project.modules", "get_module_dependencies"),
    "get_module_for_file": ("aimfp.helpers.project.modules", "get_module_for_file"),
    "get_unassigned_files": ("aimfp.helpers.project.modules", "get_unassigned_files"),
    "search_modules": ("aimfp.helpers.project.modules", "search_modules"),

    # ── Project: Types ───────────────────────────────────────────────────
    # helpers/project/types_1.py (8 tools)
    "delete_type": ("aimfp.helpers.project.types_1", "delete_type"),
    "finalize_type": ("aimfp.helpers.project.types_1", "finalize_type"),
    "finalize_types": ("aimfp.helpers.project.types_1", "finalize_types"),
    "get_type_by_name": ("aimfp.helpers.project.types_1", "get_type_by_name"),
    "reserve_type": ("aimfp.helpers.project.types_1", "reserve_type"),
    "reserve_types": ("aimfp.helpers.project.types_1", "reserve_types"),
    "search_types": ("aimfp.helpers.project.types_1", "search_types"),
    "update_type": ("aimfp.helpers.project.types_1", "update_type"),
    # helpers/project/types_2.py (3 tools)
    "add_types_functions": ("aimfp.helpers.project.types_2", "add_types_functions"),
    "update_type_function_role": ("aimfp.helpers.project.types_2", "update_type_function_role"),
    "delete_type_function": ("aimfp.helpers.project.types_2", "delete_type_function"),

    # ── Shared ───────────────────────────────────────────────────────────
    # helpers/shared/database_info.py (1 tool)
    "get_databases": ("aimfp.helpers.shared.database_info", "get_databases"),
    # helpers/shared/supportive_context.py (1 tool)
    "get_supportive_context": ("aimfp.helpers.shared.supportive_context", "get_supportive_context"),

    # ── User Directives ──────────────────────────────────────────────────
    # helpers/user_directives/crud.py (8 tools)
    "add_user_custom_entry": ("aimfp.helpers.user_directives.crud", "add_user_custom_entry"),
    "delete_user_custom_entry": ("aimfp.helpers.user_directives.crud", "delete_user_custom_entry"),
    "get_active_user_directives": ("aimfp.helpers.user_directives.crud", "get_active_user_directives"),
    "get_from_user_custom": ("aimfp.helpers.user_directives.crud", "get_from_user_custom"),
    "get_from_user_custom_where": ("aimfp.helpers.user_directives.crud", "get_from_user_custom_where"),
    "query_user_custom": ("aimfp.helpers.user_directives.crud", "query_user_custom"),
    "search_user_directives": ("aimfp.helpers.user_directives.crud", "search_user_directives"),
    "update_user_custom_entry": ("aimfp.helpers.user_directives.crud", "update_user_custom_entry"),
    # helpers/user_directives/management.py (7 tools)
    "activate_user_directive": ("aimfp.helpers.user_directives.management", "activate_user_directive"),
    "init_user_directives_db": ("aimfp.helpers.user_directives.management", "init_user_directives_db"),
    "add_user_directive_note": ("aimfp.helpers.user_directives.management", "add_user_directive_note"),
    "deactivate_user_directive": ("aimfp.helpers.user_directives.management", "deactivate_user_directive"),
    "get_user_directive_by_name": ("aimfp.helpers.user_directives.management", "get_user_directive_by_name"),
    "get_user_directive_notes": ("aimfp.helpers.user_directives.management", "get_user_directive_notes"),
    "search_user_directive_notes": ("aimfp.helpers.user_directives.management", "search_user_directive_notes"),
    # helpers/user_directives/schema.py (4 tools)
    "get_user_custom_fields": ("aimfp.helpers.user_directives.schema", "get_user_custom_fields"),
    "get_user_custom_json_parameters": ("aimfp.helpers.user_directives.schema", "get_user_custom_json_parameters"),
    "get_user_custom_schema": ("aimfp.helpers.user_directives.schema", "get_user_custom_schema"),
    "get_user_custom_tables": ("aimfp.helpers.user_directives.schema", "get_user_custom_tables"),
    # helpers/user_directives/validation.py (1 tool)
    "user_directives_allowed_check_constraints": ("aimfp.helpers.user_directives.validation", "user_directives_allowed_check_constraints"),

    # ── User Preferences ─────────────────────────────────────────────────
    # helpers/user_preferences/crud.py (6 tools)
    "add_settings_entry": ("aimfp.helpers.user_preferences.crud", "add_settings_entry"),
    "delete_settings_entry": ("aimfp.helpers.user_preferences.crud", "delete_settings_entry"),
    "get_from_settings": ("aimfp.helpers.user_preferences.crud", "get_from_settings"),
    "get_from_settings_where": ("aimfp.helpers.user_preferences.crud", "get_from_settings_where"),
    "query_settings": ("aimfp.helpers.user_preferences.crud", "query_settings"),
    "update_settings_entry": ("aimfp.helpers.user_preferences.crud", "update_settings_entry"),
    # helpers/user_preferences/management.py (14 tools)
    "add_directive_preference": ("aimfp.helpers.user_preferences.management", "add_directive_preference"),
    "add_tracking_note": ("aimfp.helpers.user_preferences.management", "add_tracking_note"),
    "add_user_setting": ("aimfp.helpers.user_preferences.management", "add_user_setting"),
    "delete_custom_return_statement": ("aimfp.helpers.user_preferences.management", "delete_custom_return_statement"),
    "get_tracking_notes": ("aimfp.helpers.user_preferences.management", "get_tracking_notes"),
    "get_tracking_settings": ("aimfp.helpers.user_preferences.management", "get_tracking_settings"),
    "get_user_setting": ("aimfp.helpers.user_preferences.management", "get_user_setting"),
    "get_user_settings": ("aimfp.helpers.user_preferences.management", "get_user_settings"),
    "load_directive_preferences": ("aimfp.helpers.user_preferences.management", "load_directive_preferences"),
    "search_tracking_notes": ("aimfp.helpers.user_preferences.management", "search_tracking_notes"),
    "set_custom_return_statement": ("aimfp.helpers.user_preferences.management", "set_custom_return_statement"),
    "toggle_tracking_feature": ("aimfp.helpers.user_preferences.management", "toggle_tracking_feature"),
    "update_directive_preference": ("aimfp.helpers.user_preferences.management", "update_directive_preference"),
    "update_user_setting": ("aimfp.helpers.user_preferences.management", "update_user_setting"),
    # helpers/user_preferences/schema.py (4 tools)
    "get_settings_fields": ("aimfp.helpers.user_preferences.schema", "get_settings_fields"),
    "get_settings_json_parameters": ("aimfp.helpers.user_preferences.schema", "get_settings_json_parameters"),
    "get_settings_schema": ("aimfp.helpers.user_preferences.schema", "get_settings_schema"),
    "get_settings_tables": ("aimfp.helpers.user_preferences.schema", "get_settings_tables"),
    # helpers/user_preferences/validation.py (1 tool)
    "user_preferences_allowed_check_constraints": ("aimfp.helpers.user_preferences.validation", "user_preferences_allowed_check_constraints"),
}


# ============================================================================
# Pure Functions
# ============================================================================

def is_registered_tool(tool_name: str) -> bool:
    """Pure: Check if a tool name exists in the registry."""
    return tool_name in TOOL_REGISTRY


def get_registry_entry(tool_name: str) -> Tuple[str, str]:
    """
    Pure: Get the (module_path, function_name) for a registered tool.

    Args:
        tool_name: Tool name to look up

    Returns:
        Tuple of (module_path, function_name)

    Raises:
        KeyError: If tool_name is not registered
    """
    return TOOL_REGISTRY[tool_name]


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_import_tool_function(tool_name: str) -> Callable[..., Any]:
    """
    Effect: Lazy import a tool's function via importlib.

    Python caches modules after first import, so subsequent calls
    for the same module are instant.

    Args:
        tool_name: Registered tool name

    Returns:
        The callable helper function

    Raises:
        KeyError: If tool_name not in registry
        ImportError: If module cannot be imported
        AttributeError: If function not found in module
    """
    module_path, function_name = TOOL_REGISTRY[tool_name]
    module = importlib.import_module(module_path)
    return getattr(module, function_name)
