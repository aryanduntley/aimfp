# Directive: user_directive_implement

**Type**: User System
**Level**: 3
**Parent Directive**: `user_directive_validate`
**Priority**: Critical

---

## Purpose

Generate FP-compliant implementation code from validated user directive configurations. This directive transforms the validated directive specification into actual executable Python code that follows all AIMFP functional programming principles.

**Key Responsibilities**:
1. Generate pure, functional code from directive configuration
2. Apply all FP directives (purity, immutability, no OOP)
3. Create trigger handlers and action executors
4. Generate tests for the implementation
5. Update project.db to track generated code

**Critical Rule**: All generated code MUST pass FP compliance checks before being written to files.

---

## When to Apply

This directive applies when:
- **Automatically after validate**: Directive reaches `validation_status='validated'`
- User says: "Implement my directives"
- User says: "Generate code for {directive_name}"
- Detected trigger: `user_directives` table has entries with `status='validated'` and no implementation

**Always runs after**: `user_directive_validate`
**Always runs before**: `user_directive_approve`

---

## Workflow

### Trunk: Generate FP-Compliant Implementation

#### Step 1: Load Validated Configuration

1. **Query directive**:
   ```sql
   SELECT id, name, validated_config, trigger_type, action_type
   FROM user_directives
   WHERE validation_status = 'validated'
     AND implementation_status IS NULL
   ORDER BY validated_at ASC
   LIMIT 1;
   ```

2. **Parse validated configuration**:
   ```python
   config = json.loads(directive.validated_config)
   trigger_config = config['trigger']
   action_config = config['action']
   conditions = config.get('conditions', [])
   metadata = config.get('metadata', {})
   ```

#### Step 2: Design Module Structure

1. **Determine file structure** (generated in `src/`, the actual project code):
   ```
   # User's automation project directory
   home-automation/
   ├── directives/               # ← User writes directive definitions here
   │   └── lights.yaml
   ├── src/                      # ← AI GENERATES implementation here
   │   └── directives/
   │       └── {directive_name}/
   │           ├── __init__.py
   │           ├── trigger.py       # Trigger handler (pure functions)
   │           ├── action.py        # Action executor (effect functions)
   │           ├── conditions.py    # Condition evaluators (pure functions)
   │           └── types.py         # ADTs and data structures
   ├── tests/                    # ← AI GENERATES tests here
   │   └── directives/
   │       └── test_{directive_name}.py
   └── .aimfp-project/            # ← AI-managed metadata (user never touches)
       ├── project.db            # Tracks generated src/ code
       └── user_directives.db    # References ../directives/ files
   ```

2. **Plan function signatures**:
   - Pure functions: trigger evaluation, condition checking, data transformation
   - Effect functions: API calls, I/O operations (isolated)
   - Main orchestrator: compose pure + effect functions

#### Step 3: Generate Type Definitions

1. **Create ADTs (Algebraic Data Types)**:
   ```python
   # types.py
   from dataclasses import dataclass
   from typing import Result, Optional
   from datetime import datetime

   @dataclass(frozen=True)  # Immutable
   class TriggerEvent:
       """Immutable trigger event data"""
       timestamp: datetime
       trigger_type: str
       data: dict  # Validated trigger-specific data

   @dataclass(frozen=True)
   class ActionResult:
       """Immutable action execution result"""
       success: bool
       message: str
       execution_time_ms: int
       data: Optional[dict] = None
   ```

2. **Apply FP directives**:
   - ✅ `fp_immutability`: Use `frozen=True` dataclasses
   - ✅ `fp_type_safety`: Explicit type annotations
   - ✅ `fp_no_oop`: Dataclasses only, no methods with side effects

#### Step 4: Generate Trigger Handler (Pure Functions)

1. **Time-based trigger example**:
   ```python
   # trigger.py
   from datetime import datetime, time
   from typing import Result
   from returns.result import Success, Failure
   from .types import TriggerEvent

   def should_trigger(
       current_time: datetime,
       target_time: time,
       timezone: str
   ) -> bool:
       """
       Pure function: Check if current time matches trigger time.

       Args:
           current_time: Current datetime
           target_time: Target time to trigger
           timezone: Timezone string

       Returns:
           True if should trigger, False otherwise
       """
       # Pure logic - no side effects
       local_time = current_time.astimezone(timezone)
       return (
           local_time.hour == target_time.hour and
           local_time.minute == target_time.minute
       )

   def create_trigger_event(
       current_time: datetime,
       config: dict
   ) -> TriggerEvent:
       """
       Pure function: Create immutable trigger event.
       """
       return TriggerEvent(
           timestamp=current_time,
           trigger_type='time',
           data=config
       )
   ```

