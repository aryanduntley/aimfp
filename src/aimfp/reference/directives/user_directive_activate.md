# Directive: user_directive_activate

**Type**: User System
**Level**: 5
**Parent Directive**: `user_directive_approve`
**Priority**: Critical

---

## Purpose

Deploy and activate approved user directives for real-time execution. This directive transforms tested, approved implementation code into running automation services.

**Critical**: This directive ONLY runs after `user_directive_approve` sets `approved = true`. Cannot activate unapproved directives.

**Responsibilities**:
1. Verify approval status (gate: approved must be true)
2. Deploy implementation (schedulers, services, event handlers)
3. Initialize execution tracking and logging
4. Change project status from 'in_progress' to 'active'
5. Start real-time monitoring

---

## When to Apply

This directive applies when:
- User says: "Activate directive {name}"
- User says: "Start {directive_name}"
- **After approval**: User chooses "yes" to "Activate now?"
- User says: "Activate all directives"
- Detected trigger: Approved directive exists with status != 'active'

**Critical requirement**: `approved = true` in user_directives table

---

## Workflow

### Trunk: Deploy and Activate Directive

#### Step 1: Verify Approval Gate

1. **Check approval status**:
   **Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

2. **Gate check**:
   ```python
   if not directive.approved:
       return Error("Cannot activate unapproved directive. Please approve first.")

   if directive.implementation_status != 'implemented':
       return Error("Cannot activate directive without implementation.")
   ```

3. **If gate fails**:
   ```
   AI: "❌ Cannot activate '{name}'

   Reason: {not_approved | not_implemented}

   Required steps:
   {approval_steps | implementation_steps}

   Would you like me to help with this?"
   ```

#### Step 2: Pre-Activation Verification

1. **Check implementation files exist**:
   ```python
   # Query directive_implementations table
   SELECT file_path FROM directive_implementations
   WHERE directive_id = ?;

   # Verify each file exists
   for file_path in implementation_files:
       if not os.path.exists(file_path):
           return Error(f"Implementation file missing: {file_path}")
   ```

2. **Check dependencies installed**:
   **Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

   ```python
   missing_deps = [dep for dep in deps if dep.availability_status != 'available']

   if missing_deps:
       AI: "Missing required dependencies:
            {list missing_deps}

            Install now? (y/n)"

       if user says yes:
           install_dependencies(missing_deps)
       else:
           return Error("Cannot activate without dependencies")
   ```

3. **Verify entry point callable**:
   ```python
   # Import and validate entry point
   try:
       module = import_module(f"src.directives.{directive_name}")
       entry_point = getattr(module, 'run_directive')
   except (ImportError, AttributeError) as e:
       return Error(f"Entry point not found: {e}")
   ```

#### Step 3: Deploy Based on Trigger Type

**Branch by trigger_type**:

##### 3a. Time-Based Trigger (Scheduler)

`trigger_config` for a time trigger is a discriminated union on `kind`. Read
it with the grammar, never by guessing at key names — AIMFP enforces this
shape at insert, so these are the only keys that can be present:

```python
if trigger_type == 'time':
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    config = directive.trigger_config          # already a dict

    if config['kind'] == 'interval':
        job = scheduler.add_job(
            entry_point, 'interval', seconds=config['seconds'],
            id=f"directive_{directive_id}", name=directive_name)
    else:
        hour, minute = config['at'].split(':')
        job = scheduler.add_job(
            entry_point, 'cron',
            hour=int(hour), minute=int(minute),
            timezone=config.get('timezone'),
            day_of_week=','.join(config['weekdays'])
                if config['kind'] == 'weekly' else None,
            day=','.join(str(d) for d in config['days'])
                if config['kind'] == 'monthly' else None,
            id=f"directive_{directive_id}", name=directive_name)

    scheduler.start()

    store_deployment_info(
        directive_id=directive_id,
        deployment_type='scheduler',
        scheduler_job_id=job.id,
        next_run_time=job.next_run_time
    )
```

A scheduler is only one option. cron and systemd timers are equally valid,
and the runner is generated into the user's project either way — AIMFP has
no timer of its own (see `user_directive_implement`).

##### 3b. Event-Based Trigger (Webhook/Listener)

`trigger_config` is `{"event": "...", "source": "..."}`. `source` is
optional; a directive may name only the event it waits for.

