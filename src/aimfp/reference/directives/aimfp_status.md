# aimfp_status - Project Status Reporting Directive

**Type**: Project Management
**Level**: 1
**Parent**: `aimfp_run`
**Category**: Status & Context

---

## Purpose

`aimfp_status` provides comprehensive project context for continuation and status checking. It reads ProjectBlueprint.md, queries project.db, and returns a structured status report with historical context, open items, and recommended actions.

**Use this directive when**:
- User requests continuation ("continue", "resume", "what's next")
- User asks for status ("status", "where are we", "show progress")
- Starting task decomposition (context-aware planning)
- AI needs project context after context loss

---

## When to Use

### Automatic Triggers

Keywords that trigger `aimfp_status`:
- "continue", "resume", "keep going"
- "status", "progress", "where are we"
- "what's next", "what should I do"
- "show me", "summarize", "update"

### Proactive Calls

Called by other directives:
- `project_task_decomposition` → Calls `aimfp_status` first for context
- `aimfp_run` → Routes continuation requests to `aimfp_status`
- `project_evolution` → Checks status before architectural changes

### Project Not Initialized

If `.aimfp-project/` does not exist, AI must handle project detection:

**AI Responsibility — Project State Detection:**

1. **Check for `.aimfp-project/`** — If found, project is initialized. Proceed with status report.
2. **Check for `.git/.aimfp/` backup** — If found, prompt user: "Found archived project state. Restore or initialize new?"
   - If restore: Copy backup files to `.aimfp-project/`, then call `aimfp_status` again
   - If new: Route to `project_init` directive
3. **No project found** — Prompt user: "Project not initialized. Initialize now?"
   - If yes: Route to `project_init` directive (which handles OOP detection and FP cataloging)
   - If no: Inform user AIMFP requires initialization to function

**IMPORTANT**: This is AI decision logic, not a helper function. AI evaluates the filesystem state and routes accordingly via the directive flow (`aimfp_status → project_init` when `project_initialized=false`).

---

## Workflow

### Trunk: `generate_status_report`

**Step 1: Read ProjectBlueprint.md**

```python
# Load blueprint for high-level context
blueprint_path = f"{project_root}/.aimfp-project/ProjectBlueprint.md"
blueprint_content = read_file(blueprint_path)

# Parse sections
project_overview = parse_section(blueprint_content, "1. Project Overview")
technical_blueprint = parse_section(blueprint_content, "2. Technical Blueprint")
themes_flows = parse_section(blueprint_content, "3. Project Themes & Flows")
completion_path = parse_section(blueprint_content, "4. Completion Path")
```

**Step 2: Query project.db for Current State**

**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

**Step 3: Check User Directive Status (Use Case 2 Projects)**

```python
# Check if this is a Use Case 2 project (automation-based)
if project.user_directives_status is not None:
    # This is a Use Case 2 project

    # Determine project phase based on status
    if project.user_directives_status == 'pending_discovery':
        project_phase = "DISCOVERY MODE - Case 2 selected, defining project shape"
        focus = "Complete project_discovery with automation context"
    elif project.user_directives_status == 'pending_parse':
        project_phase = "ONBOARDING MODE - Discussing directive files with user"
        focus = "Discuss directive files with user, help create/improve them, then parse"
    elif project.user_directives_status == 'in_progress':
        project_phase = "DEVELOPMENT MODE - Building automation code"
        focus = "Complete directive pipeline (validate → implement → approve → activate)"
        # Call user_directive_status for comprehensive directive reporting
        user_directive_report = call_directive("user_directive_status")
    elif project.user_directives_status == 'active':
        project_phase = "RUN MODE - Directives executing"
        focus = "Monitor directive execution, check health, handle errors"
        user_directive_report = call_directive("user_directive_status")
    elif project.user_directives_status == 'disabled':
        project_phase = "PAUSED - Directives deactivated"
        focus = "Resume directives when ready or debug issues"
```