2. **Condition-based trigger example**:
   ```python
   # trigger.py
   from typing import Result
   from returns.result import Success, Failure

   def check_condition(
       metric_value: float,
       threshold: float,
       operator: str
   ) -> bool:
       """
       Pure function: Evaluate condition.

       Args:
           metric_value: Current metric value
           threshold: Threshold to compare against
           operator: Comparison operator ('gt', 'lt', 'eq')

       Returns:
           True if condition met, False otherwise
       """
       operators = {
           'gt': lambda v, t: v > t,
           'lt': lambda v, t: v < t,
           'eq': lambda v, t: v == t,
           'gte': lambda v, t: v >= t,
           'lte': lambda v, t: v <= t
       }
       return operators[operator](metric_value, threshold)
   ```

3. **Apply FP directives**:
   - ✅ `fp_purity`: No side effects, deterministic
   - ✅ `fp_immutability`: Immutable parameters and returns
   - ✅ `fp_type_safety`: Full type annotations
   - ✅ `fp_docstring_enforcement`: Docstrings on all functions

#### Step 5: Generate Action Executor (Effect Functions)

1. **API call action example**:
   ```python
   # action.py
   import requests
   from typing import Result
   from returns.result import Success, Failure
   from .types import ActionResult
   import time

   def execute_api_call(
       url: str,
       method: str,
       headers: dict,
       payload: dict
   ) -> Result[ActionResult, str]:
       """
       Effect function: Make API call (isolated side effect).

       Args:
           url: API endpoint URL
           method: HTTP method
           headers: Request headers
           payload: Request body

       Returns:
           Success with ActionResult or Failure with error message
       """
       start_time = time.time()

       try:
           response = requests.request(
               method=method,
               url=url,
               headers=headers,
               json=payload,
               timeout=30
           )
           response.raise_for_status()

           execution_time = int((time.time() - start_time) * 1000)

           return Success(ActionResult(
               success=True,
               message=f"API call successful: {response.status_code}",
               execution_time_ms=execution_time,
               data=response.json() if response.content else None
           ))

       except requests.RequestException as e:
           execution_time = int((time.time() - start_time) * 1000)
           return Failure(f"API call failed: {str(e)}")
   ```

2. **Apply FP directives**:
   - ✅ `fp_io_isolation`: I/O isolated in effect function
   - ✅ `fp_result_types`: Use Result type instead of exceptions
   - ✅ `fp_immutability`: Return immutable ActionResult
   - ✅ `fp_side_effects_flag`: Clearly marked as effect function

#### Step 6: Generate Main Orchestrator

1. **Compose pure and effect functions**:
   ```python
   # __init__.py
   from datetime import datetime
   from typing import Result
   from returns.result import Success, Failure
   from returns.pipeline import flow
   from .trigger import should_trigger, create_trigger_event
   from .action import execute_api_call
   from .types import TriggerEvent, ActionResult

   def run_directive(
       current_time: datetime,
       config: dict
   ) -> Result[ActionResult, str]:
       """
       Orchestrate directive execution: pure trigger check + effect action.

       Args:
           current_time: Current datetime for trigger evaluation
           config: Validated directive configuration

       Returns:
           Success with ActionResult or Failure with error message
       """
       # Pure: Check trigger
       if not should_trigger(
           current_time,
           config['trigger']['time'],
           config['trigger']['timezone']
       ):
           return Success(ActionResult(
               success=False,
               message="Trigger condition not met",
               execution_time_ms=0
           ))

       # Pure: Create trigger event
       trigger_event = create_trigger_event(current_time, config)

       # Effect: Execute action
       return execute_api_call(
           url=config['action']['url'],
           method=config['action']['method'],
           headers=config['action']['headers'],
           payload=config['action']['payload']
       )
   ```

2. **Apply FP directives**:
   - ✅ `fp_function_composition`: Compose pure + effect functions
   - ✅ `fp_monadic_composition`: Use Result type throughout
   - ✅ `fp_pattern_matching`: Clear conditional flow