```python
if trigger_type == 'event':
    config = directive.trigger_config

    listener = EventListener(
        event_source=config.get('source'),
        event_type=config['event'],
        callback=entry_point
    )
    listener.start()

    store_deployment_info(
        directive_id=directive_id,
        deployment_type='event_listener',
        listener_port=getattr(listener, 'port', None),
        event_source=config.get('source')
    )
```

##### 3c. Condition-Based Trigger (Polling Service)

`trigger_config` is `{"expression": "...", "evaluate_every_seconds": N}`.
AIMFP stores the expression but never evaluates it — deciding whether the
condition holds is the runner's, which is why `is_due` always returns
condition triggers and leaves the judgment to the caller.

```python
if trigger_type == 'condition':
    config = directive.trigger_config

    poller = ConditionPoller(
        check_interval=config.get('evaluate_every_seconds', 60),
        expression=config['expression'],
        condition_func=entry_point,
        directive_id=directive_id
    )
    process = poller.start_background()

    store_deployment_info(
        directive_id=directive_id,
        deployment_type='background_service',
        process_id=process.pid,
        check_interval=config.get('evaluate_every_seconds', 60)
    )
```

#### Step 4: Initialize Execution Tracking

1. **Create execution record**:
   **Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

2. **Calculate next execution**:

   AIMFP owns this arithmetic — do NOT hand-roll it. Hand-rolling is how two
   projects end up disagreeing about when 17:00 is.

   ```python
   from aimfp.hooks.schedule import next_fire_time

   if trigger_type == 'time':
       result = next_fire_time(directive.trigger_config)
       next_run = result.next_fire_time if result.success else None
   ```

   In practice a runner rarely needs this call at all: `record_execution_end`
   already advances `next_scheduled_time` after every run, so a time
   directive that just fired is not due again until it should be.

#### Step 5: Initialize Logging

1. **Set up execution log**:
   ```python
   # Create log handlers
   execution_log_path = '.aimfp-project/logs/executions/'
   os.makedirs(execution_log_path, exist_ok=True)

   execution_logger = setup_logger(
       name=f"execution_{directive_name}",
       log_file=f"{execution_log_path}/current.log",
       format='json',  # JSON Lines format
       rotation='daily',
       retention_days=30
   )
   ```

2. **Set up error log**:
   ```python
   error_log_path = '.aimfp-project/logs/errors/'
   os.makedirs(error_log_path, exist_ok=True)

   error_logger = setup_logger(
       name=f"errors_{directive_name}",
       log_file=f"{error_log_path}/current_errors.log",
       format='json',
       rotation='10MB',  # or daily
       retention_days=90
   )
   ```

3. **Log activation event**:
   ```
   lifecycle_log('.aimfp-project/logs/user-directives.log'):
   [2025-10-28 14:30:00] INFO: Activated '{directive_name}'
   ```

#### Step 6: Update Project Status

**If this is first directive activated**:
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

**Update directive status**:
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

#### Step 7: Confirmation and Monitoring

```
AI: "✅ Directive '{name}' activated!

Deployment:
  Type: {scheduler|event_listener|background_service}
  Status: 🟢 Running
  {Scheduler info: Next run at {time}}
  {Service info: Process ID {pid}}
  {Listener info: Listening on {source}}

Logging:
  Execution log: .aimfp-project/logs/executions/current.log
  Error log: .aimfp-project/logs/errors/current_errors.log
  Lifecycle log: .aimfp-project/logs/user-directives.log

Monitoring:
  I'll track executions and alert on errors.
  Check status anytime: 'user directive status'

Project status changed: in_progress → active

{directive_name} is now running! 🚀"
```

---

### Branches

#### Branch 1: Time-Based Scheduler
- **Condition**: `trigger_type = 'time'`
- **Deploy**: APScheduler cron job
- **Info**: Show next run time

#### Branch 2: Event Listener
- **Condition**: `trigger_type = 'event'`
- **Deploy**: Webhook receiver or event monitor
- **Info**: Show listening source/port

#### Branch 3: Polling Service
- **Condition**: `trigger_type = 'condition'`
- **Deploy**: Background process with polling interval
- **Info**: Show PID and check interval

#### Branch 4: Multiple Directives
- **Condition**: "activate all directives"
- **Action**:
  1. Query all approved, inactive directives
  2. Activate each sequentially
  3. Report results: success/failure for each

