# Directive: user_directive_approve

**Type**: User System
**Level**: 4
**Parent Directive**: `user_directive_implement`
**Priority**: Critical

---

## Purpose

Manage the user testing and approval workflow for implemented directives. This directive serves as the **approval gate** between implementation and activation.

**Critical principle**: User directives must be tested and explicitly approved by the user before activation. Implementation can take days or weeks of testing before the user feels confident to approve.

**Responsibilities**:
1. Prompt user for testing completion status
2. Collect testing feedback
3. Handle approval, rejection, or modification requests
4. Update directive status and project state
5. Gate activation (directives cannot activate without approval)

---

## When to Apply

This directive applies when:
- User says: "Approve directive {name}"
- User says: "Testing complete" / "Ready to activate"
- User says: "I've tested {directive}, it works"
- AI asks: "Have you tested the implementation?" and user confirms
- Detected trigger: User has been testing for a period and indicates readiness

**Context**: This directive ONLY runs after `user_directive_implement` completes. It's the gate before `user_directive_activate`.

---

## Workflow

### Trunk: User Testing and Approval Process

#### Step 1: Verify Implementation Status

1. **Check directive exists and is implemented**:
   ```sql
   SELECT id, name, implementation_status, approved, status
   FROM user_directives
   WHERE name = ?
     AND implementation_status = 'implemented';
   ```

2. **If not implemented**:
   ```
   AI: "Directive '{name}' is not yet implemented.
        Current status: {status}
        Would you like me to implement it first?"
   ```

3. **If already approved**:
   ```
   AI: "Directive '{name}' is already approved.
        Status: {active|inactive}
        Would you like to retest or modify it?"
   ```

#### Step 2: Confirm Testing Completion

1. **Prompt user about testing**:
   ```
   AI: "Have you tested the '{directive_name}' implementation?

   Testing checklist:
   ✓ Ran automated tests (pytest)
   ✓ Tested manually with real triggers
   ✓ Verified expected behavior
   ✓ Checked edge cases
   ✓ Confirmed no errors or unexpected results

   Have you completed testing? (yes/no/partially)"
   ```

2. **Handle responses**:
   - **"yes"**: Proceed to approval confirmation
   - **"no"**: Offer guidance, keep status as 'in_progress'
   - **"partially"**: Ask what's incomplete, offer help

#### Step 3: Collect Feedback

1. **Ask about testing results**:
   ```
   AI: "How did the testing go?

   Options:
   a) Works perfectly - ready to approve
   b) Works but needs minor adjustments
   c) Has issues - needs fixes
   d) Doesn't match my expectations - rework needed"
   ```

2. **For each response**:

   **a) Works perfectly**:
   ```
   AI: "Excellent! I'll mark it as approved.
        Activate now or later?"

   Actions:
   - Set approved = true
   - Log approval to notes
   - Prompt for activation
   ```

   **b) Minor adjustments**:
   ```
   AI: "What adjustments are needed?
        (e.g., change time, modify threshold, adjust parameters)"

   Actions:
   - Collect specific changes
   - Apply changes to validated_config
   - Re-implement with changes
   - Return to testing phase
   - Status stays 'in_progress'
   ```

   **c) Has issues**:
   ```
   AI: "What issues did you encounter?
        (Please describe the problem and expected behavior)"

   Actions:
   - Log issues to notes
   - Analyze issue (code bug vs config issue)
   - Fix implementation or re-validate
   - Re-implement
   - Return to testing phase
   ```

   **d) Rework needed**:
   ```
   AI: "What should change?
        Would you like to:
        1. Re-validate the directive (change requirements)
        2. Try a different approach
        3. Cancel this directive"

   Actions:
   - Handle based on user choice
   - May return to validation or implementation
   - Status stays 'in_progress'
   ```

#### Step 4: Approval Confirmation

1. **Final confirmation** (if user approved):
   ```
   AI: "Confirm approval of '{directive_name}'?

   This will:
   ✓ Mark directive as approved
   ✓ Allow activation for real-time execution
   ✓ Lock configuration (changes require re-approval)

   Confirm approval? (yes/no)"
   ```

2. **On "yes"**:
   ```sql
   UPDATE user_directives
   SET approved = true,
       approved_at = CURRENT_TIMESTAMP,
       approved_by = 'user'  -- Could be username if tracked
   WHERE id = ?;
   ```

3. **Log approval**:
   ```sql
   INSERT INTO notes (
       content,
       note_type,
       reference_table,
       reference_id,
       source,
       directive_name,
       severity
   ) VALUES (
       'User approved directive after testing',
       'approval',
       'user_directives',
       ?,
       'user',
       'user_directive_approve',
       'info'
   );
   ```

#### Step 5: Activation Prompt

1. **After approval, prompt for activation**:
   ```
   AI: "✅ Directive '{name}' approved!

   Would you like to activate it now? (yes/no)

   If 'yes': Directive will start executing immediately
   If 'no': Directive stays approved but inactive
            (activate later with 'activate directive {name}')"
   ```

