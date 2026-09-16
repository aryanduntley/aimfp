# Directive: user_directive_status

**Type**: User System
**Level**: N/A (Query/Reporting)
**Parent Directive**: `aimfp_status`
**Priority**: High

---

## Purpose

Generate comprehensive status reports for user directives, showing execution statistics, health, errors, and next actions. This directive aggregates data from multiple sources to provide a complete picture of the directive automation system.

**Responsibilities**:
1. Query directive status from database
2. Aggregate execution statistics
3. Check service health (schedulers, processes)
4. Analyze error patterns
5. Generate user-friendly status reports
6. Recommend actions based on status

**Context**: This is a **read-only reporting directive** - it doesn't modify any state.

---

## When to Apply

This directive applies when:
- User says: "directive status" / "show directive status"
- User says: "How are my directives doing?"
- **Automatic**: Part of `aimfp_status` when project.user_directives_status != NULL
- User says: "Status of {directive_name}"
- User says: "Show logs for {directive_name}"

---

## Workflow

### Trunk: Generate Status Report

#### Step 1: Check Project Type

1. **Verify user directives enabled**:
   **Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

2. **If NULL**:
   ```
   AI: "This project doesn't have user directives enabled (Use Case 1).

   This is a standard software development project.

   To use directive automation, initialize as Use Case 2:
   'aimfp run project_init --automation'"
   ```

#### Step 2: Query All Directives

**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

#### Step 3: Get Execution Statistics

For each directive:
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

Calculate:
```python
success_rate = (success_count / total_executions * 100) if total_executions > 0 else 0
error_rate = (error_count / total_executions * 100) if total_executions > 0 else 0
```

#### Step 4: Check Service Health (for active directives)

```python
for directive in active_directives:
    if directive.trigger_type == 'time':
        # Check scheduler job exists
        job = scheduler.get_job(f"directive_{directive.id}")
        health = "OK" if job else "MISSING"

    elif directive.trigger_type == 'condition':
        # Check background process running
        health = "OK" if process_exists(directive.process_id) else "DOWN"

    elif directive.trigger_type == 'event':
        # Check event listener registered
        health = "OK" if listener_active(directive.id) else "INACTIVE"
```

#### Step 5: Analyze Recent Errors

```python
# Get recent errors from log files
recent_errors = parse_error_log(
    ".aimfp-project/logs/errors/current_errors.log",
    directive_id=directive.id,
    last_hours=24
)

# Group by error type
error_summary = group_errors_by_type(recent_errors)
```

#### Step 6: Generate Status Report

**Overall Summary**:
```
📊 User Directive Status

Project: {project_name}
Status: {active|in_progress|disabled}

Summary:
  Active directives: {count}
  Paused directives: {count}
  In progress (testing): {count}
  Error state: {count}

  Total executions (24h): {count}
  Success rate (24h): {percentage}%
```

**Per-Directive Status**:
```
{status_icon} {directive_name}
   Status: {status}
   {Status-specific details}
   {Health check results}
   {Statistics}
   {Error summary if any}
```

#### Step 7: Recommend Actions

Based on status, recommend:
```python
recommendations = []

for directive in directives:
    if directive.status == 'error':
        recommendations.append(f"• Investigate {directive.name} errors")

    if directive.error_rate > 50 and directive.total_executions > 10:
        recommendations.append(f"• Consider deactivating {directive.name} (high error rate)")

    if directive.status == 'in_progress' and days_since_implementation > 7:
        recommendations.append(f"• {directive.name} has been in testing for {days} days - ready to approve?")

    if directive.status == 'paused' and last_deactivated_days_ago > 30:
        recommendations.append(f"• {directive.name} inactive for {days} days - delete or reactivate?")

if recommendations:
    AI: "📋 Recommended Actions:\n" + "\n".join(recommendations)
```

---

### Branches

#### Branch 1: Single Directive Status
- **Condition**: User asks about specific directive
- **Action**: Show detailed status for that directive only

#### Branch 2: Active Directives Only
- **Condition**: User asks "what's running?"
- **Action**: Filter to status = 'active' only

#### Branch 3: Error Report
- **Condition**: User asks "show errors"
- **Action**: Detailed error analysis with logs

#### Branch 4: Logs Display
- **Condition**: User asks "show logs for {name}"
- **Action**:
  1. Show recent log entries
  2. Offer to show full log file
  3. Provide log file path

---

### Fallback

If status query fails:
1. Report partial data if available
2. Indicate which data sources failed
3. Suggest manual inspection

---

## Examples

