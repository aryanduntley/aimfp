# Directive: project_file_delete

**Type**: Project
**Level**: 3 (Operational Execution)
**Parent Directive**: project_file_write
**Priority**: HIGH - File removal with database cleanup

---

## Purpose

The `project_file_delete` directive handles safe deletion of files from the project using an **ERROR-first approach**. The delete_file() helper NEVER automatically cascades deletes - instead, it returns detailed error information about dependencies that must be cleaned up first, forcing intentional and systematic cleanup to prevent accidental data loss.

Key responsibilities:
- **Call delete_file() helper** - Attempts deletion, returns error if dependencies exist
- **Handle error response** - Parse dependency lists (functions, types, file_flows)
- **Systematic cleanup** - Delete functions, unlink types_functions, remove file_flows entries
- **Retry deletion** - After cleanup, retry delete_file() until success
- **Remove from filesystem** - After database deletion succeeds

This is the **safe file remover** - no cascading deletes, no automatic cleanup, forces AI to be intentional.

---

## ERROR-First Deletion Flow

### The delete_file() Helper Behavior

**On Error** (dependencies exist):
```json
{
  "success": false,
  "error": "dependencies_exist",
  "functions": [
    {"id": 1, "name": "calculate_id1", "has_types_functions": true},
    {"id": 2, "name": "process_id2", "has_types_functions": false}
  ],
  "types": [
    {"id": 5, "name": "Result_id5", "has_types_functions": true}
  ],
  "file_flows": [
    {"file_id": 10, "flow_id": 3, "flow_name": "authentication"}
  ]
}
```

**On Success** (no dependencies):
```json
{
  "success": true,
  "deleted_file_id": 10
}
```

---

## When to Apply

This directive applies when:
- **Removing obsolete code** - Deleting files no longer needed
- **Refactoring** - Consolidating files, removing duplicates
- **Cleanup** - Removing test files, prototypes, or experiments
- **Project evolution** - Removing deprecated implementations

---

## Workflow

### Trunk: validate_delete_request

Validates file and attempts deletion via helper.

**Steps**:
1. **Validate file path** - Check file is within project and tracked in database
2. **Call delete_file(file_id, note_reason, note_severity, note_source, note_type)** - Attempt deletion
3. **Check result**:
   - If `success: true` → Remove file from filesystem, done
   - If `success: false` → Dependencies exist, enter cleanup loop

### Branch 1: If file_tracked_in_db

**Then**: `call_delete_file_helper`

Call the delete_file() helper function:
```
delete_file(
  file_id=file_id,
  note_reason="User requested file deletion",
  note_severity="info",
  note_source="ai",
  note_type="entry_deletion"
)
```

### Branch 2: If delete_file_returns_error

**Then**: `dependencies_exist_cleanup_loop`

**Error Structure**:
```json
{
  "success": false,
  "error": "dependencies_exist",
  "functions": [...],     // Functions that must be deleted
  "types": [...],         // Types that must be deleted
  "file_flows": [...]     // file_flows entries that must be removed
}
```

**Cleanup Steps** (in order):

**Step 1: Delete Functions**

For each function in the error's `functions` array:

a) Call `delete_function` for the function. A function that still has interactions is refused with the edges listed (`interactions_exist`): pass `repoint_to` when another function took over its job, or `confirm_cascade` when the dependencies are really gone. Either way the deletion note records every moved or removed edge, so the deletion can be undone.

b) If delete_function returns a type-relationship error:
```json
{
  "success": false,
  "error": "types_functions_exist",
  "type_relationships": [
    {"type_id": 5, "type_name": "Result_id5", "role": "transformer"}
  ]
}
```

c) For each type_relationship: Delete the types_functions entry:
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

d) Retry delete_function() - should now succeed

**Step 2: Delete Types**

For each type in the error's `types` array:

a) Call `delete_type(type_id, note_reason, note_severity, note_source, note_type)`

b) If delete_type returns error:
```json
{
  "success": false,
  "error": "types_functions_exist",
  "function_relationships": [
    {"function_id": 8, "function_name": "create_result_id8", "role": "factory"}
  ]
}
```

c) For each function_relationship: Delete the types_functions entry:
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

d) Retry delete_type() - should now succeed

**Step 3: Remove file_flows Entries**

For each file_flow in the error's `file_flows` array:

Delete the entry manually:
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**Step 4: Retry delete_file()**

After all dependencies cleaned:
```
delete_file(file_id, note_reason, note_severity, note_source, note_type)
```

Should now return:
```json
{"success": true, "deleted_file_id": 10}
```