2. **Route based on response**:
   - **"yes"**: Call `user_directive_activate`
   - **"no"**: Keep approved but inactive, confirm status

---

### Branches

#### Branch 1: If Testing Incomplete
- **Condition**: User hasn't finished testing
- **Action**:
  1. Offer testing guidance
  2. Provide testing commands/scripts
  3. Encourage thorough testing
  4. Keep status as 'in_progress'
  5. Wait for user to return

#### Branch 2: If Issues Found
- **Condition**: User reports problems
- **Action**:
  1. Collect detailed issue description
  2. Analyze: code bug vs config problem
  3. Fix and re-implement
  4. Prompt for re-testing
  5. Status stays 'in_progress'

#### Branch 3: If Minor Adjustments Needed
- **Condition**: Works but needs tweaks
- **Action**:
  1. Collect specific changes
  2. Update validated_config
  3. Re-implement quickly
  4. Prompt for quick retest
  5. Fast-track to approval if user confirms

#### Branch 4: If User Wants to Modify Directive
- **Condition**: Requirements changed
- **Action**:
  1. Return to `user_directive_validate`
  2. Re-validate with new requirements
  3. Re-implement
  4. Return to testing phase
  5. Set approved = false

#### Branch 5: If User Cancels Directive
- **Condition**: User doesn't want this directive anymore
- **Action**:
  1. Confirm cancellation
  2. Update status to 'cancelled'
  3. Optionally delete implementation files
  4. Log cancellation

---

### Fallback

If approval process interrupted:
1. Keep status as 'in_progress'
2. Keep approved = false
3. Log interruption
4. User can return later with "approve directive {name}"

---

## Examples

### ✅ Example 1: Smooth Approval

```
User: "Approve directive turn_off_lights_5pm"

AI: "Have you tested 'turn_off_lights_5pm'?

Testing checklist:
✓ Ran automated tests
✓ Tested manually
✓ Verified behavior
✓ Checked edge cases

Completed testing? (yes/no)"

User: "yes"

AI: "How did testing go?
a) Works perfectly
b) Needs minor adjustments
c) Has issues
d) Rework needed"

User: "a"

AI: "Excellent! Confirm approval of 'turn_off_lights_5pm'?

This will:
✓ Mark as approved
✓ Allow activation
✓ Lock configuration

Confirm? (yes/no)"

User: "yes"

AI: "✅ Directive approved!

Would you like to activate now? (yes/no)"

User: "yes"

AI: [Calls user_directive_activate]
    "🚀 Activating turn_off_lights_5pm..."
```

---

### ⚠️ Example 2: Issues Found

```
User: "Approve lights directive"

AI: "Have you tested 'turn_off_lights_5pm'?"

User: "yes"

AI: "How did testing go?"

User: "c - has issues"

AI: "What issues did you encounter?"

User: "The lights turn off but it's using the wrong time zone.
      It triggers at 5pm UTC instead of 5pm EST."

AI: "I see the issue. The timezone configuration is incorrect.

Let me fix this:
1. Update timezone to America/New_York
2. Re-implement with correct timezone handling
3. Regenerate code

Proceeding with fix..."

[AI re-implements]

AI: "✅ Fixed and re-implemented.

Please retest to verify the timezone is now correct.
Say 'approve directive turn_off_lights_5pm' when ready."

[Status stays 'in_progress', approved stays false]
```

---

### ⚠️ Example 3: Minor Adjustments

```
User: "Approve stove monitor"

AI: "Have you tested 'monitor_stove'?"

User: "yes, works but needs adjustments"

AI: "What adjustments?"

User: "Change the timeout from 20 minutes to 15 minutes"

AI: "Understood. Updating:
- Timeout: 20 minutes → 15 minutes

Re-implementing with new timeout..."

[AI updates validated_config and re-implements]

AI: "✅ Updated and re-implemented.

Please do a quick retest with the new 15-minute timeout.
When satisfied, say 'approve directive monitor_stove'"

[Status stays 'in_progress']
```

---

### ❌ Example 4: Rework Needed

```
User: "I've tested the backup directive"

AI: "How did testing go?"

User: "d - doesn't match expectations. I want it to backup to
      S3 instead of local disk."

AI: "That's a significant change. Options:

1. Re-validate directive (change backup location to S3)
2. Create new directive for S3 backup
3. Cancel this directive

What would you prefer?"

User: "1 - re-validate"

AI: [Calls user_directive_validate]
    "Re-validating 'backup_database'...

Q: Where should backups be stored?
   Current: /backups/database/
   Change to: ?"

User: "S3 bucket: my-backups/database/"

[Validation and re-implementation follow]
```

---

## Edge Cases

### 1. **Long Testing Period**
- **Scenario**: User tests for weeks before approving
- **Handling**:
  - No time limit on testing
  - Status stays 'in_progress' indefinitely
  - User can test as long as needed
  - AI patient, no pressure to approve

### 2. **Directive Modified During Testing**
- **Scenario**: User modifies source file while testing
- **Handling**:
  - Detect file change (checksum)
  - Warn: "Source file changed - implementation outdated"
  - Offer to re-parse and re-implement
  - Reset approved = false if changes detected