#### Step 7: Generate Tests

1. **Test pure functions**:
   ```python
   # tests/directives/test_{directive_name}.py
   import pytest
   from datetime import datetime, time
   from src.directives.{directive_name}.trigger import should_trigger

   def test_should_trigger_returns_true_when_time_matches():
       """Test trigger fires at correct time"""
       current = datetime(2025, 1, 1, 17, 0)  # 5pm
       target = time(17, 0)
       timezone = 'America/New_York'

       result = should_trigger(current, target, timezone)

       assert result is True

   def test_should_trigger_returns_false_when_time_mismatch():
       """Test trigger doesn't fire at wrong time"""
       current = datetime(2025, 1, 1, 16, 0)  # 4pm
       target = time(17, 0)  # 5pm
       timezone = 'America/New_York'

       result = should_trigger(current, target, timezone)

       assert result is False
   ```

2. **Test effect functions with mocks**:
   ```python
   from unittest.mock import patch, Mock
   from src.directives.{directive_name}.action import execute_api_call

   @patch('requests.request')
   def test_execute_api_call_success(mock_request):
       """Test API call succeeds"""
       mock_response = Mock()
       mock_response.status_code = 200
       mock_response.json.return_value = {'status': 'ok'}
       mock_request.return_value = mock_response

       result = execute_api_call(
           url='http://example.com/api',
           method='POST',
           headers={},
           payload={'data': 'test'}
       )

       assert result.is_success()
       assert result.unwrap().success is True
   ```

3. **Apply FP directives**:
   - ✅ `fp_test_purity`: Test pure functions with deterministic inputs/outputs
   - ✅ `fp_side_effect_detection`: Mock side effects in tests

#### Step 8: Write Files and Update Database

**CRITICAL**: Generated code goes in `src/`, NOT in `.aimfp-project/`. The generated code IS the project.

1. **Write generated files to `src/`**:
   - Use `project_file_write` directive for each file
   - Location: `src/directives/{directive_name}/` (or `src/` for smaller implementations)
   - This automatically:
     - Writes file to disk
     - Updates project.db (files, functions tables)
     - Validates FP compliance

2. **Update project metadata** (project IS the automation):
   ```sql
   -- Update project purpose to reflect automation
   UPDATE project
   SET purpose = 'User directive automation: ' || ?,
       goals_json = ?  -- Include directive goals
   WHERE id = ?;
   ```

3. **Update user_directives table**:
   ```sql
   UPDATE user_directives
   SET implementation_status = 'implemented',
       implemented_at = CURRENT_TIMESTAMP,
       approved = false  -- Requires user testing and approval
   WHERE id = ?;

   -- Status stays 'in_progress' until user approves
   -- Do NOT change project.user_directives_status yet
   ```

4. **Store implementation mapping**:
   ```sql
   INSERT INTO directive_implementations (
       directive_id,
       file_path,
       function_name,
       function_type  -- 'trigger', 'action', 'condition', 'orchestrator'
   ) VALUES
       (?, 'src/directives/{name}/trigger.py', 'should_trigger', 'trigger'),
       (?, 'src/directives/{name}/action.py', 'execute_api_call', 'action'),
       (?, 'src/directives/{name}/__init__.py', 'run_directive', 'orchestrator');
   ```

5. **Inform user about testing requirement**:
   ```
   AI: "✅ Implementation complete!

   Generated files in src/:
   - src/directives/{name}/trigger.py
   - src/directives/{name}/action.py
   - src/directives/{name}/__init__.py
   - tests/directives/test_{name}.py

   ⚠️  IMPORTANT: Please test the implementation before activation.

   Testing instructions:
   1. Run tests: pytest tests/directives/test_{name}.py
   2. Test manually: python src/directives/{name}/__init__.py
   3. Verify behavior matches your expectations

   When testing is complete, say: 'Approve directive {name}'"
   ```

---

### Branches

#### Branch 1: If Time-Based Trigger
- **Condition**: `trigger_type='time'`
- **Action**: Generate cron-compatible scheduler + time comparison functions

#### Branch 2: If Condition-Based Trigger
- **Condition**: `trigger_type='condition'`
- **Action**: Generate metric polling + threshold comparison functions

#### Branch 3: If Event-Based Trigger
- **Condition**: `trigger_type='event'`
- **Action**: Generate webhook listener + event validation functions

