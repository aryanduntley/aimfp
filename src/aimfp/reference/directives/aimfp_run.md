# aimfp_run - Gateway Directive

**Type**: Orchestration
**Level**: 0 (Root)
**Parent**: None (entry point)
**Category**: Core System

---

## Purpose

`aimfp_run` is the **gateway directive** and primary entry point for all AIMFP operations. On session start (`is_new_session=true`), it bundles comprehensive project context. During the session (`is_new_session=false`), it serves as a watchdog checkpoint — returning external file change alerts when present, empty when nothing to report. Checkpoint calls are triggered organically by return_statements on helpers like `finalize_file` and `update_task`.

---

## When to Use

**Session Start** (Mandatory):
- AI calls `aimfp_run(is_new_session=true)` on first interaction of a session
- Returns full bundle: status, settings, directives, supportive context, guidance

**Work Checkpoints** (Organic):
- AI calls `aimfp_run(is_new_session=false)` at work milestones (file finalization, task/milestone completion)
- Return_statements on checkpoint helpers remind AI to call
- Returns empty when nothing to report, watchdog alerts when external files changed

**Manual Invocation**:
```bash
aimfp run "Initialize project for calculator"
aimfp run "Write multiply function"
aimfp run "Check project status"
```

---

## Workflow

### Trunk: `evaluate_and_route`

**Step 1: Evaluate Mode**

`aimfp_run` does NOT execute tasks directly. It returns data based on mode:
- **`is_new_session=true`**: Full session bundle (status, settings, directives, guidance, watchdog)
- **`is_new_session=false`**: Watchdog alerts only (empty `{}` when nothing to report, reminders + self-destruct notice when alerts exist)

**Step 2: AI Decision Flow (session start only)**

After receiving guidance, AI evaluates:

1. **Check autostart setting (session start only)**
   - Query: `SELECT setting_value FROM user_settings WHERE setting_key='project_continue_on_start'`
   - **If is_new_session=true AND project_continue_on_start=true**:
     - Automatically continue project work with context from session bundle
     - Load pending tasks and present them with priority order
     - Skip waiting for user command - proactively suggest next steps
     - Goal: Project completion and/or auto-execute user custom directives
   - **If false or not new session** → Continue to step 2

2. **Is this a continuation request?**
   - Keywords: "continue", "status", "resume", "where were we", "what's next"
   - **YES** → Call `aimfp_status` first for context
   - **NO** → Proceed to step 3

3. **Check project type (Use Case 1 vs Use Case 2)**
   - Query: `SELECT user_directives_status FROM project WHERE id = 1`
   - **NULL** → Use Case 1 (regular software development)
   - **'pending_discovery', 'pending_parse', 'in_progress', 'active', 'disabled'** → Use Case 2 (automation project)
   - When `is_new_session=true` and Case 2: Include `case_2_context` in bundle with phase, next_action, pipeline reminder, and user_directive_* directive names

4. **What type of task is this?**
   - **Coding** → Write FP-compliant code (consult FP directives only if uncertain)
   - **Project Management** → Execute Project directives
   - **User Directives** (Use Case 2) → Execute User System directives
   - **Git Operations** → Execute Git directives
   - **Discussion** → No directives unless decision made

5. **Do I have directive names?**
   - **NO** → Already provided in is_new_session bundle (directive names only)
   - **YES** → Use cached directive names
   - **Need details?** → Query specific directive by name

6. **Which directives to execute?**
   - AI evaluates user request and project context
   - AI decides which project/user/git directives to execute
   - For coding: Write FP code naturally, query FP directives only if uncertain about implementation

---

## Interactions with Other Directives

### Hierarchical Routing

```
aimfp_run (Level 0)
  ├─ Routes to aimfp_status (Level 1)           [Continuation requests]
  ├─ Routes to project_init (Level 1)          [Initialization]
  ├─ Routes to project_file_write (Level 2)    [Code writing]
  ├─ Routes to project_task_decomposition (L1) [Task creation]
  ├─ Routes to fp_purity, fp_immutability      [FP compliance]
  └─ Routes to user_directive_parse (Level 2)  [User automations]
```

### Common Routing Patterns

**Pattern 1: Continuation Request**
```
User: "Continue"
  ↓
aimfp_run → aimfp_status
  ├─ Calls get_project_status()
  ├─ Queries project.db for open tasks
  ├─ Reads ProjectBlueprint.md
  └─ Returns status report with recommendations
```

