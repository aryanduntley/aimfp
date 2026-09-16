# Directive: user_directive_monitor

**Type:** project
**Level:** 3
**Parent:** user_directive_activate
**Use Case:** 2 only — requires `.aimfp-project/user_directives.db`

---

## Purpose

Review what the user's directive runners did while no AI session was open, and
report anything that needs attention.

This directive is **read-only**. AIMFP does not execute user directives and does
not supervise the process that runs them.

---

## The execution model this directive assumes

Read this before the workflow; it is the reason the steps look the way they do.

A user directive is not run by AIMFP. During `user_directive_implement`, the AI
generates code into the user's own project — including whatever timer the
directive implied, whether that is cron, a systemd timer, or an in-process
scheduler. That generated runner calls the AIMFP hook library
(`aimfp.hooks.directives`) to ask what is due and to record what happened:

```python
from aimfp.hooks.directives import get_due_directives, run_directive

for d in get_due_directives(project_root=ROOT).directives:
    run_directive(d.directive_id, HANDLERS[d.name], ROOT)
```

`run_directive` times the handler, catches its exceptions, writes the JSON-lines
log records, and updates `directive_executions`. All of that happens at 3am with
no AI present.

So by the time this directive runs, **the work is already done and recorded**.
Your job is to look at the record and interpret it.

### Why there is no PID check here

An earlier version of this workflow asked the AI to verify process IDs, confirm
scheduler registration, and restart dead services. None of that is possible:
AIMFP is an MCP server answering tool calls, with no long-running process and no
handle on the user's runner.

A dead runner is detected a different way. The runner declares when it next
expects to fire, via `set_next_scheduled_time`. **Silence past a declared
deadline is the evidence.** That is what `overdue` means, and it needs no
process inspection at all.

The corollary matters: a runner that never records its schedule can never be
reported overdue. If a directive looks silent but never appears overdue, suspect
the runner is not calling `set_next_scheduled_time`.

---

## When to Apply

- At session start, when the project has active user directives
- When the user asks how their automation is doing
- When another directive reports a directive in error state
- Before `user_directive_update` — know what is broken before changing it

Do **not** apply to a Use Case 1 project. No `user_directives.db` means there is
nothing to monitor, which is normal and not an error.

---

## Workflow

**Trunk:** `review_directive_health`

### Step 1: `assess_health`

Call `check_directive_health`. Branch on `needs_attention`:

- **false** — say directives are running normally and stop. Do not dig further;
  there is nothing to find, and listing healthy directives is noise.
- **true** — report each non-ok directive with its health state and `detail`,
  which is written to read as a sentence.

Health states, in the precedence the assessment applies:

| State | Meaning |
|---|---|
| `error` | The directive's own status is `error` |
| `overdue` | `next_scheduled_time` has passed — the dead-runner signal |
| `degraded` | Error rate at or above the threshold (default 50%) |
| `never_run` | Active but never executed — runner likely not deployed |
| `ok` | Running normally |

Precedence is deliberate and most-actionable-first: a directive already in error
state is not better news for being on schedule.

### Step 2: `investigate_failures`

*When a directive is `error` or `degraded`.*

Call `get_recent_directive_errors(directive_id=<id>)`. Look for a **repeated
`error_type`** across records — that points at a systemic cause such as a dead
dependency or a rotated credential, rather than a flaky run.

- `handler_exception` — the user's own handler raised; the record carries a full
  traceback.
- `execution_error` — the runner reported failure itself, without a traceback.

Tell the user what is failing and why, and propose a fix. Do not silently repair
a directive they authored.

### Step 3: `investigate_silence`

*When a directive is `overdue` or `never_run`.*

- **`overdue`** — the runner said it would fire and did not. Check that it is
  deployed and its scheduler is running, using whatever mechanism the
  implementation chose. AIMFP cannot check this for you.
- **`never_run`** — active but never executed. Implementation likely completed
  while the runner was never deployed, or its schedule was never registered.

