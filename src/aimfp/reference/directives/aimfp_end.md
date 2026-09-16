# aimfp_end - Session Termination Directive

**Type**: Project Management
**Level**: 0
**Parent**: None (user-initiated session lifecycle)
**Category**: Session Management

---

## Purpose

`aimfp_end` provides graceful session termination with a comprehensive audit of all work done during the session. It is the counterpart to `aimfp_run(is_new_session=true)` — together they bookend the session lifecycle.

**What it does**:
- Stops the watchdog process and reads final reminders (if watchdog is running)
- Verifies all files written during the session are tracked in project.db
- Verifies all functions are registered in the database
- Quick FP compliance scan of session work
- Reviews project management state for staleness
- Generates and presents a session summary to the user
- Confirms safe to end session

**Use this directive when**:
- User says "end session", "wrap up", "done for now", "signing off"
- AI detects the user is ending the conversation
- User explicitly requests session audit

**DO NOT use when**:
- Project is not initialized (nothing to audit)
- User is just pausing temporarily (normal `aimfp_status` loop continues)

---

## When to Use

### Manual Trigger

Keywords: "end session", "wrap up", "done for now", "finish session", "close", "goodbye", "signing off"

### Important Note

`aimfp_end` is a **best practice**, not mandatory. If the user closes the chat without calling it:
- The watchdog catches drift during the next session (startup reconciliation plus live monitoring)
- `aimfp_run(is_new_session=true)` on next session start detects and addresses stale state
- No data is lost — the database is always the source of truth

---

## Workflow

### Trunk: `begin_session_audit`

Call the `aimfp_end` helper to gather all audit data from the database. The helper returns structured data; AI interprets it and acts.

**Helper**: `aimfp_end(project_root)` — Returns:
```json
{
  "success": true,
  "watchdog": {
    "stopped": true,
    "final_reminders": []
  },
  "project_state": {
    "files": [],
    "functions": [],
    "active_tasks": [],
    "active_items": [],
    "recent_notes": [],
    "metadata": {}
  }
}
```

---

### Branch 1: Stop Watchdog and Read Final Reminders

**Condition**: Watchdog PID file exists at `.aimfp-project/watchdog/watchdog.pid`.

**Steps**:
1. The `aimfp_end` helper reads the PID file and kills the watchdog process
2. Helper reads final `reminders.json` before clearing
3. AI processes any outstanding reminders:
   - `missing_function`: Register the function
   - `new_file_detected`: Register the file
   - `missing_db_function`: Flag for review (may have been removed/renamed)
   - `timestamp_synced`: Already handled by watchdog (informational only)
4. If watchdog not implemented yet: helper returns `watchdog.stopped = null`, AI skips this branch gracefully

**Watchdog note**: Watchdog implementation is pending. This branch is designed to work both before and after watchdog is built.

---

### Branch 2: Verify All Files Tracked

**Action**: Cross-reference session work against database.

**Steps**:
1. AI identifies files written or modified during this session (from context/memory)
2. Compare against `files` list returned by helper
3. If any file missing from DB: register it using `reserve_file` + `finalize_file`
4. If any file has stale `updated_at`: correct the timestamp
5. Report discrepancies found and actions taken

---

### Branch 3: Verify All Functions Tracked

**Action**: Cross-reference functions against database.

**Steps**:
1. For each file modified during session, scan for function definitions
2. Compare against `functions` list returned by helper
3. If any function missing from DB: register it using `reserve_function` + `finalize_function`
4. If any DB function missing from actual file: flag for user review (may have been removed or renamed during session)
5. Report discrepancies found and actions taken

---

### Branch 4: Check FP Compliance

**Action**: Quick FP scan of all code written during session.

**Steps**:
1. Review all code written during this session
2. Check for common violations:
   - Mutations (mutable variables, in-place modifications)
   - OOP patterns (classes with methods, inheritance)
   - Unwrapped external library calls
   - Missing Result types for fallible operations
   - Hidden state (global mutable variables)
3. Report violations to user (**informational — do not auto-fix**)
4. Log compliance status in notes (`source=directive`, `directive_name=aimfp_end`, `severity=info` or `warning`)

