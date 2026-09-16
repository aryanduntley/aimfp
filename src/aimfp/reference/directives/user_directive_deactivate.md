# Directive: user_directive_deactivate

**Type**: User System
**Level**: N/A
**Parent Directive**: Multiple (user request, update, error handling)
**Priority**: High

---

## Purpose

Stop execution of active user directives and clean up deployed resources. This is the opposite of `user_directive_activate` and ensures clean shutdown of automation services.

**Responsibilities**:
1. Stop scheduler jobs, event listeners, or background services
2. Finalize and flush execution logs
3. Update directive and project status
4. Clean up resources (connections, file handles, etc.)
5. Preserve directive state for potential reactivation

**Critical**: Deactivation is **non-destructive** - implementation files and configuration are preserved.

---

## When to Apply

This directive applies when:
- User says: "Deactivate directive {name}"
- User says: "Stop {directive_name}"
- **Automatic**: Before `user_directive_update` (if directive is active)
- **Automatic**: High error rate detected by `user_directive_monitor`
- User says: "Pause all directives"
- System shutdown or maintenance

---

## Workflow

### Trunk: Stop and Clean Up Directive

#### Step 1: Verify Directive is Active

1. **Check current status**:
   **Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

2. **If not active**:
   ```
   AI: "Directive '{name}' is not currently active.

   Current status: {status}

   Options:
   a) View directive status
   b) Delete directive completely
   c) Nothing to do"
   ```

#### Step 2: Stop Execution Based on Type

**Branch by trigger_type**:

##### 2a. Stop Time-Based Scheduler
```python
if trigger_type == 'time':
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = get_scheduler_instance()

    # Get job ID
    job_id = f"directive_{directive_id}"

    # Remove job
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        AI: "✓ Scheduler job removed"
    else:
        AI: "⚠️  Scheduler job not found (may have been removed already)"
```

##### 2b. Stop Event Listener
```python
if trigger_type == 'event':
    # Stop event listener service
    listener = get_listener_instance(directive_id)

    if listener:
        listener.stop()
        listener.cleanup()
        AI: "✓ Event listener stopped"
```

##### 2c. Stop Background Service
```python
if trigger_type == 'condition':
    # Terminate background process
    process_id = directive.process_id

    if process_exists(process_id):
        terminate_process(process_id)
        wait_for_termination(process_id, timeout=10)

        if process_exists(process_id):
            # Force kill if still running
            kill_process(process_id)
            AI: "✓ Background service stopped (forced)"
        else:
            AI: "✓ Background service stopped gracefully"
    else:
        AI: "⚠️  Background service not running"
```

#### Step 3: Finalize Logs

1. **Flush execution log**:
   ```python
   # Ensure all buffered log entries are written
   flush_log(".aimfp-project/logs/executions/current.log")
   ```

2. **Write final entry**:
   ```python
   final_entry = {
       "timestamp": datetime.utcnow().isoformat(),
       "directive_id": directive_id,
       "directive_name": directive_name,
       "event": "deactivated",
       "reason": reason  # user_request, update, error_threshold, etc.
   }

   append_to_log(
       ".aimfp-project/logs/executions/current.log",
       json.dumps(final_entry)
   )
   ```

3. **Rotate logs if needed**:
   ```python
   # Archive current session
   archive_log(
       ".aimfp-project/logs/executions/current.log",
       f".aimfp-project/logs/executions/archived_{timestamp}.log"
   )
   ```

4. **Write to lifecycle log**:
   ```
   lifecycle_log('.aimfp-project/logs/user-directives.log'):
   [2025-10-28 22:30:00] INFO: Deactivated '{directive_name}' (reason: {reason})
   ```

#### Step 4: Update Database Status

1. **Update directive status**:
   **Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

2. **Update execution tracking**:
   **Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

3. **Update deployment info**:
   **Use helper functions** for database operations. Query available helpers for the appropriate database.

#### Step 5: Update Project Status

**If this was the last active directive**:
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

#### Step 6: Clean Up Resources

1. **Close connections**:
   ```python
   # If directive had open API connections
   if hasconnections(directive_id):
       close_all_connections(directive_id)
   ```

2. **Remove temp files** (if any):
   ```python
   temp_dir = f"/tmp/aimfp_directive_{directive_id}"
   if os.path.exists(temp_dir):
       shutil.rmtree(temp_dir)
   ```

3. **Release locks** (if any):
   ```python
   release_directive_locks(directive_id)
   ```

