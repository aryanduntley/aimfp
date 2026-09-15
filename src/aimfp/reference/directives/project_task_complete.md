# Directive: project_task_complete

**Type**: Project
**Level**: 3 (Operational Execution)
**Parent Directive**: project_task_update
**Priority**: HIGH - Essential for continuous project momentum

---

## Purpose

The `project_task_complete` directive handles the complete post-task completion workflow, ensuring continuous forward project progress. When a task is marked complete, this directive:

- Marks task and all items as complete
- Checks milestone progress
- Reviews completion_path status
- **Engages user to plan next task** (critical for maintaining momentum)
- Delegates to `project_milestone_complete` if milestone is done

This directive ensures that **completing a task is never the end** - it automatically transitions to planning the next step, preventing project stagnation.

---

## When to Apply

This directive is automatically triggered when:
- `project_task_update` changes a task status to `completed`
- User explicitly says "complete task [name]"
- All items in a task are marked complete

**Delegation Chain**:
```
User marks task complete
  → project_task_update (validates and updates status)
  → project_task_complete (handles post-completion workflow)
  → IF milestone complete → project_milestone_complete
```

---

## Workflow

### Trunk: mark_task_complete

Primary execution path that handles task completion and next-step planning.

### Branches

**Branch 1: If task_valid_for_completion**
- **Then**: `mark_complete_and_items`
- **Details**:
  - First verifies task files: `get_task_context(task_id)` — `files[]` must list every file worked on. Files auto-link while the task is in_progress; link any worked on earlier with `link_files_to_task(task_id, file_ids)`
  - Updates task status to `completed`
  - Sets `completed_at` timestamp
  - Marks all items as complete
  - Logs completion time and duration
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Branch 2: If task_completed**
- **Then**: `check_milestone_status`
- **Details**:
  - Queries remaining tasks in milestone
  - Counts pending/in_progress tasks
  - Determines if milestone is complete
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
- **Result**: milestone_complete or milestone_not_complete

**Branch 3: If milestone_complete**
- **Then**: `call_project_milestone_complete`
- **Details**:
  - Delegates to `project_milestone_complete` directive
  - Passes milestone_id
  - Milestone directive handles all next-milestone planning
- **Delegation**: `project_milestone_complete(milestone_id)`

**Branch 4: If milestone_not_complete** ⭐ **CRITICAL BRANCH**
- **Then**: `review_next_steps`
- **Details**:
  - Calls `aimfp_status` (brief mode) to get context
  - Shows completion_path progress (e.g., "Foundation: 3/6 milestones complete")
  - Shows milestone progress (e.g., "Database Schemas: 4/5 tasks complete")
  - Queries next pending tasks:
    **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
  - Presents user with options:
    - Continue with next task
    - Create new task
    - Pivot to different milestone
    - Review completion path
  - **Engages user in conversation** about next steps
- **Prompt Template**:
  ```
  Task '[task_name]' completed successfully!

  Milestone '[milestone_name]' progress: [X/Y tasks complete]
  Completion Path '[path_name]' progress: [A/B milestones complete]

  Next pending tasks:
  1. [task_1]
  2. [task_2]
  3. [task_3]

  What would you like to work on next?
  ```

**Branch 5: If user_chooses_next_task**
- **Then**: `set_next_task_in_progress`
- **Details**:
  - User selects from pending tasks
  - Updates selected task to `in_progress`
  - Calls helper: `get_project_tasks(milestone_id, status='pending')`
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Branch 6: If user_chooses_create_task**
- **Then**: `call_project_task_create`
- **Details**:
  - Delegates to `project_task_create`
  - Auto-creates items if user provides task details
  - Returns to task work immediately

**Fallback**: `prompt_user`
- User clarification needed
- Log to notes for context

### Error Handling

**on_failure**: `prompt_user`
**retry**: max 1 attempt

- If completion fails: Rollback and prompt user
- If milestone query fails: Continue with task completion, defer milestone check