### 3. **Multiple Directives to Approve**
- **Scenario**: User has 5 directives all in testing
- **Handling**:
  - Approve one at a time
  - Allow batch approval: "approve all directives"
  - Show status of each during batch approval

### 4. **Approval Without Testing**
- **Scenario**: User tries to approve without testing
- **Handling**:
  - Warn: "Are you sure? Testing is recommended"
  - Require explicit confirmation: "approve without testing"
  - Log decision

### 5. **Directive Already Active**
- **Scenario**: User tries to approve already active directive
- **Handling**:
  - Report: "Already approved and active"
  - Offer options:
    - View status
    - Deactivate and modify
    - Continue as is

### 6. **Approval by Different User**
- **Scenario**: Multi-user project, different user approves
- **Handling**:
  - Track who approved (if user tracking enabled)
  - Log approver identity
  - No restriction (any user can approve)

---

## Related Directives

### Pipeline Position
```
user_directive_parse
   ↓
user_directive_validate
   ↓
user_directive_implement
   ↓
user_directive_approve (YOU ARE HERE)
   ↓
user_directive_activate
```

### Receives From
- **user_directive_implement**: Provides implemented directive awaiting approval

### Triggers After Completion
- **user_directive_activate**: If user chooses to activate after approval
  - Only possible after approved = true

### Depends On
- **user_directive_implement**: Must run first to create implementation

### Works With
- **user_directive_validate**: May loop back for re-validation
- **user_directive_implement**: May loop back for re-implementation
- **project_notes_log**: Logs approval decisions

### Cannot Proceed Without
- **Implementation complete**: Cannot approve non-implemented directive
- **User confirmation**: Cannot auto-approve without explicit user consent

### Blocks
- **user_directive_activate**: Activation blocked if approved = false

---

## Helper Functions

Query `get_helpers_for_directive('user_directive_approve')` to discover this
directive's helpers, and `get_helper_by_name` for a signature. They are not
listed here on purpose: the tool surface evolves, and a hardcoded list in a
rarely-read file goes stale silently and is believed anyway.

---

## Database Operations

**Primary Method**: Use helper functions for all database operations. Query available helpers for user_directives.db operations.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

### Tables Read
Load directive by name to check: id, name, implementation_status, approved, status

### Tables Updated

#### user_directives
**Use helpers** to update approval status.
- On approval: Set approved=true, approved_at (timestamp), approved_by (optional)
- On rejection/rework: Set approved=false, feedback_json

#### notes (optional AI record-keeping)
**Use helpers for notes operations** - query available note helpers for user_directives.db.

**Note**: The notes table in user_directives.db is for flexible AI record-keeping. Useful for tracking:
- User feedback during approval testing
- Issues found during testing
- User preferences or customization requests
- Approval/rejection decisions and reasoning

---

## Testing

### Test 1: Smooth Approval Flow
```
1. Directive implemented
2. User tested
3. User approves
4. Status: approved = true
5. Prompt for activation
```

### Test 2: Issues Found, Fix and Retry
```
1. User reports issues
2. AI fixes implementation
3. User retests
4. User approves on second attempt
5. Status: approved = true
```

### Test 3: Minor Adjustments
```
1. User requests tweaks
2. AI adjusts and re-implements
3. User confirms with quick retest
4. User approves
```

### Test 4: Approval Without Testing (Warning)
```
1. User tries to approve immediately
2. AI warns about testing
3. User confirms skip testing
4. AI approves with warning logged
```

### Test 5: Cancellation During Approval
```
1. User decides not to use directive
2. Cancels during approval
3. Status: cancelled
4. Implementation preserved for future
```

---

## Common Mistakes

### ❌ Mistake 1: Auto-Approving Without User Confirmation
**Wrong**: Set approved = true after implementation
**Right**: Wait for explicit user approval

### ❌ Mistake 2: Skipping Testing Prompt
**Wrong**: Assume user tested
**Right**: Always ask about testing completion

### ❌ Mistake 3: Not Handling Feedback
**Wrong**: Only accept "yes" or "no"
**Right**: Handle adjustments, issues, rework requests

### ❌ Mistake 4: Activating Without Approval
**Wrong**: Allow activation with approved = false
**Right**: Block activation until approved = true

### ❌ Mistake 5: Forcing Quick Approval
**Wrong**: Pressure user to approve quickly
**Right**: Allow unlimited testing time

---

## References

- [Previous: user_directive_implement](./user_directive_implement.md)
- [Next: user_directive_activate](./user_directive_activate.md)
- [Helper Functions Reference](../helper-functions-reference.md#approval-helpers)

---

## Notes

**Critical Gate**: This directive is the gate between implementation and activation. Never bypass it.

**User Control**: The user is in complete control of the approval process. AI facilitates, user decides.

**Testing Period**: Can be minutes, days, or weeks. No time pressure.

**Status Management**: Status stays 'in_progress' until activation (after approval).