**Note**: This is a quick scan, not a deep audit. FP compliance should have been maintained during coding. This catches anything that slipped through.

---

### Branch 5: Review Project Management State

**Action**: Ensure all project management data is current.

**Steps**:
1. Check task/item progress: any items worked on but status not updated?
2. Check notes: any important discussion points or decisions not logged?
3. Check completion path: does current progress match task/milestone state?
4. Check themes/flows: any new patterns observed during session that should be captured?
5. Check infrastructure: any changes to tools, dependencies, or conventions?
6. Update anything that is stale or missing

---

### Branch 6: Generate Session Summary

**Action**: Compile and present session summary to user.

**Session summary includes**:
- Files created / modified
- Functions added / modified
- Tasks completed / progressed
- Milestones progressed
- Discrepancies found and fixed
- FP compliance status
- Outstanding items for next session

**Steps**:
1. Compile summary from audit results
2. Log session summary in notes (`source=directive`, `directive_name=aimfp_end`)
3. Present to user:
   - **If clean**: "All project data is up to date. Safe to end session."
   - **If outstanding issues**: List them with severity and recommended next-session actions

---

### Fallback: No Work Done

If no code changes were detected this session:
```
No code changes detected this session. Project state unchanged. Safe to end session.
```

---

## Terminal Flow

`aimfp_end` is **terminal** — it does not loop back to `aimfp_status`. The session is over after `aimfp_end` completes. There is no flow entry for `aimfp_end → aimfp_status`.

---

## Interactions with Other Directives

### Called By

- **`aimfp_status`** — Routes here when `user_requests_session_end = true`

### Calls

- **`aimfp_end` helper** — Gathers all audit data (files, functions, tasks, watchdog state)
- **Helpers** (conditionally): `reserve_file`, `finalize_file`, `reserve_function`, `finalize_function`, `project_notes_log`

### Related

- **`aimfp_run`** — Counterpart (session start vs session end)
- **`aimfp_status`** — Uses similar state gathering approach

---

## Edge Cases

### Case 1: Watchdog Process Cannot Be Stopped

Log warning, continue with rest of audit. Watchdog will be killed automatically when the parent MCP process exits.

### Case 2: Database Locked

Retry with backoff. If persistent, warn user and log the lock issue. Present partial audit results.

### Case 3: Large Session with Many Changes

Process audit in batches. Report progress to user between sections.

### Case 4: User Closes Without aimfp_end

Not a failure state. The watchdog catches file/function drift during the next session via startup reconciliation. `aimfp_run(is_new_session=true)` on next session start provides fresh context.

---

## Database Operations

**Read Operations** (via `aimfp_end` helper):
- Files table (all registered files)
- Functions table (all registered functions)
- Tasks/items tables (active work)
- Notes table (recent notes)
- Project metadata table
- Watchdog PID file and reminders.json

**Write Operations** (by AI based on audit):
- Files table (register missing files, fix timestamps)
- Functions table (register missing functions)
- Notes table (session summary, compliance log)
- Tasks/items tables (status corrections)

---

## FP Compliance

**Purity**: ⚠️ Effect function — reads DB and files, writes DB, kills process
**Immutability**: ✅ Audit data gathered as immutable snapshot, then acted on
**Side Effects**: ⚠️ Explicit — all operations via helpers, process kill via OS signal

---

## Best Practices

1. **Always recommend** — When user indicates they're done, suggest `aimfp_end`
2. **Don't block** — If audit finds issues, report them but don't prevent session close
3. **Inform, don't fix silently** — FP violations are reported, not auto-corrected
4. **Log everything** — Session summary should be a complete record
5. **Graceful degradation** — If watchdog isn't implemented, skip that branch

---

## Version History

- **v1.0** (2026-01-30): Initial creation — session termination with comprehensive audit

---

## Notes

- `aimfp_end` is a best practice, not a requirement — sessions can end without it
- Implemented in `helpers/orchestrators/entry_points.py` and registered as an MCP tool
- Watchdog integration degrades gracefully: if no watchdog is running, there is no PID file and `watchdog.stopped` comes back null
- Terminal flow — no loop back to status after completion