**Pattern 2: Coding Task**
```
User: "Write multiply function"
  ↓
aimfp_run returns: guidance
  ↓
AI evaluates: coding + project management
  ↓
AI writes FP-compliant code naturally (pure, immutable, no OOP)
  ↓
AI executes: project_file_write
  └─ Writes file + calls helpers to update project.db
```

**Pattern 3: Project Initialization**
```
User: "Initialize AIMFP for my project"
  ↓
aimfp_run → aimfp_status (checks project state)
  ├─ aimfp_status detects: project_initialized = false
  └─ Routes to: project_init
      ├─ AI pre-flight: Detect project state (new/existing FP/existing OOP)
      ├─ If OOP detected → Reject, inform user AIMFP is FP-only
      ├─ Phase 1: aimfp_init helper (mechanical setup)
      ├─ Phase 2: AI intelligent population
      │   ├─ If existing FP code → Catalog all files/functions into project.db
      │   └─ Populate infrastructure, blueprint, completion path
      └─ Returns success → loops back to aimfp_status
```

**Pattern 4: User Directive Automation (Use Case 2)**
```
User: "Parse my directive file at /path/to/directives.yaml"
  ↓
aimfp_run checks: project.user_directives_status
  ├─ If NULL → Set to 'in_progress' (first directive)
  └─ Routes to: user_directive_parse
      ↓
  user_directive_parse → validate → implement → approve → activate
  ↓
  When activated: project.user_directives_status = 'active'
```

---

## Examples

### Example 1: First Interaction

**User**: "Help me build a calculator"

**AI Processing**:
1. Calls `aimfp_run(is_new_session=True)` → Receives bundled startup data
   - Status: No project initialized
   - Settings: Default preferences
   - FP directive index: Names grouped by category
   - Guidance: Common starting point is `project_init`
2. Evaluates user request: "Project initialization needed"
3. Decides to execute `project_init` workflow
4. Executes `project_init` directive

**Result**: Project initialized with `.aimfp-project/` structure

---

### Example 2: Continuation

**User**: "Continue"

**AI Processing**:
1. Evaluates user request: "continue" = continuation request
2. Calls `aimfp_status()` (quick tier: position, focus, open work) — not aimfp_run; `type='summary'` only if context was lost
3. `aimfp_status` returns:
   - Current focus: "Implementing matrix operations"
   - Open tasks: "Multiply matrix function incomplete"
   - Last work: "Created add_matrices() in src/matrix.py"
4. AI resumes work on multiply_matrices()

**Result**: Contextual continuation with historical awareness

---

### Example 3: Coding Task

**User**: "Write a function to calculate factorial"

**AI Processing**:
1. Evaluates user request: "Coding task + project management" (context already cached from session start)
2. Writes FP-compliant factorial function:
   - Pure function (no side effects)
   - Immutable data (no mutations)
   - No OOP (function only, no classes)
4. Executes `project_file_write` directive:
   - Writes file to disk
   - Calls helpers to update project.db (files, functions, interactions)

**Result**: FP-compliant function written and tracked

---

## Edge Cases

### Case 1: User Explicitly Disables AIMFP

**Input**: "Do not use aimfp for this task"

**Behavior**:
- AI skips `aimfp_run` call
- No directives applied
- Normal conversation mode

---

### Case 2: MCP Server Not Available

**Behavior**:
- AI attempts `aimfp_run` call
- Call fails (MCP not installed)
- AI informs user: "AIMFP MCP server not available. Install with: pip install aimfp-mcp-server"

---

### Case 3: Ambiguous Request

**Input**: "Do something with the code"

**AI Processing**:
1. Calls `aimfp_run()` → Receives guidance
2. Evaluates user request: Ambiguous, unclear action
3. AI prompts user: "What would you like me to do with the code? (write, refactor, review, test)"

---

### Case 4: Context Lost After Compression

**Situation**: Directive names evicted from context

**Behavior**:
1. AI calls `aimfp_run(is_new_session=True)`
2. Receives fresh bundle with directive names
3. Continues work with refreshed context

---

## Related Directives

### Primary Relationships

- **`aimfp_status`** - Called by `aimfp_run` for continuation requests
- **`project_init`** - Routed to for project initialization
- **`project_file_write`** - Routed to for code writing
- **`project_task_decomposition`** - Routed to for task creation
- **`user_directive_parse`** - Routed to for user automation parsing