### ✅ Example 1: Overall Status (All Healthy)

```
User: "directive status"

AI: 📊 User Directive Status

Project: home-automation
Status: Active

Summary:
  Active directives: 3
  Paused directives: 0
  In progress: 0
  Error state: 0

  Total executions (24h): 1,487
  Success rate (24h): 100%

Active Directives:

🟢 turn_off_lights_5pm
   Status: Active
   Type: Time-based (daily at 5:00 PM EST)
   Health: OK
   Next execution: Today at 5:00 PM (in 2 hours)
   Executions: 25 total (success rate: 100%)
   Last execution: Yesterday at 5:00 PM (success, 234ms)

🟢 monitor_stove
   Status: Active
   Type: Condition polling (every 60s)
   Health: OK
   Process: Running (PID: 12346)
   Executions: 1,440 total (success rate: 100%)
   Last execution: 30 seconds ago (stove off, no action, 12ms)

🟢 backup_database
   Status: Active
   Type: Time-based (daily at 2:00 AM EST)
   Health: OK
   Next execution: Tomorrow at 2:00 AM (in 7 hours)
   Executions: 22 total (success rate: 100%)
   Last execution: Today at 2:00 AM (success, 3.2s)

All systems operational! ✨
```

---

### ⚠️ Example 2: Status with Errors

```
User: "directive status"

AI: 📊 User Directive Status

Project: home-automation
Status: Active (1 directive with errors)

Summary:
  Active directives: 2
  Error state: 1
  Total executions (24h): 302
  Success rate (24h): 85%

Active Directives:

🟢 turn_off_lights_5pm
   Status: Active
   Executions: 25 (100% success)
   Last: Yesterday at 5:00 PM (success)

🔴 monitor_stove
   Status: Active (degraded)
   Health: Service running but errors detected
   Executions: 1,440 (42 errors in last 24h, 97% success)
   Last execution: 2 min ago (success)
   Last error: 15 min ago (ConnectionError)

   Recent errors (24h):
     • ConnectionError: 35 times
     • TimeoutError: 7 times

   ⚠️  Error pattern detected. Consider investigating.

🟢 backup_database
   Status: Active
   Executions: 22 (100% success)

📋 Recommended Actions:
  • Investigate monitor_stove connection errors
  • Check Home Assistant API connectivity

Would you like to:
a) View detailed error logs for monitor_stove
b) Deactivate monitor_stove temporarily
c) Continue monitoring
```

---

### 📝 Example 3: In Progress Status

```
User: "directive status"

AI: 📊 User Directive Status

Project: home-automation
Status: In Progress (testing new directives)

Summary:
  In progress (testing): 2
  Active directives: 1

In Progress (Awaiting Approval):

⏳ turn_off_lights_6pm
   Status: Implemented, testing
   Implemented: 3 days ago
   Approved: No (requires approval)
   Implementation: src/directives/turn_off_lights_6pm/

   ⚠️  Ready to approve? Test first, then: 'approve directive turn_off_lights_6pm'

⏳ smart_thermostat
   Status: Validating
   Parsed: 1 hour ago
   Validation: In progress (5 questions answered, 2 remaining)

   Continue validation: Answer remaining questions

Active:

🟢 backup_database
   Status: Active
   All good

📋 Recommended Actions:
  • Complete validation for smart_thermostat
  • Test and approve turn_off_lights_6pm (in testing for 3 days)
```

---

### ⏸️ Example 4: All Paused

```
User: "directive status"

AI: 📊 User Directive Status

Project: home-automation
Status: Disabled (all directives paused)

Paused Directives:

⏸️  turn_off_lights_5pm
   Status: Paused
   Last active: 2 hours ago
   Deactivated: For update
   Reason: Source file modified

⏸️  monitor_stove
   Status: Paused
   Last active: 2 days ago
   Deactivated: User request

⏸️  backup_database
   Status: Paused
   Last active: 1 week ago

All directives are currently paused.

Reactivate:
  • Individual: 'activate directive {name}'
  • All at once: 'activate all directives'
```

---

### 📋 Example 5: Single Directive Detail