---

## Examples

### Example 1: Task Complete, Milestone Not Complete

**User**: "Mark task 'Create project.sql schema file' complete"

**AI Execution**:
1. Validates: Task exists, status is `in_progress`
2. Marks task complete:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. Marks all items complete (5 items)
4. Checks milestone:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
5. Milestone NOT complete → **Review next steps**:
   - Calls `aimfp_status` (brief)
   - Shows progress: "Database Schemas: 4/5 tasks complete"
   - Queries pending tasks:
     **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
6. Engages user:
   ```
   Task 'Create project.sql schema file' completed!

   Milestone 'Database Schemas' progress: 4/5 tasks complete
   Completion Path 'Foundation' progress: 2/6 milestones complete

   Next pending task:
   1. Review and update sync-directives.py

   Continue with next task, or create a new one?
   ```
7. User responds: "Continue with next task"
8. Sets task #21 to `in_progress`

**Result**: Seamless transition to next task

### Example 2: Task Complete, Milestone Complete

**User**: "Complete task 'Review and update sync-directives.py'"

**AI Execution**:
1. Marks task complete (task #21)
2. Checks milestone:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. Milestone complete → **Delegates to project_milestone_complete**:
   - Passes milestone_id = 2
   - Milestone directive handles:
     - Marking milestone complete
     - Moving to next milestone
     - Creating first task

**Result**: Automatic escalation to milestone completion workflow

### Example 3: No Pending Tasks, User Creates New

**User**: "Complete task 'Create user_directives.sql schema file'"

**AI Execution**:
1. Marks task complete
2. Checks milestone: 4/5 tasks complete
3. Queries pending tasks:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
4. Engages user:
   ```
   Task 'Create user_directives.sql schema file' completed!

   Milestone 'Database Schemas' progress: 4/5 tasks complete

   No pending tasks in this milestone. Would you like to:
   1. Create new task
   2. Move to next milestone
   3. Review milestone status
   ```
5. User responds: "Create new task for schema validation"
6. Delegates to `project_task_create`:
   - Prompts for task details
   - Creates task with items
   - Returns to work

**Result**: Flexible task creation when needed

---

## Integration with Other Directives

### Called By:
- `project_task_update` - Delegates when task status changes to completed

### Calls:
- `aimfp_status` - Gets current project context (brief mode)
- `project_milestone_complete` - If milestone is done
- `project_task_create` - If user wants to create new task
- `get_project_tasks` (helper) - Queries pending tasks

### Triggers:
- `project_milestone_complete` - When all milestone tasks done
- `project_task_create` - When user opts to create new task

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

### Roadblock 1: active_subtasks_blocking
**Issue**: Cannot complete task with active subtasks
**Resolution**: Block completion, notify user to complete or cancel subtasks first

### Roadblock 2: no_pending_tasks
**Issue**: No pending tasks in milestone
**Resolution**: Offer to create new task or move to next milestone

### Roadblock 3: user_unavailable
**Issue**: User not available for next-step discussion
**Resolution**: Log completion, defer planning to next session, update notes

---

## Intent Keywords

- "complete task"
- "finish task"
- "task done"
- "mark complete"
- "task finished"

**Confidence Threshold**: 0.8

---

## Related Directives

- `project_task_update` - Parent directive that delegates here
- `project_milestone_complete` - Called when milestone is done
- `project_task_create` - Called when user creates new task
- `aimfp_status` - Provides context for next-step planning
- `project_subtask_complete` - Sibling directive for subtask completion
- `project_sidequest_complete` - Sibling directive for sidequest completion

---

## Notes

- **Critical**: This directive prevents "what's next?" gaps by automatically engaging user
- Always shows progress context (milestone and completion_path)
- Queries pending tasks proactively
- Offers clear options to maintain momentum
- Logs all completion events for audit trail
- Delegates to milestone completion when appropriate
- Uses brief `aimfp_status` to avoid overwhelming user with details
