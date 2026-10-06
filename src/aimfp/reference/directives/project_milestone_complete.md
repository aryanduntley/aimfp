# Directive: project_milestone_complete

**Type**: Project
**Level**: 3 (Operational Execution)
**Parent Directive**: project_task_complete
**Priority**: CRITICAL - Ensures continuous project momentum across milestones

---

## Purpose

The `project_milestone_complete` directive handles the complete post-milestone completion workflow, ensuring seamless transition to the next phase of the project.

Key responsibilities:
- Marks milestone as complete
- Updates completion_path progress
- **Reviews overall project status**
- **Moves to next milestone automatically**
- **Creates first task for next milestone** (with user input)
- Checks for project completion

This directive ensures that **completing a milestone immediately transitions to the next phase**, preventing project stagnation and maintaining forward momentum.

---

## When to Apply

This directive is automatically triggered when:
- `project_task_complete` detects all tasks in milestone are done
- User explicitly says "complete milestone [name]"
- Final task in milestone is marked complete

**Delegation Chain**:
```
Final task in milestone completes
  → project_task_complete (detects milestone complete)
  → project_milestone_complete (handles next-milestone transition)
  → Creates first task for next milestone
  → IF all completion_paths complete → project_completion_check
```

---

## Workflow

### Trunk: mark_milestone_complete

Primary execution path for milestone completion and next-phase planning.

### Branches

**Branch 0: If milestone_valid_for_completion — flow review first**
- **Then**: `review_milestone_flows`
- **Why**: flows evolve with milestones. A milestone is where new behaviour lands, so it is where flows drift: descriptions go stale, one flow quietly absorbs three behaviours, new behaviour ships with no flow at all. Closing the milestone is the checkpoint that keeps the modularity layer honest.
- **Details**:
  - `get_flows_for_milestone(milestone_id)` — the flows this milestone built or changed
  - For each: `update_flow` the description to match what was actually built
  - Split any flow that absorbed several behaviours (`get_structure_health` reports long descriptions as `oversized_flows`): `add_flow` per behaviour with `theme_ids`, `move_files_to_flow` for its files, `add_milestone_flows` for this milestone
  - Behaviour built with no flow: `add_flow`, link its files and this milestone
  - `get_structure_health()`: fix the gaps this milestone introduced (files without flow or module, flows without theme)
  - `add_note(note_type='evolution')` summarising the flow changes

**Branch 1: If milestone_valid_for_completion**
- **Then**: `mark_complete_and_update_path`
- **Details**:
  - Updates milestone status to `completed`
  - Sets `completed_at` timestamp
  - Queries associated completion_path
  - Checks if completion_path is done
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Branch 2: If completion_path_complete**
- **Then**: `mark_path_complete`
- **Details**:
  - Updates completion_path status to `completed`
  - Logs path completion
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Branch 3: If completion_path_complete**
- **Then**: `check_project_completion`
- **Details**:
  - Queries all completion_paths for project
  - Checks if project is entirely complete
- **SQL**:
  **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
- **Result**: project_complete or project_not_complete

**Branch 4: If project_complete** 🎉
- **Then**: `call_project_completion_check`
- **Details**:
  - All completion_paths done!
  - Delegates to `project_completion_check` for final validation
  - Triggers project archival workflow
- **Delegation**: `project_completion_check(project_id, trigger_final_review=true)`

**Branch 5: If project_not_complete** ⭐ **NEXT MILESTONE**
- **Then**: `move_to_next_milestone`
- **Details**:
  - Calls `aimfp_status` (brief) to show overall progress
  - Shows completion_path progress
  - Queries next pending milestone:
    **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
  - Presents milestone completion summary:
    ```
    Milestone '[milestone_name]' completed!
    Project progress: [X/Y completion paths complete]
    Moving to next milestone: '[next_milestone_name]'
    ```

**Branch 6: If next_milestone_identified**
- **Then**: `discuss_first_task`
- **Details**:
  - Engages user in planning first task
  - Options:
    1. Let AI suggest first task based on milestone description
    2. User defines first task manually
    3. Review milestone details before deciding
  - **Critical**: AI and user collaborate on next steps
- **Prompt**:
  ```
  Next milestone: '[milestone_name]'
  Description: [milestone_description]

  What should be the first task?
  ```

**Branch 7: If user_chooses_ai_suggestion**
- **Then**: `suggest_and_create_task`
- **Details**:
  - AI analyzes milestone description
  - Suggests task name and task items
  - Prompts user for approval:
    ```
    Suggested first task:
    Name: [suggested_task_name]
    Items:
    - [item_1]
    - [item_2]
    - [item_3]

    Approve and create this task?
    ```
  - If approved: Delegates to `project_task_create`

**Branch 8: If user_defines_task**
- **Then**: `create_user_defined_task`
- **Details**:
  - Prompts for task name
  - Prompts for task description
  - Prompts for task items
  - Delegates to `project_task_create`

**Fallback**: `prompt_user`
- User clarification needed
- Review milestone details

### Error Handling

**on_failure**: `prompt_user`
**retry**: max 1 attempt

- If milestone update fails: Rollback and prompt user
- If next milestone not found: Check for project completion or pivot

---

## Examples

### Example 1: Milestone Complete, Next Milestone Ready

**Context**:
- Completed: Milestone "Database Schemas" (5/5 tasks complete)
- Completion Path: "Foundation" (3/6 milestones complete)
- Next: Milestone "Core Types"