#### Step 7: Confirmation

```
AI: "✅ Directive '{name}' deactivated

Summary:
  - Execution stopped: {scheduler|event_listener|background_service}
  - Final statistics recorded
  - Logs finalized and archived
  - Resources cleaned up

Status: Paused (can be reactivated later)

Deactivation stats:
  - Total uptime: {duration}
  - Total executions: {count}
  - Success rate: {percentage}%

Options:
a) Reactivate later: 'activate directive {name}'
b) Modify and update: Edit source file and say 'update directive {name}'
c) Delete completely: 'delete directive {name}'"
```

---

### Branches

#### Branch 1: Deactivate for Update
- **Condition**: Called by `user_directive_update`
- **Action**:
  1. Quick deactivation
  2. Minimal logging
  3. Status = 'updating'
  4. Expect reactivation soon

#### Branch 2: Deactivate for Error Threshold
- **Condition**: Called by `user_directive_monitor` due to high errors
- **Action**:
  1. Deactivate immediately
  2. Log reason = 'error_threshold'
  3. Alert user with error summary
  4. Status = 'error'
  5. Recommend investigation before reactivation

#### Branch 3: Deactivate All
- **Condition**: User says "deactivate all" or system shutdown
- **Action**:
  1. Deactivate each directive sequentially
  2. Show progress
  3. Update project status to 'disabled'
  4. Preserve all for bulk reactivation

#### Branch 4: Force Deactivate
- **Condition**: Process not responding or stuck
- **Action**:
  1. Attempt graceful shutdown (10s timeout)
  2. If fails, force kill process
  3. Log forced termination
  4. Clean up as much as possible
  5. Warn user about potential inconsistency

#### Branch 5: Deactivate Non-Existent Process
- **Condition**: Process already dead (crashed)
- **Action**:
  1. Log that process was already stopped
  2. Update status anyway
  3. Clean up orphaned resources
  4. Report crash to user

---

### Fallback

If deactivation fails:
1. Log failure details
2. Mark status as 'deactivation_failed'
3. Alert user with specific issue
4. Offer manual intervention steps
5. Don't leave in inconsistent state

---

## Examples

### ✅ Example 1: Normal Deactivation

```
User: "Deactivate turn_off_lights_5pm"

AI: Stopping directive 'turn_off_lights_5pm'...

    ✓ Removed scheduler job (job ID: directive_1)
    ✓ Finalized execution logs
    ✓ Updated statistics

    ✅ Directive deactivated

    Statistics:
      - Active for: 7 days 3 hours
      - Total executions: 25
      - Success rate: 100%
      - Last execution: 2 hours ago (success)

    Status: Paused

    Reactivate anytime: 'activate directive turn_off_lights_5pm'
```

---

### ✅ Example 2: Deactivate for Update

```
[Automatic during update process]

AI: Deactivating 'turn_off_lights_5pm' for update...
    ✓ Scheduler job removed
    ✓ Status: Updating

    Proceeding with re-validation...
```

---

### ⚠️ Example 3: Deactivate Due to Errors

```
[Automatic by monitor]

AI: 🚨 Auto-deactivating 'scale_ec2_high_cpu'

    Reason: High error rate (100% in last 15 executions)
    Error: "NoCredentialsError: Unable to locate AWS credentials"

    ✓ Background service stopped (PID: 12345)
    ✓ Status: Error

    This directive is non-functional and has been deactivated.

    To reactivate:
    1. Fix AWS credentials issue
    2. Test manually
    3. Reactivate: 'activate directive scale_ec2_high_cpu'

    Would you like help fixing the credentials issue?
```

---

### ✅ Example 4: Deactivate All

```
User: "Deactivate all directives"

AI: Deactivating all active directives...

    [1/3] turn_off_lights_5pm... ✓ Stopped
    [2/3] monitor_stove... ✓ Stopped
    [3/3] backup_database... ✓ Stopped

    ✅ All directives deactivated

    Project status: disabled

    Summary:
      - 3 directives paused
      - All can be reactivated individually or together
      - Implementations preserved

    Reactivate all: 'activate all directives'
```

---

### ⚠️ Example 5: Force Stop Stuck Process

```
User: "Deactivate webhook_listener"

AI: Stopping 'webhook_listener'...

    Attempting graceful shutdown...
    ⚠️  Process not responding (timeout after 10s)

    Force terminating process 12347...
    ✓ Process killed

    Cleaning up resources...
    ✓ Webhook port released
    ✓ Temp files removed

    ⚠️  Directive force-stopped

    Status: Inactive

    Note: Process was unresponsive and had to be killed.
    Check logs for potential issues before reactivating.
```

