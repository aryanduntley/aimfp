# Directive: project_subtask_complete

**Type**: Project
**Level**: 3 (Operational Execution)
**Parent Directive**: project_task_update
**Priority**: HIGH - Ensures parent tasks resume automatically

---

## Purpose

The `project_subtask_complete` directive handles the complete post-subtask completion workflow, ensuring parent tasks automatically resume when all blocking subtasks are finished.

Key responsibilities:
- Marks subtask as complete
- Checks all subtasks for parent task
- **Automatically resumes parent task** when all subtasks done
- Notifies user of remaining work if subtasks remain
- Maintains parent-child task relationships

Subtasks are **high-priority blocking work** that pause the parent task until complete. This directive ensures seamless resumption.

---

## When to Apply

This directive is automatically triggered when:
- `project_task_update` changes a subtask status to `completed`
- User explicitly says "complete subtask [name]"
- Called by `project_task_update` with subtask completion

**Delegation Chain**:
```
User marks subtask complete
  → project_task_update (validates and updates status)
  → project_subtask_complete (handles resumption logic)
  → Parent task resumes if all subtasks done
```

---

## Workflow

### Trunk: mark_subtask_complete

Primary execution path for subtask completion and parent resumption.

### Branches

**Branch 1: If subtask_valid_for_completion**
- **Then**: `mark_complete`
- **Details**:
  - Updates subtask status to `completed`
  - Sets `completed_at` timestamp
  - Logs completion
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Branch 2: If subtask_completed**
- **Then**: `check_parent_subtasks`
- **Details**:
  - Queries all subtasks for parent task
  - Counts remaining pending/in_progress subtasks
  - Determines if parent can resume
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
- **Result**: all_subtasks_complete or subtasks_remaining

**Branch 3: If all_subtasks_complete** ⭐ **AUTO-RESUME**
- **Then**: `resume_parent_task`
- **Details**:
  - Updates parent task status from `paused` to `in_progress`
  - Logs resume action
  - Notifies user that parent task is back in focus
  - Calls `aimfp_status` (brief) to show current context
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
- **Notification**: "All subtasks complete for task '[parent_task_name]'. Parent task resumed."

**Branch 4: If subtasks_remaining**
- **Then**: `notify_remaining_work`
- **Details**:
  - Queries remaining subtasks
  - Shows user what's left
  - Parent stays paused
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
- **Notification**: "Subtask '[subtask_name]' complete. [X] subtasks remaining for parent task '[parent_name]'."

**Fallback**: `prompt_user`
- Manual intervention needed for edge cases
- Log issue to notes

### Error Handling

**on_failure**: `prompt_user`
**retry**: max 1 attempt

- If resumption fails: Log warning, prompt user
- If parent not paused: Log warning (data integrity issue)

---

## Examples

### Example 1: Last Subtask Complete, Parent Resumes

**Context**:
- Parent Task: "Implement authentication system"
- Subtask 1: "Add email verification" → completed
- Subtask 2: "Add password reset" → in_progress (about to complete)

**User**: "Complete subtask 'Add password reset'"

**AI Execution**:
1. Marks subtask complete:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
2. Checks all subtasks for parent:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. All subtasks complete → **Resume parent task**:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
4. Logs action:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
5. Calls `aimfp_status` (brief) to show updated context
6. Notifies user:
   ```
   Subtask 'Add password reset' completed!

   All subtasks complete for task 'Implement authentication system'.
   Parent task resumed and ready to continue.

   Current focus: Implement authentication system
   ```

**Result**: Parent task automatically resumes, user can continue work

### Example 2: Subtasks Remaining, Parent Stays Paused

**Context**:
- Parent Task: "Build user dashboard"
- Subtask 1: "Create layout component" → in_progress (about to complete)
- Subtask 2: "Add user profile widget" → pending
- Subtask 3: "Add activity feed widget" → pending

**User**: "Complete subtask 'Create layout component'"

**AI Execution**:
1. Marks subtask complete
2. Checks all subtasks:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. Subtasks remaining → **Show remaining work**:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
4. Notifies user:
   ```
   Subtask 'Create layout component' completed!

   2 subtasks remaining for parent task 'Build user dashboard':
   1. Add user profile widget (pending)
   2. Add activity feed widget (pending)

   Parent task will resume once all subtasks are complete.
   ```

**Result**: Parent stays paused, user aware of remaining work

### Example 3: Parent Not Paused (Data Integrity Warning)

**User**: "Complete subtask 'Fix login bug'"

**AI Execution**:
1. Marks subtask complete
2. Checks parent task status:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. **Data integrity issue detected**:
   - Logs warning:
     **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
   - Notifies user:
     ```
     ⚠️ Warning: Subtask 'Fix login bug' completed, but parent task is not paused.
     This may indicate a data integrity issue. Parent task status: in_progress

     Please verify parent-subtask relationships are correct.
     ```

**Result**: Issue flagged for user review

---

## Integration with Other Directives

### Called By:
- `project_task_update` - Delegates when subtask status changes to completed

### Calls:
- `aimfp_status` - Shows updated context after parent resumes (brief mode)

### Triggers:
- Parent task state change (`paused` → `in_progress`)

---

## Database Updates

### Tables Modified:

**project.db**:
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

### Tables Queried:
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

---

## Roadblocks and Resolutions

### Roadblock 1: parent_task_not_paused
**Issue**: Parent task not in `paused` state when subtasks exist
**Resolution**: Log warning, notify user of data integrity issue

### Roadblock 2: parent_task_completed
**Issue**: Cannot resume completed parent task
**Resolution**: This is a critical error - log and prompt user to investigate

### Roadblock 3: database_lock
**Issue**: Task locked during update
**Resolution**: Retry once, then prompt user

### Roadblock 4: tracking_audit_blocked
**Issue**: Completion refused: the tracking audit of the subtask's linked files found blocking drift (missing/stale functions, types or interactions, repoints, parameter drift, incomplete or malformed records, unfinished reservations). The error carries the counts and a report path.
**Resolution**: The refusal names the exact fix calls (reconcile_paths, apply_audit_fixes(report=..., apply=[...]), prose updates) — run them and complete again. Avoid the refusal altogether by running audit_tracking(task_id=..., task_type=...) as a pre-flight before marking complete. Pass audit_override_reason only for drift that is understood and scheduled; it is recorded as a warning note. A finding a person reviewed as not drift (a function or type the audit cannot see, a signature it cannot read) is exempted once with apply_audit_fixes(reason=..., exempt=[{category, function_id | type_id}]) instead of overriding every completion; the exemption lapses when the function's code changes. Warnings never block. The user setting audit_gate (block | warn | off, default block) relaxes the gate for a project mid-adoption: with warn, completion succeeds and the result lists would_block findings.

---

## Intent Keywords

- "complete subtask"
- "finish subtask"
- "subtask done"
- "resume parent"
- "subtask finished"

**Confidence Threshold**: 0.8

---

## Related Directives

- `project_task_update` - Parent directive that delegates here
- `project_subtask_create` - Creates subtasks and pauses parent
- `project_task_complete` - Sibling directive for task completion
- `project_sidequest_complete` - Sibling directive for sidequest completion
- `aimfp_status` - Provides context after resumption

---

## Notes

- **Automatic resumption** is the key feature - no user action needed
- Parent task status should always be `paused` when subtasks exist
- Subtasks are high-priority by default (defined in `project_subtask_create`)
- Always check data integrity (parent status)
- Brief status update keeps user informed without overwhelming
- Logs all state changes for audit trail