### Helper Functions

**Session start (`is_new_session=true`)** — `aimfp_run` bundles data from these helpers:
- **`aimfp_status(project_root)`** — Comprehensive project state (metadata, infrastructure, work hierarchy, warnings, git state)
- **`get_user_settings()`** — All user preference settings
- **`get_fp_directive_index()`** — FP directives grouped by category for quick reference
- **`get_all_directive_names()`** — All directive names for context caching
- **`get_all_infrastructure(project_root)`** — Infrastructure entries (language, build tool, source dir, etc.)

**Checkpoint mode (`is_new_session=false`)** — Watchdog alerts only. Returns empty when nothing to report. AI uses cached data from session start bundle. Available helpers for on-demand queries:
- **`get_project_status(project_root)`** — Refresh work hierarchy data mid-session
- **`get_task_context(task_id)`** — Context for resuming specific tasks (task_type auto-detected)
- **`get_flows_from_directive(directive_name)`** — Navigate directive flow graph
- **`get_directive(name)`** / **`search_directives(keyword)`** — Query directive details
---

## Database Operations

**Read Operations**:
- Queries `aimfp_core.db` (via MCP server) for directive names and definitions
- Checks `project.db` (in user project) via helpers
- Reads `config.json` for project-specific configuration

**Database Architecture**:
- **`aimfp_core.db`**: Lives in MCP server installation directory, accessed via MCP tools
  - Contains all 122 core AIMFP directive definitions
  - Read-only, immutable
  - Never copied to user projects
  - Queried via helpers for directive names/details
- **`project.db`**: Lives in user's `.aimfp-project/` directory
  - Contains project-specific state (tasks, files, functions)
  - Mutable, updated via helper functions during directive execution
- **`user_preferences.db`**: Lives in user's `.aimfp-project/` directory
  - Contains user customizations and preferences
  - Mutable, updated via helper functions
- **`user_directives.db`**: (Optional, Use Case 2 only) Lives in user's `.aimfp-project/` directory
  - Contains user-defined automation directives
  - Created on first directive parse

**Write Operations**:
- None directly (routing only)
- Routed directives perform writes

---

## FP Compliance

**Note**: This section describes the aimfp_run helper function implementation, NOT how AI should code. AI writes FP-compliant code naturally without consulting directives for every line.

**Purity**: ✅ Pure function
- Returns guidance structure only
- No side effects in function logic
- Deterministic output given same inputs

**Immutability**: ✅ Immutable
- No state mutations within function
- Returns frozen data structures
- Context passed explicitly

**Side Effects**: ✅ Isolated
- Database reads isolated in helper calls
- File system reads isolated in helper calls
- All effects occur in dedicated helper functions

---

## Error Handling

### Ambiguous User Request

**Trigger**: User request is unclear or ambiguous

**AI Behavior**:
- AI receives standard guidance from aimfp_run
- AI cannot determine appropriate action
- AI asks user for clarification
- No special error response from aimfp_run itself

### MCP Server Unavailable

**Trigger**: MCP tool call fails

**Response**:
```json
{
  "success": false,
  "error": "MCP server not available",
  "installation_hint": "pip install aimfp-mcp-server"
}
```

---

## Best Practices

1. **Always call on session start** - First interaction uses `aimfp_run(is_new_session=true)`
2. **Checkpoint calls are organic** - `is_new_session=false` at work milestones (return_statements remind you)
3. **Status-first for continuation** - Call `aimfp_status` for "continue", "resume", "status"
4. **Directive names cached** - Provided in is_new_session bundle, query details when needed
5. **Trust guidance** - Follow the returned guidance structure
6. **AI decides** - Guidance informs, AI makes final decision on which directives to execute
7. **FP is baseline** - Write FP code naturally, consult FP directives only when uncertain
8. **Ask on ambiguity** - Prompt user for clarification when request is unclear

---

## Version History

- **v1.0** (2025-10-22): Initial gateway directive with routing logic
- **v1.1** (2025-10-23): Added `aimfp_status` integration for continuation
- **v1.2** (2025-10-26): Added two use case support (regular dev vs automation)

---

## Notes

- This directive is **NOT an executor** - it's a gateway and watchdog checkpoint
- Actual work happens in routed directives
- AI maintains responsibility for directive selection
- Session start bundles all context; checkpoint calls surface only watchdog alerts
- When watchdog reminders are returned, they are cleared — handle them immediately