#### Branch 4: If API Call Action
- **Condition**: `action_type='api_call'`
- **Action**: Generate HTTP client wrapper with retry logic

#### Branch 5: If Command Execution Action
- **Condition**: `action_type='command'`
- **Action**: Generate subprocess wrapper with safety checks

#### Branch 6: If Complex Conditions
- **Condition**: Multiple conditions with AND/OR logic
- **Action**: Generate condition evaluation tree (pure functions)

#### Branch 7: If FP Compliance Fails
- **Condition**: Generated code fails FP validation
- **Action**:
  1. Log specific violations
  2. Attempt auto-fix (e.g., add `frozen=True`)
  3. If unfixable: Report to user, ask for manual review
  4. Do NOT write non-compliant code

---

### Fallback

If code generation fails:
1. Log detailed error with directive config
2. Mark `implementation_status='failed'`
3. Store error in notes table
4. Prompt user: "Code generation failed. Review configuration or contact support."

---

## Examples

### ✅ Example 1: Complete Home Automation Implementation

**Input** (validated config):
```json
{
  "name": "turn_off_lights_5pm",
  "trigger": {
    "type": "time",
    "time": "17:00",
    "timezone": "America/New_York"
  },
  "action": {
    "type": "api_call",
    "url": "http://homeassistant.local:8123/api/services/light/turn_off",
    "method": "POST",
    "headers": {"Authorization": "Bearer ${HOMEASSISTANT_TOKEN}"},
    "payload": {"entity_id": "group.living_room_lights"}
  }
}
```

**Generated Files**:

`src/directives/turn_off_lights_5pm/types.py` (74 lines)
`src/directives/turn_off_lights_5pm/trigger.py` (52 lines)
`src/directives/turn_off_lights_5pm/action.py` (68 lines)
`src/directives/turn_off_lights_5pm/__init__.py` (45 lines)
`tests/directives/test_turn_off_lights_5pm.py` (120 lines)

**Total**: 359 lines of FP-compliant code generated

**AI Report**:
```
✅ Implementation Complete: turn_off_lights_5pm

Generated files in src/ (the project codebase):
- src/directives/turn_off_lights_5pm/ (4 files, 239 LOC)
- tests/directives/test_turn_off_lights_5pm.py (120 LOC)

FP Compliance: ✓ All checks passed
- Purity: 6/6 pure functions validated
- Immutability: All data structures immutable
- No OOP: No classes with mutable state
- Type Safety: Full type annotations
- Tests: 8 test cases generated

Dependencies detected:
- requests (for HTTP calls)
- returns (for Result types)
- pytest (for testing)

Project status: in_progress (awaiting user approval)

⚠️  NEXT STEPS:
1. Test the implementation (run tests, manual testing)
2. Verify behavior matches your expectations
3. When ready, say: "Approve directive turn_off_lights_5pm"
4. After approval, I'll activate it for real-time execution

Testing can take as long as needed (days, weeks) before approval.
```

---

### ✅ Example 2: AWS EC2 Scaling Implementation

**Input** (validated config):
```json
{
  "name": "scale_ec2_high_cpu",
  "trigger": {
    "type": "condition",
    "metric": "cpu_utilization",
    "threshold": 80,
    "duration_seconds": 300
  },
  "action": {
    "type": "api_call",
    "api": "aws_ec2",
    "method": "increase_instance_count",
    "params": {
      "auto_scaling_group": "web-servers",
      "increment": 2
    }
  }
}
```

**Generated Implementation**:
- Polling function (pure): Check CloudWatch metrics
- Condition evaluator (pure): Compare against threshold with time window
- AWS API wrapper (effect): boto3 Auto Scaling client
- Orchestrator: Compose polling + evaluation + scaling

**Complexity**: Higher than home automation (AWS SDK integration)

---

### ❌ Example 3: FP Compliance Failure

**Scenario**: Generated code uses global mutable state

```python
# BAD - Generated incorrectly
last_trigger_time = None  # ← Mutable global state

def should_trigger(current_time):
    global last_trigger_time  # ← Side effect
    if last_trigger_time is None:
        last_trigger_time = current_time
        return True
    # ...
```