#### Branch 5: Already Active
- **Condition**: Directive status already 'active'
- **Action**:
  ```
  AI: "'{name}' is already active.

  Status: 🟢 Running
  {deployment info}

  Options:
  a) View status
  b) Restart
  c) Deactivate
  d) Continue as is"
  ```

#### Branch 6: Activation Failure
- **Condition**: Deployment fails (port conflict, scheduler error, etc.)
- **Action**:
  1. Rollback any partial deployment
  2. Log error
  3. Update status to 'error'
  4. Report specific error to user
  5. Offer troubleshooting

---

### Fallback

If activation fails at any step:
1. Rollback deployment (stop services, unregister jobs)
2. Keep status as previous (not 'active')
3. Log detailed error
4. Report to user with specific issue
5. Offer to retry with fixes

---

## Examples

### ✅ Example 1: Activate Time-Based Directive

```
User: "Activate directive turn_off_lights_5pm"

AI: Checking approval status...
    ✓ Directive approved
    ✓ Implementation exists
    ✓ Dependencies satisfied

    Deploying scheduler...
    ✓ Scheduler registered
    ✓ Job ID: directive_1
    ✓ Next run: Today at 5:00 PM EST

    Initializing logging...
    ✓ Execution log configured
    ✓ Error log configured

    ✅ Directive 'turn_off_lights_5pm' activated!

    Deployment:
      Type: Scheduler (cron)
      Status: 🟢 Running
      Next run: Today at 5:00 PM EST (in 3 hours 45 minutes)

    Logging:
      Execution: .aimfp-project/logs/executions/current.log
      Errors: .aimfp-project/logs/errors/current_errors.log

    Monitoring:
      Active and monitoring for executions
      Will alert on errors

    Project status: active

    turn_off_lights_5pm is now running! 🚀
```

---

### ✅ Example 2: Activate Condition-Based Directive

```
User: "Activate scale_ec2_high_cpu"

AI: Verifying prerequisites...
    ✓ Approved
    ✓ Implemented
    ✓ AWS credentials configured (from env vars)
    ✓ boto3 installed

    Starting background monitoring service...
    ✓ Service started (PID: 12345)
    ✓ Polling CloudWatch every 300 seconds
    ✓ Threshold: CPU > 80% for 5 minutes

    ✅ Directive 'scale_ec2_high_cpu' activated!

    Deployment:
      Type: Background Service (condition polling)
      Status: 🟢 Running
      Process ID: 12345
      Check interval: 300 seconds (5 minutes)
      Next check: In 5 minutes

    Monitoring:
      Service health: OK
      Will scale EC2 when conditions met

    scale_ec2_high_cpu is now monitoring! 🚀
```

---

### ❌ Example 3: Activation Blocked (Not Approved)

```
User: "Activate backup_database"

AI: ❌ Cannot activate 'backup_database'

    Reason: Directive not approved

    Current status:
      - Implemented: ✓ Yes
      - Tested: ? Unknown
      - Approved: ✗ No

    Required steps:
      1. Test the implementation
      2. Approve the directive: 'approve directive backup_database'
      3. Then activate

    Would you like me to help with testing?
```

---

### ⚠️ Example 4: Activation Failure (Port Conflict)

```
User: "Activate webhook_listener"

AI: Checking prerequisites...
    ✓ Approved
    ✓ Implemented

    Starting event listener...
    ❌ Failed to start listener

    Error: Port 8080 already in use by another process

    Troubleshooting options:
    a) Stop the conflicting process
    b) Change webhook port configuration
    c) Use a different port (I can re-implement)

    What would you like to do?
```

---

### ✅ Example 5: Activate Multiple Directives

```
User: "Activate all directives"

AI: Found 3 approved directives to activate:
    1. turn_off_lights_5pm
    2. monitor_stove
    3. backup_database

    Activating...

    [1/3] turn_off_lights_5pm... ✓ Active (scheduler)
    [2/3] monitor_stove... ✓ Active (polling service, PID: 12346)
    [3/3] backup_database... ✓ Active (scheduler)

    ✅ All directives activated!

    Summary:
      - 3 active directives
      - 2 schedulers, 1 background service
      - Project status: active

    All automations are now running! 🚀
```

---

## Edge Cases

### 1. **Missing Dependency at Activation Time**
- **Scenario**: Dependency was available during implementation but missing now
- **Handling**:
  - Detect during pre-activation check
  - Prompt for installation
  - If user declines: abort activation