**User Directive Status Values**:
- **NULL** (default): Use Case 1 project (regular software development)
- **'pending_discovery'**: Case 2 selected during project_discovery, discovery still in progress
- **'pending_parse'**: Discovery complete, AI discussing directive files with user
- **'in_progress'**: Use Case 2 project, AI building automation code (implementation phase)
- **'active'**: Use Case 2 project, directives deployed and running (execution phase)
- **'disabled'**: Use Case 2 project, directives temporarily paused

**Step 4: Build Priority-Based Status Tree**

AIMFP uses a priority-based system for determining current focus:

**Priority 1: Open Sidequests** (Highest priority)
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Priority 2: Current Subtask** (If no sidequests)
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Priority 3: Current Task** (If no subtasks)
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Step 4: Check for Ambiguities and Recent Context**

**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Step 5: Build Status Report**

```python
status_report = {
    "project": {
        "name": project_name,
        "purpose": project_purpose,
        "version": project_version,
        "status": project_status
    },
    "completion_path": [
        {
            "stage": stage_name,
            "status": stage_status,
            "progress": f"{completed_milestones}/{total_milestones} milestones"
        }
    ],
    "current_focus": {
        "type": "sidequest|subtask|task",  # Priority type
        "item": current_item,
        "parent": parent_context if applicable,
        "progress": f"{completed_items}/{total_items} items"
    },
    "historical_context": [
        {"task": "Previous task", "last_items": [...], "completed": True}
    ],
    "ambiguities": [
        {"note": "Type error in function", "severity": "warning"}
    ],
    "recent_activity": [
        {"note": "Added matrix operations", "source": "ai", "when": "2 hours ago"}
    ]
}
```

**Step 6: Return Status Report**

Returns structured JSON with full context, prioritized focus, and historical awareness. The helper returns **data only** — AI interprets the data and generates its own recommendations for the user.

---

## Interactions with Other Directives

### Called By

- **`aimfp_run`** - Routes continuation/status requests
- **`project_task_decomposition`** - Gets context before creating tasks
- **`project_evolution`** - Checks state before architectural changes

### Calls

- **`aimfp_status()`** - Orchestrator helper for comprehensive session-start state
- **`get_project_status()`** - Helper for work hierarchy data (counts + nested tree)
- Project helpers for infrastructure, metadata, notes, and git state queries

### Data Flow

```
User: "Continue"
  ↓
aimfp_run → aimfp_status
  ├─ Reads .aimfp-project/ProjectBlueprint.md
  ├─ Queries project.db (completion_path, milestones, tasks, notes)
  ├─ Builds structured status report
  └─ Returns to AI with recommendations
  ↓
AI uses context to continue work
```

---

## Examples

### Example 1: Basic Status Check

**User**: "Show me the status"

**AI calls**: `aimfp_status()`

**Returns**:
```json
{
  "success": true,
  "project": {
    "name": "Matrix Calculator",
    "purpose": "Pure FP matrix operations library",
    "version": 1,
    "status": "active"
  },
  "completion_path": [
    {
      "stage": "Foundation",
      "status": "completed",
      "progress": "5/5 milestones"
    },
    {
      "stage": "Core Development",
      "status": "in_progress",
      "progress": "2/4 milestones"
    }
  ],
  "current_focus": {
    "milestone": "Matrix Operations",
    "progress": "3/6 tasks",
    "open_tasks": [
      {"id": 5, "name": "Implement multiply_matrices", "priority": 1},
      {"id": 6, "name": "Add validation for dimensions", "priority": 2}
    ]
  },
}
```

**AI interprets the data and presents**:
```
📊 Matrix Calculator Status

Current Stage: Core Development (2/4 milestones)
Current Milestone: Matrix Operations (3/6 tasks)

Open Tasks:
  1. [Priority 1] Implement multiply_matrices
  2. [Priority 2] Add validation for dimensions

Shall I continue with multiply_matrices?
```

---

### Example 2: Continuation After Break

**User**: "Where were we?"

**AI calls**: `aimfp_status()`