**AI Detection**:
```
❌ FP Compliance Failed: scale_ec2_high_cpu

Violations detected:
1. fp_state_elimination: Global mutable variable 'last_trigger_time'
2. fp_purity: Function 'should_trigger' has side effects (global mutation)

Auto-fix attempted: Convert to pure function with state parameter
Status: Manual review needed

Would you like me to:
A) Generate alternative implementation
B) Show you the violations for manual fix
C) Skip this directive for now
```

---

## Edge Cases

### 1. **Missing Dependencies**
- **Scenario**: Generated code requires package not in environment
- **Handling**:
  - Detect imports needed
  - Check against `directive_dependencies` table
  - Prompt: "Install {package} before activation"

### 2. **Environment Variable Not Set**
- **Scenario**: Code references `${HOMEASSISTANT_TOKEN}` but env var not set
- **Handling**:
  - Generate code with env var access
  - Add check: "Validate env var exists before execution"
  - Prompt user: "Set {env_var} before testing"

### 3. **Large/Complex Directive**
- **Scenario**: Directive requires >500 lines of code
- **Handling**:
  - Break into multiple modules
  - Generate helper functions
  - Warn: "Complex directive - review carefully"

### 4. **Ambiguous Action**
- **Scenario**: Action config is still vague despite validation
- **Handling**:
  - Generate best-effort implementation
  - Add TODO comments in code
  - Flag for user review

### 5. **Trigger + Action Type Mismatch**
- **Scenario**: Time trigger with AWS action (unusual but valid)
- **Handling**:
  - Generate normally
  - Add note: "Unusual combination - verify intent"

### 6. **Test Generation Fails**
- **Scenario**: Cannot generate meaningful tests
- **Handling**:
  - Generate basic smoke tests
  - Add TODO for user to enhance tests
  - Warn: "Tests are minimal - please expand"

---

## Related Directives

### Pipeline Position
```
user_directive_parse
   ↓
user_directive_validate
   ↓
user_directive_implement (YOU ARE HERE)
   ↓
user_directive_approve
```

### Receives From
- **user_directive_validate**: Provides validated configuration

### Triggers After Completion
- **project_reserve_finalize**: Reserve names before writing to get IDs (automatic)
- **project_file_write**: For each generated file with embedded IDs (automatic)
- **project_update_db**: Update files, functions tables (automatic)
- **user_directive_approve**: Next step in pipeline (manual trigger by AI)

### Depends On
- **All FP directives**: Must apply purity, immutability, no OOP, etc.
  - fp_purity
  - fp_immutability
  - fp_no_oop
  - fp_type_safety
  - fp_result_types
  - fp_io_isolation
  - fp_docstring_enforcement

### Works With
- **project_file_write**: Writes generated code to disk
- **project_update_db**: Tracks generated files and functions
- **fp_compliance_check**: Validates generated code

### Escalates To (on failure)
- **user_directive_validate**: If config is actually invalid (re-validate)
- **project_error_handling**: If code generation fails unrecoverably
- **project_user_referral**: If user needs to manually write code

---

## Helper Functions

Query `get_helpers_for_directive('user_directive_implement')` to discover this
directive's helpers, and `get_helper_by_name` for a signature. They are not
listed here on purpose: the tool surface evolves, and a hardcoded list in a
rarely-read file goes stale silently and is believed anyway.

### The runtime hook surface (what you generate calls TO)

Separate from the helpers above. `get_hooks` returns AIMFP's library API for
code running **outside** AIMFP — the runner you are generating. You never invoke
these; you write code that imports them.

```python
from aimfp.hooks.directives import get_due_directives, run_directive

for d in get_due_directives(project_root=ROOT).directives:
    run_directive(d.directive_id, HANDLERS[d.name], ROOT)
```

Rules that belong in every generated runner:

- **`run_directive` is the default.** It times the handler, catches its
  exceptions, writes the log records, and updates the statistics in one call.
  Use `record_execution_start` / `record_execution_end` only when the work
  cannot be wrapped in a callable — a subprocess, a webhook, a run spanning
  processes.
- **Always call `set_next_scheduled_time`** when the runner schedules its next
  run. Without it, AIMFP cannot distinguish a directive idle by design from one
  whose runner has died, and `user_directive_monitor` can never report it
  overdue.
- **Use `record_directive_error`** for failures *outside* a run — a trigger that
  misfired, a dependency that was missing, a scheduler that could not dispatch.
- **Do not write your own logging or statistics.** `run_directive` already
  writes the JSON-lines execution and error logs and updates
  `directive_executions`. A second logger produces a per-project record shape
  that nothing can read back.
