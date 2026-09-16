# Directive: user_directive_update

**Type**: User System
**Level**: N/A (Event-driven)
**Parent Directive**: Multiple (parse, validate, implement)
**Priority**: High

---

## Purpose

Detect and handle changes to user directive source files, re-processing modified directives through the appropriate pipeline stages. This directive enables iterative development of directives.

**Key principle**: When a user modifies a directive source file, the system must detect the change, deactivate if active, re-validate, re-implement, and require re-approval before reactivation.

**Responsibilities**:
1. Detect source file changes (checksum monitoring)
2. Determine what changed (trigger, action, conditions)
3. Deactivate directive if currently active
4. Reset approval status (approved = false)
5. Loop back through appropriate pipeline stages
6. Require user re-approval before reactivation

---

## When to Apply

This directive applies when:
- User modifies directive source file and saves
- User says: "Update directive {name}" after editing file
- Detected trigger: File checksum mismatch on source_files table
- User says: "Reload directives" / "Refresh directives"

**Automatic detection** if file watching enabled, **manual trigger** otherwise.

---

## Workflow

### Trunk: Detect and Process Updates

#### Step 1: Detect Source File Changes

1. **File watching** (if enabled):
   ```python
   # Watch directive source files for changes
   watcher = FileSystemWatcher()
   watcher.watch_directory("directives/")

   @watcher.on_modified
   def on_file_modified(file_path):
       if file_path.endswith(('.yaml', '.json', '.txt')):
           trigger_update(file_path)
   ```

2. **Manual check** (on user command):
   ```sql
   -- Check checksums for all directive source files
   SELECT sf.file_path, sf.checksum, ud.id, ud.name, ud.status
   FROM source_files sf
   JOIN user_directives ud ON ud.source_file_id = sf.id
   WHERE sf.checksum != calculate_current_checksum(sf.file_path);
   ```

3. **Identify changed directives**:
   ```python
   changed_files = detect_changed_files()

   for file_path in changed_files:
       directives = get_directives_from_file(file_path)
       for directive in directives:
           process_update(directive)
   ```

#### Step 2: Notify User of Changes

```
AI: "🔄 Directive Source File Changed

File: directives/lights.yaml
Directives affected: turn_off_lights_5pm
Status: Active

Detected changes require re-validation and re-implementation.

If this directive is active, it will be deactivated.
You'll need to test and re-approve before reactivating.

Process update now? (yes/no/diff)"
```

**If user chooses "diff"**:
```
AI: "Comparing old vs new configuration...

Changes detected:
- trigger.time: '17:00' → '18:00' (Changed: 5pm to 6pm)

Do you want to proceed with update? (yes/no)"
```

#### Step 3: Deactivate if Active

1. **Check if directive is active**:
   ```sql
   SELECT status FROM user_directives WHERE id = ?;
   ```

2. **If active, deactivate**:
   ```python
   if directive.status == 'active':
       # Call user_directive_deactivate
       deactivate_directive(directive_id)

       AI: "⚠️  Deactivating '{directive_name}' for update..."
       # Stops scheduler/service, updates status
   ```

#### Step 4: Reset Approval Status

```sql
UPDATE user_directives
SET approved = false,
    status = 'in_progress',
    updated_at = CURRENT_TIMESTAMP
WHERE id = ?;

-- Project status back to in_progress if this was only active directive
UPDATE project
SET user_directives_status = 'in_progress'
WHERE id = ?
  AND (SELECT COUNT(*) FROM user_directives WHERE status = 'active') = 0;
```

#### Step 5: Re-Parse Source File

```python
# Call user_directive_parse for the modified file
parsed_directives = parse_directive_file(file_path)

# Compare with existing directive
old_config = directive.validated_config
new_parsed = parsed_directives[directive_name]

# Identify what changed
changes = compare_configs(old_config, new_parsed)
```

#### Step 6: Re-Validate (Interactive)

```python
# Call user_directive_validate
# May need to ask fewer questions if only minor changes

AI: "Re-validating '{directive_name}'...

I detected these changes:
- {change 1}
- {change 2}

I need to ask {N} questions to validate:

Q1: {question about change 1}
..."

# Follow normal validation workflow
```

#### Step 7: Re-Implement

```python
# Call user_directive_implement
# Generate new implementation code

AI: "Re-implementing '{directive_name}' with updated configuration...

Generated files (updated):
- src/directives/{name}/trigger.py (modified)
- src/directives/{name}/__init__.py (modified)

Tests updated:
- tests/directives/test_{name}.py

⚠️  IMPORTANT: Please retest the updated implementation.
When ready, approve again: 'approve directive {name}'"
```

#### Step 8: Update Source File Tracking