### Branch 3: If delete_file_success

**Then**: `remove_from_filesystem`

After database deletion succeeds:
1. Remove file from filesystem
2. Confirm deletion
3. Report success to user

**Fallback**: `prompt_user_for_clarification`

If validation fails or user cancels.

---

## Examples

### Example 1: Simple Delete (No Dependencies)

**User**: "Delete src/old_helper.py"

**AI Execution**:
1. Validates path: `src/old_helper.py` ✓ Valid
2. Gets file_id from database: 42
3. Calls `delete_file(42, "User requested deletion", "info", "ai", "entry_deletion")`
4. Response:
   ```json
   {"success": true, "deleted_file_id": 42}
   ```
5. Removes `src/old_helper.py` from filesystem
6. Reports: "✓ File deleted successfully"

### Example 2: Delete with Dependencies (Cleanup Required)

**User**: "Delete src/calculator.py"

**AI Execution**:
1. Validates path: `src/calculator.py` ✓ Valid
2. Gets file_id: 10
3. Calls `delete_file(10, ...)`
4. Response:
   ```json
   {
     "success": false,
     "error": "dependencies_exist",
     "functions": [
       {"id": 5, "name": "add_id5", "has_types_functions": false},
       {"id": 6, "name": "multiply_id6", "has_types_functions": true}
     ],
     "types": [],
     "file_flows": [
       {"file_id": 10, "flow_id": 2, "flow_name": "math_operations"}
     ]
   }
   ```
5. **Cleanup Loop**:

   a) Delete function 5 (no types_functions):
      ```
      delete_function(5, "Deleting function before file deletion", "info", "ai", confirm_cascade=true)
      → {"success": true, "deleted_function_id": 5, "file_id": 10, "interactions": [...removed edges...]}
      ```

   b) Delete function 6 (has types_functions):
      ```
      delete_function(6, ...)
      → {
          "success": false,
          "error": "types_functions_exist",
          "type_relationships": [
            {"type_id": 8, "type_name": "Number_id8", "role": "operator"}
          ]
        }
      ```

      Unlink types_functions:
      **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

      Retry:
      ```
      delete_function(6, ...)
      → {"success": true, "deleted_function_id": 6, "file_id": 10}
      ```

   c) Remove file_flows:
      **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

6. **Retry delete_file**:
   ```
   delete_file(10, ...)
   → {"success": true, "deleted_file_id": 10}
   ```

7. Remove `src/calculator.py` from filesystem
8. Reports: "✓ File deleted after cleaning 2 functions and 1 flow association"

### Example 3: Complex Nested Dependencies

**User**: "Delete src/types.py"

**AI Execution**:
1. Gets file_id: 15
2. Calls `delete_file(15, ...)`
3. Response shows: 0 functions, 3 types, 1 file_flow
4. **Delete type 1**: Error - has types_functions links
   - Unlink all types_functions entries for type 1
   - Retry - success
5. **Delete type 2**: Success (no links)
6. **Delete type 3**: Error - has types_functions links
   - Unlink all types_functions entries for type 3
   - Retry - success
7. Remove file_flows entry
8. Retry delete_file(15) - success
9. Remove from filesystem
10. Reports: "✓ File deleted after cleaning 3 types and unlinking type-function relationships"

---

## Roadblocks and Resolutions

### Roadblock 1: dependencies_exist

**Issue**: Functions, types, or file_flows exist for this file
**Resolution**: Systematically delete functions, types, remove file_flows, then retry

### Roadblock 2: types_functions_exist_when_deleting_function

**Issue**: Cannot delete function because it has type relationships
**Resolution**: Unlink types_functions entries before deleting function

### Roadblock 3: types_functions_exist_when_deleting_type

**Issue**: Cannot delete type because it has function relationships
**Resolution**: Unlink types_functions entries before deleting type

---

## Safety Principles

- **ERROR-first**: Never auto-cascade - always return error with details
- **Intentional cleanup**: Force AI to systematically handle each dependency
- **No data loss**: Prevent accidental deletion of linked data
- **Clear feedback**: Error responses contain full dependency lists
- **Predictable**: Same pattern for file, function, type deletions

---

## Related Directives

- `project_file_write` - Creates files
- `project_file_read` - Reads files
- `project_update_db` - Syncs database

---

## Notes

- **No automatic cascading** - AI must handle each dependency explicitly
- **Error responses guide cleanup** - Detailed lists show what to delete
- **No soft delete** - Either file exists or it doesn't
- **No backups** - User responsible for version control
- **Transactional** - Database operations are atomic