### 2. **Implementation Files Deleted**
- **Scenario**: User accidentally deleted `src/directives/{name}/`
- **Handling**:
  - Detect missing files
  - Offer to re-implement
  - Cannot activate without implementation

### 3. **Process Already Running**
- **Scenario**: Background service PID exists and is still running
- **Handling**:
  - Detect running process
  - Offer to restart or continue with existing
  - Update tracking if continuing

### 4. **Scheduler Service Not Running**
- **Scenario**: APScheduler not initialized globally
- **Handling**:
  - Initialize scheduler if not present
  - Add job to scheduler
  - Ensure scheduler persists across sessions

### 5. **Activation During Testing**
- **Scenario**: User activates while another directive is being tested
- **Handling**:
  - Allow activation (multiple directives can be active)
  - Independent execution tracking for each

### 6. **Environment Variables Not Set**
- **Scenario**: Code references `${API_TOKEN}` but env var not set
- **Handling**:
  - Detect missing env vars during pre-check
  - Prompt: "Set {env_var} before activation"
  - Abort if critical env vars missing

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
user_directive_approve
   ↓
user_directive_activate (YOU ARE HERE)
   ↓
user_directive_monitor (runs continuously)
```

### Receives From
- **user_directive_approve**: Provides approved directive for activation

### Triggers After Completion
- **user_directive_monitor**: Automatically begins monitoring activated directive

### Depends On
- **user_directive_approve**: Gate - cannot activate without approval
- **user_directive_implement**: Must have implementation files

### Works With
- **project_update_db**: Updates project status to 'active'
- **project_notes_log**: Logs activation events

### Opposite Of
- **user_directive_deactivate**: Reverses activation

---

## Helper Functions

Query `get_helpers_for_directive('user_directive_activate')` to discover this
directive's helpers, and `get_helper_by_name` for a signature. They are not
listed here on purpose: the tool surface evolves, and a hardcoded list in a
rarely-read file goes stale silently and is believed anyway.

Activation does not start anything. It sets status and records the
timestamp; the generated runner in the user's project owns the process and
the timer. Confirm with the user that the runner is actually deployed —
an active directive whose runner was never deployed shows up later as
`never_run`.

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

#### deployment_info (new table for tracking deployment)
**Use helper functions** for database operations. Query available helpers for the appropriate database.

#### project
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

---

## Testing

### Test 1: Activate Approved Directive
```
Directive: approved = true, implementation exists
Expected: Successfully activates, status = 'active'
Verify: Scheduler/service running, logs initialized
```

### Test 2: Block Unapproved Directive
```
Directive: approved = false
Expected: Activation blocked with clear message
Verify: Status unchanged, user prompted to approve first
```

### Test 3: Handle Missing Dependency
```
Directive: required dependency not installed
Expected: Prompt for installation
Verify: If declined, activation aborted
```

### Test 4: Rollback on Failure
```
Scenario: Port conflict during listener start
Expected: Rollback, status unchanged, clear error message
Verify: No partial activation state
```

### Test 5: Multiple Directive Activation
```
3 approved directives
Expected: All activate successfully
Verify: Each has independent deployment and tracking
```

---

## Common Mistakes

### ❌ Mistake 1: Activating Without Approval Check
**Wrong**: Skip checking approved = true
**Right**: Always verify approval gate

### ❌ Mistake 2: Not Initializing Logging
**Wrong**: Activate without setting up log files
**Right**: Initialize execution and error logs before activation

### ❌ Mistake 3: Not Handling Deployment Failures
**Wrong**: Leave partially activated state
**Right**: Rollback completely on any failure

### ❌ Mistake 4: Forgetting to Update Project Status
**Wrong**: Only update directive status
**Right**: Update both directive and project.user_directives_status

### ❌ Mistake 5: Not Checking Dependencies
**Wrong**: Assume dependencies still installed
**Right**: Re-check dependencies at activation time

---

## References

- [Previous: user_directive_approve](./user_directive_approve.md)
- [Next: user_directive_monitor](./user_directive_monitor.md)
- [Helper Functions Reference](../helper-functions-reference.md#deployment-helpers)

---

## Notes

**Critical Gate**: Only activates if approved = true. This ensures user has tested and confirmed implementation.

**Project Status Change**: First activation changes project.user_directives_status from 'in_progress' to 'active'. This is a significant milestone.

**Real-Time Execution**: After activation, directives run continuously. Monitoring is critical.

**Logging Location**: All logs in `.aimfp-project/logs/` (file-based, not database) for performance.