**Returns**:
```json
{
  "success": true,
  "current_focus": {
    "milestone": "Testing Infrastructure",
    "progress": "1/3 tasks",
    "last_completed": {
      "task": "Set up pytest configuration",
      "when": "3 hours ago"
    },
    "in_progress": {
      "task": "Write property-based tests for add_matrices",
      "status": "in_progress"
    }
  },
  "recent_activity": [
    {"note": "Added hypothesis for property-based testing", "when": "3 hours ago"},
    {"note": "Created test_matrix.py with basic structure", "when": "3 hours ago"}
  ]
}
```

**AI presents**:
```
Last work: Set up pytest configuration (3 hours ago)
In progress: Write property-based tests for add_matrices

Recent activity:
  • Added hypothesis library
  • Created test_matrix.py structure

Let's continue with the property-based tests using hypothesis strategies.
```

---

### Example 3: Status with Sidequests

**User**: "What's the current status?"

**Returns**:
```json
{
  "success": true,
  "current_focus": {
    "milestone": "Matrix Operations",
    "progress": "4/6 tasks"
  },
  "sidequests": [
    {
      "name": "Fix type hint in add_matrices",
      "reason": "mypy reported error",
      "priority": 2,
      "status": "pending"
    }
  ],
}
```

**AI presents**:
```
⚠️ Sidequest Detected: Fix type hint in add_matrices
Reason: mypy error blocking progress

Should we fix this first? (Recommended: Yes)
```

---

### Example 4: Use Case 2 Status (Automation Project)

**User**: "Show status"

**Project Context**: Home automation project with user directives active

**Returns**:
```json
{
  "success": true,
  "project": {
    "name": "Home Automation",
    "purpose": "Automated home controls via user directives",
    "status": "active",
    "user_directives_status": "active"
  },
  "project_type": "Use Case 2 - Automation Project",
  "project_phase": "RUN MODE - Directives executing",
  "user_directives": {
    "total": 3,
    "active": 3,
    "inactive": 0,
    "directives": [
      {
        "name": "lights_off_5pm",
        "status": "active",
        "executions_today": 1,
        "last_execution": "2025-10-30 17:00:00",
        "success_rate": "100%"
      },
      {
        "name": "stove_alert",
        "status": "active",
        "executions_today": 0,
        "last_check": "2025-10-30 18:45:00",
        "success_rate": "100%"
      },
      {
        "name": "door_lock_check",
        "status": "active",
        "executions_today": 2,
        "last_execution": "2025-10-30 18:30:00",
        "success_rate": "100%"
      }
    ],
    "errors_last_24h": 0
  },
  "focus": "Monitor directive execution, check health, handle errors"
}
```

**AI presents**:
```
🏠 Home Automation Status
Project Type: Use Case 2 (Automation)
Phase: RUN MODE - Directives Executing

Active Directives: 3/3
  ✓ lights_off_5pm - Executed 1x today (last: 5:00 PM)
  ✓ stove_alert - Monitoring (last check: 6:45 PM)
  ✓ door_lock_check - Executed 2x today (last: 6:30 PM)

Health: All directives running normally
Errors (24h): 0

→ Directives are executing as expected. Use "show directive logs" for details.
```

**Use Case 2 vs Use Case 1**:
- **Use Case 1** (regular dev): Status shows code files, functions, tasks, completion paths
- **Use Case 2** (automation): Status shows directive execution health, runs, errors, monitoring

---

## Edge Cases

### Case 1: Project Not Initialized

**Trigger**: `.aimfp-project/` doesn't exist

**Response**:
```json
{
  "success": false,
  "error": "Project not initialized",
  "recommendation": "Run `aimfp init` to initialize project"
}
```

---

### Case 2: No Open Tasks

**Trigger**: All tasks completed for current milestone

**Response**:
```json
{
  "success": true,
  "current_focus": {
    "milestone": "Matrix Operations",
    "progress": "6/6 tasks",
    "status": "all_complete"
  }
}
```