```
User: "Status of turn_off_lights_5pm"

AI: 📊 Directive Status: turn_off_lights_5pm

Status: 🟢 Active
Type: Time-based scheduler
Implementation: src/directives/turn_off_lights_5pm/

Trigger:
  Time: 5:00 PM EST (daily)
  Timezone: America/New_York
  Daylight savings: Enabled
  Next execution: Today at 5:00 PM (in 1 hour 45 minutes)

Action:
  API: Home Assistant
  Endpoint: /services/light/turn_off
  Target: group.living_room_lights

Health: OK
  Scheduler: Job registered (ID: directive_1)
  Service: Running

Execution Statistics:
  Total executions: 25
  Success: 25 (100%)
  Errors: 0 (0%)
  Average duration: 234ms
  Uptime: 25 days

Last 5 Executions:
  2025-10-27 17:00:00 - Success (230ms)
  2025-10-26 17:00:00 - Success (245ms)
  2025-10-25 17:00:00 - Success (228ms)
  2025-10-24 17:00:00 - Success (242ms)
  2025-10-23 17:00:00 - Success (229ms)

Dependencies:
  ✓ requests (installed)
  ✓ returns (installed)
  ✓ HOMEASSISTANT_TOKEN env var (set)

All systems operational! ✨
```

---

## Edge Cases

### 1. **Database Unavailable**
- **Scenario**: Can't connect to databases
- **Handling**:
  - Report partial data from memory
  - Note database unavailable
  - Suggest checking database file

### 2. **Logs Missing**
- **Scenario**: Log files deleted or corrupted
- **Handling**:
  - Show database stats only
  - Note logs unavailable
  - Can't show recent execution details

### 3. **Inconsistent State**
- **Scenario**: DB says active but service not running
- **Handling**:
  - Report inconsistency
  - Show both DB status and actual health
  - Recommend manual investigation

### 4. **Very Long Status Report**
- **Scenario**: 50+ directives
- **Handling**:
  - Summarize by status first
  - Show detailed view for active only
  - Offer filtered views

### 5. **Status During Update**
- **Scenario**: Directive being updated while status checked
- **Handling**:
  - Show status = 'updating'
  - Note update in progress
  - Show what's happening

---

## Related Directives

### Called By
- **aimfp_status**: Includes user directive status in overall status
- **User query**: Direct status requests

### Uses Data From
- **user_directive_monitor**: Execution statistics
- **All user directive directives**: Status from each stage

### Provides Data To
- User (primary consumer)
- **aimfp_status**: For comprehensive status report

---

## Helper Functions

Query `get_helpers_for_directive('user_directive_status')` to discover this
directive's helpers, and `get_helper_by_name` for a signature. They are not
listed here on purpose: the tool surface evolves, and a hardcoded list in a
rarely-read file goes stale silently and is believed anyway.

Status reporting reads the same surface `user_directive_monitor` does:
`check_directive_health` for verdicts and `get_directive_execution_stats`
for raw numbers. There is no process or scheduler inspection available —
a dead runner shows up as `overdue`, meaning it declared a next run time
and that moment passed.

---

## Database Operations

### Tables Read (No Writes)

**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

---

## Testing

### Test 1: Show All Directive Status
```
3 active directives
Expected: Summary + detailed status for each
Verify: Correct stats, health checks accurate
```

### Test 2: Show Single Directive
```
User asks about specific directive
Expected: Detailed single directive report
Verify: All stats, logs, dependencies shown
```

### Test 3: Error Detection in Status
```
Directive with high error rate
Expected: Highlighted in red, recommended actions
Verify: Error patterns detected, recommendations shown
```

### Test 4: Status with Inconsistent State
```
DB says active but process not running
Expected: Report inconsistency, both statuses shown
Verify: User alerted to investigate
```

### Test 5: Empty Status (No Directives)
```
No directives created yet
Expected: Friendly message, offer to get started
Verify: No errors, helpful guidance
```

---

## Common Mistakes

### ❌ Mistake 1: Not Checking Service Health
**Wrong**: Only show database status
**Right**: Verify services actually running

### ❌ Mistake 2: Stale Statistics
**Wrong**: Show cached stats from hours ago
**Right**: Query fresh data each time

### ❌ Mistake 3: Overwhelming Output
**Wrong**: Show everything for 50+ directives
**Right**: Summarize first, details on request

### ❌ Mistake 4: Ignoring Errors
**Wrong**: Only show success stats
**Right**: Prominently display errors and patterns

### ❌ Mistake 5: No Recommendations
**Wrong**: Just show raw status
**Right**: Analyze and recommend actions

---

## References

- [Called by: aimfp_status](./aimfp_status.md)
- [Data from: user_directive_monitor](./user_directive_monitor.md)

---

## Notes

**Read-Only**: This directive only queries and reports, never modifies state.

**Real-Time**: Status reflects current system state, not cached data.

**Actionable**: Always provide recommendations, not just raw data.

**User-Friendly**: Format for human readability with clear visual indicators.