**Triggered by**: project_task_complete (final task in milestone)

**AI Execution**:
1. Marks milestone complete:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
2. Checks completion_path:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. Path NOT complete → **Move to next milestone**:
   - Calls `aimfp_status` (brief)
   - Queries next milestone:
     **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
4. Presents to user:
   ```
   🎉 Milestone 'Database Schemas' completed!

   Project progress: Foundation (3/6 milestones complete)

   Moving to next milestone: 'Core Types'
   Description: Define core FP-compliant types and data structures

   What should be the first task for this milestone?
   1. Let AI suggest first task
   2. I'll define the first task
   3. Review milestone details first
   ```
5. User chooses: "Let AI suggest first task"
6. AI suggests:
   ```
   Suggested first task:
   Name: Define Result type for error handling
   Items:
   - Create Result<T, E> type definition
   - Add success and error constructors
   - Implement pattern matching helpers
   - Write unit tests
   - Document usage examples

   Approve and create this task?
   ```
7. User approves
8. Delegates to `project_task_create`:
   - Creates task with 5 items
   - Sets status to `in_progress`
   - Links to milestone 3

**Result**: Seamless transition to next milestone with first task created

### Example 2: Completion Path Complete, More Paths Remain

**Context**:
- Completed: Milestone "Packaging" (final milestone in "Finalization" path)
- Completion Path: "Finalization" (3/3 milestones complete)
- Project: 5/6 completion paths complete
- Next: Completion Path "Documentation"

**AI Execution**:
1. Marks milestone complete
2. Checks completion_path:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. **Completion path complete**:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
4. Checks project completion:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
5. Project NOT complete → **Move to next completion_path**:
   - Queries next milestone from remaining path
   - Presents similar workflow as Example 1

**Result**: Automatic transition to next completion_path

### Example 3: Project Complete!

**Context**:
- Completed: Final milestone in final completion_path
- All completion_paths: 6/6 complete

**AI Execution**:
1. Marks milestone complete
2. Marks completion_path complete
3. Checks project completion:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
4. **Project complete**! 🎉
5. Delegates to `project_completion_check`:
   - Validates project is truly done
   - Generates completion report
   - Offers archival options
6. Presents to user:
   ```
   🎉🎉🎉 PROJECT COMPLETE! 🎉🎉🎉

   All completion paths finished:
   - Foundation (6/6 milestones)
   - Core Helpers (4/4 milestones)
   - MCP Server (5/5 milestones)
   - Core Directives (3/3 milestones)
   - Directive Expansion (4/4 milestones)
   - Finalization (3/3 milestones)

   The AIMFP project is complete and ready for deployment!

   Next steps:
   1. Review final documentation
   2. Archive project
   3. Deploy to production
   ```

**Result**: Project completion validated and celebrated!

### Example 4: No Next Milestone (Unexpected)

**Context**:
- Milestone complete but no next milestone found
- Possible data issue or incomplete roadmap

**AI Execution**:
1. Marks milestone complete
2. Queries next milestone:
   **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
3. **No next milestone found**:
   - Checks if all milestones/paths complete (should trigger project_complete)
   - If not, this is an issue
4. Prompts user:
   ```
   ⚠️ Milestone 'Database Schemas' complete, but no next milestone found.

   This may indicate:
   1. Project roadmap is incomplete
   2. All work is done (project complete)
   3. Data integrity issue

   Would you like to:
   1. Review ProjectBlueprint.md to clarify roadmap
   2. Call project_completion_check to verify status
   3. Manually create next milestone
   ```

**Result**: Edge case handled gracefully

---

## Integration with Other Directives

### Called By:
- `project_task_complete` - Delegates when all tasks in milestone are done

### Calls:
- `aimfp_status` - Shows project progress (brief mode)
- `project_task_create` - Creates first task for next milestone
- `project_completion_check` - If all completion_paths done
- `project_evolution` - If user wants to pivot roadmap

### Triggers:
- Next milestone activation
- First task creation for new milestone
- Project completion validation (if done)

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

### Roadblock 1: no_next_milestone
**Issue**: No next milestone found after completion
**Resolution**: Check for project completion or review roadmap with user

### Roadblock 2: next_milestone_unclear
**Issue**: Milestone description is vague, can't suggest first task
**Resolution**: Review ProjectBlueprint.md with user to clarify

### Roadblock 3: user_wants_pivot
**Issue**: User wants to change roadmap after milestone
**Resolution**: Call `project_evolution` to handle roadmap change

### Roadblock 4: data_integrity_issue
**Issue**: Milestone marked complete but tasks remain
**Resolution**: Log error, prompt user to investigate

---

## Intent Keywords

- "complete milestone"
- "finish milestone"
- "milestone done"
- "next milestone"
- "move to next phase"

**Confidence Threshold**: 0.8

---

## Related Directives

- `project_task_complete` - Parent directive that triggers this
- `project_task_create` - Creates first task for next milestone
- `project_completion_check` - Called when all paths complete
- `aimfp_status` - Provides project progress context
- `project_evolution` - Handles roadmap changes/pivots

---

## Notes

- **Automatic milestone transition** is the key feature - maintains momentum
- Always engages user for first task planning (AI suggests but user decides)
- Shows comprehensive progress context (paths, milestones, tasks)
- Handles project completion gracefully
- Logs all milestone and path completions for audit trail
- Brief status updates keep user informed without overwhelming
- Celebrates project completion when it happens! 🎉