```sql
-- Update checksum
UPDATE source_files
SET checksum = ?,
    last_modified_at = CURRENT_TIMESTAMP,
    last_parsed_at = CURRENT_TIMESTAMP
WHERE file_path = ?;

-- Optional: Log update for record-keeping
INSERT INTO notes (
    content,
    note_type,             -- 'lifecycle'
    reference_type,        -- 'directive'
    reference_name,        -- directive name
    reference_id,          -- directive_id
    severity,              -- 'info'
    metadata_json          -- Additional context as JSON
) VALUES (
    'Directive updated due to source file change',
    'lifecycle',
    'directive',
    ?,
    'info',
    '{"reason": "source_file_changed", "fields_modified": ["trigger_config", "action_config"]}'
);
```

---

### Branches

#### Branch 1: Minor Change (No Re-Validation Needed)
- **Condition**: Only description or non-functional metadata changed
- **Action**:
  1. Update metadata only
  2. Skip re-validation
  3. Keep approved status if was approved
  4. No re-implementation needed

#### Branch 2: Configuration Change (Re-Validation Required)
- **Condition**: Trigger time, action parameters, conditions changed
- **Action**:
  1. Deactivate if active
  2. Reset approved = false
  3. Re-validate with Q&A
  4. Re-implement
  5. Require re-approval

#### Branch 3: Major Structural Change
- **Condition**: Trigger type or action type changed completely
- **Action**:
  1. Deactivate if active
  2. Reset approved = false
  3. Full re-parse, re-validate, re-implement
  4. Essentially treat as new directive

#### Branch 4: Multiple Directives in File Changed
- **Condition**: YAML file contains multiple directives, some changed
- **Action**:
  1. Process each changed directive independently
  2. Show summary of all changes
  3. Allow batch update or individual confirmation

#### Branch 5: File Deleted
- **Condition**: Source file no longer exists
- **Action**:
  1. Warn user
  2. Deactivate all directives from that file
  3. Mark status as 'source_missing'
  4. Don't delete from database (preserve for recovery)

---

### Fallback

If update fails:
1. Keep directive in previous state
2. Don't deactivate if was active (unless unsafe)
3. Log error
4. Prompt user for manual intervention

---

## Examples

### ✅ Example 1: Time Change

**User modifies file**:
```yaml
# directives/lights.yaml
# Changed: time from 17:00 to 18:00
directives:
  - name: turn_off_lights_5pm
    trigger:
      type: time
      time: "18:00"  # ← Changed from 17:00
```

**AI Response**:
```
🔄 Directive Update Detected

File: directives/lights.yaml
Directive: turn_off_lights_5pm
Current status: Active

Changes:
- trigger.time: '17:00' → '18:00'

This directive is currently active.
I need to:
1. Deactivate it
2. Re-validate the change
3. Re-implement with new time
4. Require your approval before reactivating

Process update? (yes/no)

User: yes

AI: Deactivating turn_off_lights_5pm...
    ✓ Deactivated

    Re-validating...
    ✓ Time change validated: 6:00 PM EST

    Re-implementing...
    ✓ Updated trigger logic
    ✓ Tests updated

    ⚠️  Please retest with new 6pm trigger.
    When ready: 'approve directive turn_off_lights_5pm'
```

---

### ✅ Example 2: Action Parameter Change

**User modifies**:
```yaml
action:
  api: homeassistant
  params:
    entity_id: group.all_lights  # ← Changed from group.living_room_lights
```

**AI Response**:
```
🔄 Update Detected: turn_off_lights_5pm

Changes:
- action.params.entity_id: 'group.living_room_lights' → 'group.all_lights'

This will now turn off ALL lights instead of just living room.

Confirm this change? (yes/no/cancel)

User: yes

AI: Re-validating action...
    Q: This will control all lights in your home. Correct?

User: yes

AI: Re-implementing...
    ✓ Updated action to target all lights
    ✓ Tests updated for new entity

    Please retest before approving!
```

---

### ⚠️ Example 3: Breaking Change

**User changes trigger type**:
```yaml
trigger:
  type: event  # ← Changed from 'time'
  event_source: webhook
```

**AI Response**:
```
🔄 Major Update Detected: turn_off_lights_5pm

⚠️  Breaking change: Trigger type changed from 'time' to 'event'

This is a significant structural change. The directive will be
essentially re-created with a new implementation.

Current status: Active → Will be deactivated

Process as new directive? (yes/no)

User: yes

AI: Deactivating old implementation...
    ✓ Deactivated

    Re-parsing as new directive type...
    Re-validating (full Q&A needed)...

    Q1: What webhook endpoint should trigger this?
    Q2: What event type?
    ...
```

---

### ❌ Example 4: File Deleted

**User deletes source file**:

**AI Detection**:
```
⚠️  Source File Missing: directives/lights.yaml

Affected directives:
- turn_off_lights_5pm (Status: Active)
- monitor_stove (Status: Active)

These directives are now missing their source definitions.

Actions taken:
✓ Deactivated all directives from this file
✓ Status changed to 'source_missing'

Options:
a) Restore file from backup
b) Mark directives as deleted
c) Keep in database (can restore later)

What would you like to do?
```

---

## Edge Cases

### 1. **Update While Directive Executing**
- **Scenario**: Update detected while directive is mid-execution
- **Handling**:
  - Let current execution complete
  - Then deactivate
  - Process update

### 2. **Conflicting Updates**
- **Scenario**: User updates file while AI is processing previous update
- **Handling**:
  - Detect conflict
  - Abort first update
  - Process latest version

### 3. **Partial File Corruption**
- **Scenario**: User saves invalid YAML mid-edit
- **Handling**:
  - Detect parse error
  - Keep previous version active
  - Alert user about invalid syntax

### 4. **Rollback Request**
- **Scenario**: User wants to undo update
- **Handling**:
  - Restore from backup
  - Re-validate old version
  - Re-implement
  - Reactivate if was active

### 5. **Update During Testing Period**
- **Scenario**: User updating while testing previous implementation
- **Handling**:
  - Alert: "Testing in progress, update will reset"
  - Confirm before proceeding
  - Reset to start of testing

---

## Related Directives

### Loops Back To
```
user_directive_update (YOU ARE HERE)
   ↓
user_directive_parse (re-parse)
   ↓
user_directive_validate (re-validate)
   ↓
user_directive_implement (re-implement)
   ↓
user_directive_approve (require re-approval)
```

### Calls
- **user_directive_deactivate**: If directive is active
- **user_directive_parse**: To re-parse source file
- **user_directive_validate**: To re-validate changes
- **user_directive_implement**: To re-generate code

### Triggered By
- File system watcher (automatic)
- User command (manual)

---

## Helper Functions

Query `get_helpers_for_directive('user_directive_update')` to discover this
directive's helpers, and `get_helper_by_name` for a signature. They are not
listed here on purpose: the tool surface evolves, and a hardcoded list in a
rarely-read file goes stale silently and is believed anyway.

---

## Database Operations

**Primary Method**: Use helper functions for all database operations. Query available helpers for user_directives.db operations.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

### Tables Read
Load source files with checksums joined with user_directives to check for changes

### Tables Updated

#### source_files
**Use helpers** to update source file checksums.
Fields: checksum, last_modified_at (timestamp), last_parsed_at (timestamp)

#### user_directives
**Use helpers** to reset directive for update.
Fields: approved=false, status='in_progress', updated_at (timestamp), version (increment)

#### directive_updates (tracking history)
**Use helpers** to insert update history.
Fields: directive_id, old_config_json, new_config_json, changes_summary, updated_by, updated_at

---

## Testing

### Test 1: Detect and Process Time Change
```
User modifies trigger time in YAML
Expected: Detected, deactivated, re-validated, re-implemented
Verify: New time in implementation, requires re-approval
```

### Test 2: Handle File Deletion
```
User deletes source file
Expected: Affected directives deactivated, status = 'source_missing'
Verify: No crashes, recovery options offered
```

### Test 3: Concurrent Update
```
File modified twice in quick succession
Expected: Second update supersedes first
Verify: Final implementation matches latest file state
```

### Test 4: Rollback After Failed Update
```
Update fails during re-implementation
Expected: Directive stays in previous working state
Verify: If was active, stays active (fallback)
```

---

## Common Mistakes

### ❌ Mistake 1: Not Deactivating Active Directive
**Wrong**: Update while directive running
**Right**: Deactivate first, then update

### ❌ Mistake 2: Not Resetting Approval
**Wrong**: Keep approved = true after changes
**Right**: Reset to false, require re-approval

### ❌ Mistake 3: Auto-Reactivating After Update
**Wrong**: Automatically reactivate after re-implementation
**Right**: Require user testing and approval first

### ❌ Mistake 4: Not Preserving History
**Wrong**: Overwrite old config completely
**Right**: Store update history for rollback

### ❌ Mistake 5: Ignoring Breaking Changes
**Wrong**: Treat all updates the same
**Right**: Detect breaking changes, require full re-validation

---

## References

- [Loops to: user_directive_parse](./user_directive_parse.md)
- [Calls: user_directive_deactivate](./user_directive_deactivate.md)

---

## Notes

**Critical Loop**: This directive creates the update loop that allows iterative directive development.

**Approval Reset**: Always reset approved = false when config changes. User must retest.

**Status Management**: If last active directive updated, project.user_directives_status returns to 'in_progress'.

**Version Tracking**: Increment directive version on each update for history.