### Step 4: `report_statistics`

*When the user asks how a directive has been performing.*

Call `get_directive_execution_stats`. Report executions, success and error
counts, error rate, average and maximum duration, and last execution time.

Note what the database does and does not hold: it stores **summary statistics
only** (`store_execution_history` defaults to 0). Per-run history lives in the
JSON-lines execution log under 30-day retention; error history under 90-day.

### Step 5: `route_to_repair`

*When the fix requires changing the directive or its generated code.*

- Intent or directive file changed → route to `user_directive_update`
- Should stop running while repaired → route to `user_directive_deactivate`
- Record the decision with `add_user_directive_note`

---

## Fallback

Report what could not be read and continue. Monitoring is read-only and must
never be the reason other work stops.

---

## Examples

### ✅ Everything healthy

```
check_directive_health() -> needs_attention=False, summary={'ok': 3}
```

> "All 3 active directives are running normally."

Then stop. Do not enumerate them.

### ⚠️ Overdue runner

```
summary={'ok': 2, 'overdue': 1}
backup_photos: overdue by 14h - expected to run already; its runner may have died
```

> "`backup_photos` is 14 hours overdue — it told AIMFP it would run at 02:00 and
> never did. The other two are fine. Worth checking whether its cron job is still
> installed; AIMFP can't see the process, only that the deadline passed."

### 🔴 Systemic failure

```
summary={'degraded': 1}
get_recent_directive_errors(directive_id=4) -> 8 records, all error_type='connection_error'
```

> "`monitor_stove` has failed its last 8 runs, every one a `connection_error` —
> that is a dead dependency, not flakiness. The Home Assistant endpoint is
> probably unreachable or its token expired."

---

## Edge Cases

**No `user_directives.db`.** Use Case 1 project. Nothing to monitor; not an
error. Say so and move on.

**A directive with no `next_scheduled_time`.** Cannot be reported overdue, ever.
For a `manual` or `event` trigger that is correct. For a `time` trigger it means
the runner is not recording its schedule — flag that, because it makes silence
indistinguishable from health.

**Non-zero `malformed_lines`.** Usually one run interrupted mid-write, which is
normal. Many of them suggest something other than AIMFP is writing to that log.

**Errors exceeding executions.** Expected. `record_directive_error` records
failures that happened *outside* a run — a trigger that misfired, a missing
dependency — without counting an execution. A directive that failed to start ten
times has ten errors and zero executions.

**Empty error log.** The good case. Nothing has failed.

---

## Related Directives

**Pipeline position:** `user_directive_activate` → **`user_directive_monitor`** →
`user_directive_update` / `user_directive_deactivate`

- **Triggered by:** `user_directive_activate`, session start with active directives
- **Routes to:** `user_directive_update` (directive file changed)
- **Works with:** `user_directive_status` (inventory), all active directives

---

## Helper Functions

Query `get_helpers_for_directive('user_directive_monitor')` to discover this
directive's helpers, and `get_helper_by_name` for a signature. They are not
listed here on purpose: the tool surface evolves, and a hardcoded list in a
rarely-read file goes stale silently and is believed anyway.

The hook library the *runner* calls is a separate surface, discoverable with
`get_hooks`. You never call those yourself — you generate code that does.

---

## Database Operations

**Reads:** `user_directives`, `directive_executions`, and the JSON-lines logs
under `.aimfp-project/logs/`.

**Writes:** none. This directive is read-only; repairs route to
`user_directive_update` or `user_directive_deactivate`.

Use helper functions rather than direct SQL. Direct queries against
`user_directives.db` are acceptable if the helpers are insufficient, but prefer
the helpers.

---

## Testing

Covered by `tests/test_directive_monitoring.py`: every health classification and
its precedence, error-log tailing including a truncated final line, statistics
with and without a directive filter, and clean failure on a Use Case 1 project.
Classification is a pure function, so verdicts are asserted directly without a
database or a clock.