- **Tell the user** that importing `aimfp.hooks` makes `aimfp` a **runtime**
  dependency of their automation, not just a dev tool. The generated code stops
  working if `aimfp` is absent from the environment the runner executes in.

The timer belongs to the generated project and follows from the directive's
`trigger_config` — cron, a systemd timer, or an in-process scheduler. AIMFP has
no timer of its own and never supervises the runner.

## Database Operations

**Primary Method**: Use helper functions for all database operations.

**For user_directives.db**: Query available helpers. Direct SQL queries are acceptable if helpers are insufficient.

**For project.db**: ALWAYS use helpers (via project_file_write directive). Never use direct SQL for project.db.

### Tables Read
- Load validated directives where validation_status='validated' AND implementation_status IS NULL
- Load dependencies for directive_id

### Tables Updated

#### user_directives
**Use helpers** to update implementation status.
Fields: implementation_status='implemented', implemented_at (timestamp), implementation_loc, implementation_files_count

#### directive_implementations
**Use helpers** to insert implementation records.
Fields: directive_id, file_path, function_name, function_type ('trigger'/'action'/'condition'/'orchestrator'/'test'), purity_level, loc

#### project.db (via project_file_write directive)
**IMPORTANT**: Use project_file_write directive to track generated files. This directive calls the appropriate project.db helpers for:
- Files table (path, language, purpose, checksum)
- Functions table (name, file_id, purity_level, parameters_json, return_type)
- Interactions table (function dependencies)

#### notes (optional AI record-keeping)
**Use helpers for notes operations** - query available note helpers for user_directives.db.

**Note**: The notes table in user_directives.db is for flexible AI record-keeping. Useful for tracking:
- Implementation decisions and code generation choices
- FP compliance challenges and resolutions
- Dependency requirements and version selections
- Error patterns during code generation
- Optimization opportunities identified

---

## Testing

### Test 1: Generate Simple Time Trigger
```
Directive: turn_off_lights_5pm
Expected: Generate 5 files, all FP-compliant
Verify: All functions pure, immutable types, tests pass
```

### Test 2: Generate API Action
```
Directive: send_slack_notification
Expected: Generate HTTP client wrapper with Result types
Verify: Effect function isolated, error handling with Result
```

### Test 3: FP Compliance Check
```
Directive: complex_directive
Generated code has violations
Expected: AI detects violations, attempts auto-fix
Verify: Either fixes or reports to user
```

### Test 4: Handle Missing Dependency
```
Directive: aws_scaling (needs boto3)
Expected: Generate code, flag boto3 as required
Verify: Dependency added to directive_dependencies table
```

### Test 5: Complex Multi-Action Directive
```
Directive: Multiple sequential actions
Expected: Generate orchestrator that composes actions
Verify: Pure composition, proper Result type chaining
```

---

## Common Mistakes

### ❌ Mistake 1: Generating Non-FP Code
**Wrong**: Use classes with mutable state
**Right**: Pure functions + immutable data structures

### ❌ Mistake 2: Not Isolating Side Effects
**Wrong**: Mix I/O with business logic
**Right**: Separate pure logic from effect functions

### ❌ Mistake 3: Skipping Tests
**Wrong**: Generate implementation without tests
**Right**: Always generate test file

### ❌ Mistake 4: Not Validating Before Writing
**Wrong**: Write code to disk then validate
**Right**: Validate FP compliance BEFORE writing

### ❌ Mistake 5: Ignoring Dependencies
**Wrong**: Generate code that uses uninstalled packages
**Right**: Check dependencies, warn user

---

## References

- [Previous: user_directive_validate](./user_directive_validate.md)
- [Next: user_directive_approve](./user_directive_approve.md)
- [FP Directives: fp_purity](./fp_purity.md)
- [FP Directives: fp_immutability](./fp_immutability.md)
- [FP Directives: fp_no_oop](./fp_no_oop.md)
- [Project Directive: project_file_write](./project_file_write.md)
- [Helper Functions Reference](../helper-functions-reference.md#code-generation)

---

## Notes

**Code Quality**: Generated code quality is paramount. Better to fail generation than produce non-FP code.

**Token Cost**: Code generation can be expensive. Cache common patterns and use templates where possible.

**User Review**: Always assume user will review generated code. Include clear comments and documentation.