---

## Edge Cases

### 1. **Process Already Dead**
- **Scenario**: Background service crashed before deactivation
- **Handling**:
  - Detect process doesn't exist
  - Update status anyway
  - Log as "already stopped"
  - Clean up orphaned resources

### 2. **Deactivation During Execution**
- **Scenario**: Directive executing when deactivate called
- **Handling**:
  - Wait for current execution to complete (timeout 30s)
  - Then deactivate
  - If timeout, force stop

### 3. **Multiple Deactivate Requests**
- **Scenario**: User calls deactivate twice quickly
- **Handling**:
  - First request processes
  - Second request detects already inactive
  - Report "already deactivated"

### 4. **Deactivate Non-Existent Directive**
- **Scenario**: User tries to deactivate directive that doesn't exist
- **Handling**:
  - Report: "Directive '{name}' not found"
  - List available directives

### 5. **Resource Cleanup Fails**
- **Scenario**: Can't close connections or delete temp files
- **Handling**:
  - Log warning
  - Mark deactivation as successful anyway
  - Note cleanup issues for manual resolution

---

## Related Directives

### Opposite Of
- **user_directive_activate**: This directive reverses activation

### Called By
- **user_directive_update**: Deactivates before updating
- **user_directive_monitor**: Deactivates on high error rate
- **User command**: Direct user request

### Calls
- **project_update_db**: Update project status if last directive
- **project_notes_log**: Log deactivation event

---

## Helper Functions

Query `get_helpers_for_directive('user_directive_deactivate')` to discover this
directive's helpers, and `get_helper_by_name` for a signature. They are not
listed here on purpose: the tool surface evolves, and a hardcoded list in a
rarely-read file goes stale silently and is believed anyway.

Deactivation does not stop anything. It sets status to `paused`; stopping
the runner is the user's action on their own scheduler, and worth stating
explicitly so a directive is not assumed dormant while it is still firing.

---

## Database Operations

### Tables Read
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

### Tables Updated

#### user_directives
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

#### directive_executions
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

#### deployment_info
**Use helper functions** for database operations. Query available helpers for the appropriate database.

#### project
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

---

## Testing

### Test 1: Deactivate Active Scheduler
```
Directive with time trigger active
Expected: Scheduler job removed, status updated
Verify: Job not in scheduler, logs finalized
```

### Test 2: Deactivate Background Service
```
Directive with condition trigger active (PID exists)
Expected: Process terminated gracefully
Verify: Process no longer exists, status = 'paused'
```

### Test 3: Force Stop Unresponsive Process
```
Process not responding to termination
Expected: Force kill after timeout
Verify: Process killed, cleanup completed
```

### Test 4: Deactivate All
```
3 active directives
Expected: All deactivated, project status = 'disabled'
Verify: All status = 'paused', can be reactivated
```

### Test 5: Deactivate Already Inactive
```
User tries to deactivate inactive directive
Expected: Report already inactive, no errors
Verify: Status unchanged, no side effects
```

---

## Common Mistakes

### ❌ Mistake 1: Not Waiting for Current Execution
**Wrong**: Kill process immediately while executing
**Right**: Wait for current execution to complete (with timeout)

### ❌ Mistake 2: Not Flushing Logs
**Wrong**: Stop without finalizing logs
**Right**: Flush and archive logs before stopping

### ❌ Mistake 3: Not Cleaning Up Resources
**Wrong**: Leave connections open, temp files around
**Right**: Clean up all resources

### ❌ Mistake 4: Not Updating Project Status
**Wrong**: Only update directive status
**Right**: Update project.user_directives_status if last directive

### ❌ Mistake 5: Destructive Deactivation
**Wrong**: Delete implementation files on deactivate
**Right**: Preserve everything for reactivation

---

## References

- [Opposite: user_directive_activate](./user_directive_activate.md)
- [Called by: user_directive_update](./user_directive_update.md)
- [Called by: user_directive_monitor](./user_directive_monitor.md)

---

## Notes

**Non-Destructive**: Deactivation preserves all implementation and configuration.

**Clean Shutdown**: Always attempt graceful shutdown before force kill.

**Status Preservation**: Keep execution statistics for historical analysis.

**Reactivation Ready**: Deactivated directives can be quickly reactivated.