---

### Case 3: Blueprint Out of Sync

**Trigger**: Blueprint checksum doesn't match database

**Response**:
```json
{
  "success": true,
  "warning": "ProjectBlueprint.md may be out of sync",
  "recommendation": "Run project_blueprint_update to sync",
  "current_focus": { ... }  // Still returns status
}
```

---

## Related Directives

### Primary Relationships

- **`aimfp_run`** - Routes to this directive
- **`project_blueprint_read`** - Parses blueprint file
- **`project_task_decomposition`** - Uses status for context
- **`project_completion_check`** - Validates completion progress

### Helper Functions

**Core helpers used by this directive:**

- **`aimfp_status(project_root, type="summary")`** — Session-start orchestrator. Returns comprehensive project state including metadata, infrastructure, work hierarchy, user directives status, recent warnings/errors, and git state. Called at session start to give AI a complete contextual picture.

- **`get_project_status(project_root, type="summary")`** — In-session helper. Retrieves work hierarchy data with counts, records, and nested tree in a single pass. Returns priority-based current focus (sidequest → subtask → task). Use this when AI needs fresh state mid-session (e.g., after context compression, after completing work items).
  - Returns: `{counts{}, completion_paths[], milestones[], tasks[], subtasks[], sidequests[], blocked_items[], tree{}}`

- **`get_task_context(task_id)`** — Retrieves complete context for resuming a specific task, including associated items, flows, files, and functions. Files are the ones linked to the task (and its subtasks) in `task_files` — linked automatically when tracked while the item is in_progress, or explicitly via `link_files_to_task`. Auto-detects task_type from task_id.

**Note**: Project detection and initialization routing (checking for `.aimfp-project/`, `.git/.aimfp/` backup, prompting user) is AI decision logic, not a helper function. See "Project Not Initialized" section above.

---

## Database Operations

**Read Operations**:
- Reads `.aimfp-project/ProjectBlueprint.md` (file system)
- Queries `project.db`:
  - `project` table - metadata
  - `completion_path` table - stage progress
  - `milestones` table - milestone status
  - `tasks` table - open tasks
  - `sidequests` table - active sidequests
  - `notes` table - recent activity

**Write Operations**:
- None (read-only directive)

---

## FP Compliance

**Purity**: ✅ Pure function
- No side effects (read-only)
- Deterministic given same database state
- Returns structured data

**Immutability**: ✅ Immutable
- No state mutations
- Database reads only
- Report structure frozen

**Side Effects**: ✅ Isolated
- File reads (effect isolated in helper)
- Database queries (effect isolated in helper)

---

## Error Handling

### File Not Found

**Trigger**: ProjectBlueprint.md missing

**Response**:
```json
{
  "success": true,
  "warning": "ProjectBlueprint.md not found",
  "fallback": "Using database only for status",
  "current_focus": { ... }  // Returns database-only status
}
```

### Database Query Failure

**Trigger**: Corrupt project.db or missing tables

**Response**:
```json
{
  "success": false,
  "error": "Database query failed: table 'tasks' does not exist",
  "recommendation": "Re-initialize project database"
}
```

---

## Best Practices

1. **Call for every continuation** - Provides essential context
2. **Use before task decomposition** - Prevents duplicate tasks
3. **AI generates recommendations** - Helper returns data, AI interprets and suggests next steps
4. **Show hierarchical progress** - Completion path → Milestones → Tasks
5. **Highlight sidequests** - Address blockers before continuing
6. **Include recent activity** - Helps AI remember context
7. **Be concise to user** - Format status clearly, not verbose

---

## Version History

- **v1.0** (2025-10-23): Initial status directive
- **v1.1** (2025-10-24): Added ProjectBlueprint.md integration
- **v1.2** (2025-10-25): Added sidequest support and recommendations

---

## Notes

- This directive is essential for **stateful continuation**
- Combines file-based (blueprint) and database context
- Enables AI to pick up where it left off
- Reports should be **actionable** with clear next steps
