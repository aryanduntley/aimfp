# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **User-directive approval is human-only and bound to content** (user_directives schema 1.5). Before this, an AI could approve and activate its own automation: `update_user_custom_entry` / `add_user_custom_entry` accepted `approved=1`, and `status='active'` on an *unapproved* row was dispatched by `get_due_directives`, which never read `approved`.
  - The generic write tools now refuse `approved`, `approved_at`, `approved_by`, `approval_hash` and `status='active'` on `user_directives`. Every key must also be a real column, so a case or quoting variant cannot slip past.
  - New library-only hooks (never MCP tools) in `aimfp.hooks.approval`: `approve_user_directive`, `revoke_user_directive_approval`, `get_directive_approval`. Approval stores a sha256 over the trigger, the action, the implementation file path and the implementation file's bytes. Any later change voids it.
  - `get_due_directives` returns only approved, unchanged directives and lists the rest in a new `withheld` field. `activate_user_directive` refuses them. `check_directive_health` reports the new `unapproved` and `needs_reapproval` states.
  - New `aimfp directives list|approve|revoke` command: the human approval path when AIMFP runs as a plain MCP server. `approve` shows the directive and has the person type its name back, and it refuses a non-interactive stdin.
  - **Migration:** approvals recorded before 1.5 have no hash and read as `needs_reapproval`, so those directives stop dispatching until the user approves them again. A one-time release notice tells the AI to walk the user through it.
  - The `user_directive_approve` / `activate` / `update` / `implement` directives are rewritten: the AI presents the directive and the user approves. Broken SQL in the approve workflow (a nonexistent `approved_by` column, wrong notes columns, and a nonexistent `'approval'` note type) is gone.
  - **Embedders:** write approval with `approve_user_directive` / `revoke_user_directive_approval` instead of `update_user_custom_entry({"approved": ...})`, which is now refused.

### Added
- **Lean read tools, so an overview never needs direct SQL.**
  - `get_incomplete_milestones`, `get_milestones_by_status` and `get_milestones_by_path` take `detail_level` (`'lean'` default, `'full'`) and `include_task_counts`. With counts, each milestone carries `task_counts` = `{pending, in_progress, blocked, completed, total}`, read with one grouped query.
  - `get_all_flows` takes `flow_ids` (only those flows, ordered by ID; unknown IDs are named in `return_statements`) and `detail_level`.
  - `get_tasks_by_milestone` takes `detail_level` and a `status` filter.
  - `audit_tracking` takes `sample_limit` (findings shown inline per category, default 5, `0` = counts only) and `full=[categories]` (those categories inline in full). Unknown category names are an error.

### Changed
- **Lean by default:** the three milestone getters, `get_all_flows` and `get_tasks_by_milestone` now return `description: null` unless `detail_level='full'`. This follows the convention `get_incomplete_tasks` already uses.
- **Function `parameters` come back in one shape:** a parsed list of `{name, type, required, default, description, kind}`, whatever shape they were stored in. This covers catalog records, hand-written dicts, legacy `"name: type = value"` strings and JSON wrapped in JSON. `required` is `null` when the record does not say, and a value that is not a parameter list is returned decoded, not dropped. `returns` is parsed JSON. This applies to `get_function_by_name`, `get_functions_by_file`, `search_functions`, `get_task_context(detail_level='full')` and `get_files_by_flow_context`. The two copies of `row_to_function_record` / `FunctionRecord` are merged into one in `functions_1.py`.
- A `*args` / `**kwargs` parameter recorded as a string is no longer treated as possibly required by the parameter-drift check.
- **License changed from MIT to PolyForm Noncommercial 1.0.0.** Noncommercial use (personal, research, hobby, charitable/educational/government organizations) remains free; any commercial use now requires a separate written license from the copyright holder (aryanduntley@gmail.com). Versions up to and including 1.66.1 remain under MIT.

## [0.1.0] - 2026-02-10

### Added
- 220 MCP tools for FP enforcement and project management
- 4 SQLite databases: aifp_core (global, read-only), project (per-project), user_preferences (per-project), user_directives (optional, per-project)
- Custom JSON-RPC 2.0 stdio server (pure Python stdlib, no SDK dependency)
- Directive-driven workflow system with 129 directive definitions
- Convention-based tool annotations on all 220 tools (readOnlyHint, destructiveHint, idempotentHint, openWorldHint, title)
- Two use cases: regular software development (Case 1) and custom directive automation (Case 2)
- Reserve-finalize lifecycle for files, functions, and types
- Hierarchical project tracking: completion paths, milestones, tasks, subtasks, sidequests
- Themes and flows for project organization
- User preference learning with per-directive key-value overrides
- FP-powered Git integration (branch management, conflict resolution)
- Automated backup system (opt-in)
- System prompt shipped with package (`python3 -m aifp --system-prompt`)
- MCPB bundle for Claude Desktop installation
- 180 automated tests (102 server + 78 submission)
- Interactive test screenplay for end-to-end lifecycle verification
