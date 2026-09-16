# Directive: user_directive_parse

**Type**: User System
**Level**: 1 (Entry point for user directive automation)
**Parent Directive**: `aimfp_run`
**Priority**: High

---

## Purpose

Parse user-defined directive files (YAML, JSON, or plain text) and extract structured directive definitions for automation projects. This is the **entry point** for the entire user directive automation pipeline (Use Case 2).

User directive automation allows users to define domain-specific rules like:
- **Home automation**: "Turn off lights at 5pm", "Alert if stove on > 20 min"
- **Cloud infrastructure**: "Scale EC2 when CPU > 80%", "Backup RDS nightly"
- **Custom workflows**: "Every Monday generate report", "Process uploaded files automatically"

This directive reads directive files from the user's project, detects their format, extracts structured data, and stores it in `user_directives.db` for validation.

---

## When to Apply

This directive applies when:
- User says: "Parse my directive file at {path}"
- User says: "Add automation from {file}"
- User creates a new directive file and asks AI to process it
- User says: "Set up home automation" / "Configure cloud automation" (AI should ask for directive file location)
- Detected trigger: New `.yaml`, `.json`, or `.txt` files in expected directive source locations

**Context**: This directive is ONLY for Use Case 2 (automation projects). Do NOT use for regular software development projects (Use Case 1).

---

## Workflow

### Trunk: Parse User Directive File

The main workflow follows these steps:

#### Step 1: Validate Context
1. **Check project type**
   - Query: `SELECT user_directives_status FROM project`
   - If `NULL`: This is a Use Case 1 project → ERROR: User directives not applicable
   - If `in_progress`, `active`, or `disabled`: This is a Use Case 2 project → Proceed
   - If user hasn't initialized: Prompt to initialize with `project_init` for automation project

2. **Verify file path**
   - Check file exists at provided path
   - If not found: Prompt user for correct path
   - Recommended location: `directives/` folder in project root (user's choice)

#### Step 2: Detect File Format
1. **Read file content**
   - AI reads file directly at provided path

2. **Detect format**:
   - `.yaml` or `.yml` extension → YAML format
   - `.json` extension → JSON format
   - `.txt` extension → Plain text (requires NLP parsing)
   - No extension or mixed → Attempt auto-detection:
     - Try JSON parse first
     - Try YAML parse second
     - Fall back to plain text

#### Step 3: Extract Structured Data
1. **For YAML/JSON**:
   - Parse to dictionary structure
   - Expected schema:
     ```yaml
     directives:
       - name: directive_name
         trigger:
           type: time|event|api|condition
           # ... trigger-specific fields
         action:
           type: api_call|command|notification|state_change
           # ... action-specific fields
         conditions: # optional
           - field: value
     ```

2. **For Plain Text**:
   - AI uses natural language processing to parse text
   - Extract: trigger pattern, action description, conditions
   - Store as semi-structured dict with `needs_validation: true` flag

#### Step 4: Store in Database
1. **Insert into user_directives table**: Use helpers to insert parsed directive data (name, trigger_type/config, action_type/config, conditions, status='parsed', source_file reference)

2. **Track source file**: Use helpers to insert/update source file tracking (file_path, format, checksum, parse timestamp)

#### Step 5: Identify Ambiguities
1. **Check for required fields**:
   - Missing trigger details?
   - Vague action descriptions?
   - Undefined conditions?

2. **Flag ambiguities**:
   ```sql
   UPDATE user_directives
   SET validation_status = 'needs_validation',
       ambiguities_json = ?
   WHERE id = ?;
   ```

---

### Branches

#### Branch 1: If YAML/JSON Parse Successful
- **Condition**: Format is YAML or JSON and parsing succeeds
- **Action**:
  1. Extract all directives from file (may contain multiple)
  2. Store each directive in database
  3. Generate ambiguity report for each
  4. Return success with directive IDs

#### Branch 2: If Plain Text Format
- **Condition**: Format is plain text or parsing failed
- **Action**:
  1. Use NLP parsing (more ambiguity expected)
  2. Store with `needs_validation: true` flag
  3. Generate extensive ambiguity list
  4. Warn user: "Plain text format requires more validation Q&A"

#### Branch 3: If Parse Fails Completely
- **Condition**: All parsing attempts fail
- **Action**:
  1. Log error to notes table
  2. Prompt user: "Could not parse file. Please verify format or provide example."
  3. Suggest: "Would you like me to generate a template?"

#### Branch 4: If Multiple Directives in File
- **Condition**: File contains array of directives
- **Action**:
  1. Parse each directive separately
  2. Store each with unique ID
  3. Link all to same source file
  4. Report: "Found {N} directives in file"

---

### Fallback

If no branches match or unexpected error:
1. Log detailed error to `notes` table with `severity: error`
2. Prompt user with specific error message
3. Ask: "Would you like me to help you create a properly formatted directive file?"
4. Offer to call `user_directive_template_generate` (if available)

---

## Examples

### ✅ Compliant YAML Directive

**File**: `directives/lights.yaml`
```yaml
directives:
  - name: turn_off_living_room_lights_5pm
    trigger:
      type: time
      time: "17:00"
      timezone: America/New_York
      days: [monday, tuesday, wednesday, thursday, friday]
    action:
      type: api_call
      api: homeassistant
      endpoint: /services/light/turn_off
      params:
        entity_id: group.living_room_lights
    conditions:
      - field: sun_state
        operator: below_horizon
```

**Parsing Result**: Clean extraction, minimal ambiguities (may ask: "What if HomeAssistant API is unavailable?")

---

### ✅ Compliant JSON Directive

**File**: `directives/ec2-scaling.json`
```json
{
  "directives": [
    {
      "name": "scale_ec2_high_cpu",
      "trigger": {
        "type": "condition",
        "metric": "cpu_utilization",
        "threshold": 80,
        "duration_seconds": 300,
        "check_interval_seconds": 60
      },
      "action": {
        "type": "api_call",
        "api": "aws_ec2",
        "method": "increase_instance_count",
        "params": {
          "auto_scaling_group": "web-servers",
          "increment": 2,
          "max_instances": 10
        }
      }
    }
  ]
}
```

**Parsing Result**: Clean extraction, potential ambiguities: "What are AWS credentials?", "What region?"

---

### ⚠️ Plain Text Directive (More Ambiguity)

**File**: `directives/backup.txt`
```
Every night at 2am, backup the production database to S3.
If backup fails, send email to admin@example.com.
Keep backups for 30 days then delete.
```

**Parsing Result**:
- **Trigger**: Detected as `type: time`, `time: 02:00`, but timezone unknown
- **Action**: Detected as `type: command`, but exact command unknown
- **Ambiguities**:
  - Which database?
  - Which S3 bucket?
  - What backup tool?
  - What timezone?
  - Email service configuration?

**Status**: `needs_validation: true`, many questions generated for `user_directive_validate`

---

### ❌ Invalid Directive File

**File**: `directives/broken.yaml`
```yaml
directives:
  - name: turn_on_lights
    # Missing trigger!
    action:
      type: api_call
      # Missing required fields
```

**Error Result**:
```
Parse Warning: Directive 'turn_on_lights' missing required field 'trigger'.
Status: partial_parse
Ambiguities: ["No trigger defined - when should this run?", "Action missing endpoint details"]
```

**Next Step**: User prompted to fix file or proceed to validation with warnings.

---

## Edge Cases

### 1. **Empty Directive File**
- **Scenario**: File exists but contains no directives
- **Handling**:
  - Return warning: "File parsed but contains no directives"
  - Do not create database entries
  - Ask: "Would you like to add directives to this file?"

### 2. **Duplicate Directive Names**
- **Scenario**: Multiple directives with same name in file or across files
- **Handling**:
  - Auto-append suffix: `directive_name_2`, `directive_name_3`
  - Warn user: "Found duplicate name, renamed to {new_name}"
  - Log to notes

### 3. **Nested/Complex Structures**
- **Scenario**: Directives with deeply nested conditions or multiple actions
- **Handling**:
  - Parse hierarchically
  - Store as JSON in `trigger_config` and `action_config` fields
  - Both columns have an ENFORCED GRAMMAR. Call `validate_trigger_config` and
    `validate_action_config` before writing: `add_user_custom_entry` refuses a
    non-conforming config, and on failure both tools return the expected shape
    alongside the field-level errors, so there is nothing to guess at.
  - Flag for thorough validation

### 4. **File Modified During Parsing**
- **Scenario**: User edits file while parsing is in progress
- **Handling**:
  - Calculate checksum at start
  - Verify checksum at end
  - If mismatch: Abort and ask user to retry

### 5. **Large Files (>100 directives)**
- **Scenario**: File contains many directives
- **Handling**:
  - Parse in batches
  - Show progress: "Parsing directive 25/150..."
  - Warn if >100: "Large file detected. Consider splitting for maintainability."

### 6. **External References**
- **Scenario**: Directive references external config files or variables
- **Handling**:
  - Detect `$VARIABLE` or `@import` patterns
  - Flag in ambiguities: "External reference detected: {reference}"
  - Store as dependency for validation phase

### 7. **Multiple File Formats in Project**
- **Scenario**: User has both YAML and JSON directive files
- **Handling**:
  - Parse each file independently
  - Track source file for each directive
  - Allow mixed formats (store format in source_files table)

---

## Related Directives

### This Directive is Part of User Directive Automation Pipeline

**Pipeline Flow**:
```
1. user_directive_parse (YOU ARE HERE)
   ↓
2. user_directive_validate → Validates parsed directives via Q&A
   ↓
3. user_directive_implement → Generates FP-compliant code
   ↓
4. user_directive_approve → User tests and approves
   ↓
5. user_directive_activate → Deploys for real-time execution
   ↓
6. user_directive_monitor → Tracks execution
```

### Triggers After Completion
- **user_directive_validate**: Automatically called if ambiguities detected
  - AI should say: "I've parsed your directive file. I need to ask {N} questions to validate the configuration."

### Depends On
- **project_init**: Project must be initialized as Use Case 2 (automation)
  - Check: `project.user_directives_status IS NOT NULL`
- **aimfp_run**: Entry point that routes to this directive

### Works With
- **project_file_write**: If directive generation creates source code files
- **project_update_db**: Tracks directive source files in project.db

### Escalates To (on failure)
- **project_error_handling**: If parsing fails unrecoverably
- **project_user_referral**: If user needs to create properly formatted directive file

---

## Helper Functions

Query `get_helpers_for_directive()` to discover this directive's available helpers.
See system prompt for usage.

---

## Database Operations

**Primary Method**: Use helper functions for all database operations. Query available helpers for user_directives.db operations.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

### Tables Updated

#### user_directives
Fields: name, trigger_type, trigger_config (JSON, grammar enforced — see `validate_trigger_config`), action_type, action_config (JSON, envelope enforced — see `validate_action_config`), conditions_json (JSON, optional), status ('parsed'), validation_status ('needs_validation' or 'ready'), ambiguities_json (JSON array), source_file_id (FK), parsed_at

#### source_files
Fields: file_path, format ('yaml'/'json'/'txt'), last_parsed_at, checksum, directive_count

#### directive_dependencies (if external resources detected)
Fields: directive_id, dependency_type ('api'/'package'/'env_var'/'config_file'), dependency_name, required

#### notes (optional AI record-keeping)

**Use helpers for notes operations** - query available note helpers for user_directives.db.

**Note**: The notes table in user_directives.db is for flexible AI record-keeping. Use at AI's discretion for tracking important information like:
- Parse results and ambiguities detected
- Validation decisions and Q&A sessions
- Implementation choices and code generation notes
- Error patterns and debugging observations
- Performance observations and optimization opportunities

---

## Testing

### How to Verify This Directive is Working

#### Test 1: Parse Valid YAML File
```bash
# User action
User: "Parse my directive file at directives/lights.yaml"

# Expected AI behavior
1. Calls user_directive_parse directive
2. Reads file content
3. Detects YAML format
4. Parses to dictionary
5. Inserts into user_directives table
6. Reports: "Successfully parsed 1 directive: 'turn_off_lights_5pm'"
7. Reports ambiguities if any
8. Calls user_directive_validate next
```

#### Test 2: Parse Plain Text File
```bash
# User action
User: "Parse directives/automation.txt"

# Expected AI behavior
1. Calls user_directive_parse directive
2. Reads file, detects plain text
3. Uses NLP parsing
4. Extracts semi-structured data
5. Inserts with needs_validation=true
6. Reports: "Parsed plain text directive. I need to ask 8 questions to clarify."
7. Calls user_directive_validate with extensive Q&A
```

#### Test 3: Parse Multiple Directives
```bash
# User action
User: "Parse directives/home_automation.yaml"

# File contains 5 directives
# Expected AI behavior
1. Parses file
2. Detects array of 5 directives
3. Inserts each separately
4. Reports: "Successfully parsed 5 directives from home_automation.yaml"
5. Shows summary of each directive
```

#### Test 4: Handle Parse Error
```bash
# User action
User: "Parse directives/broken.yaml"

# File has invalid YAML syntax
# Expected AI behavior
1. Attempts YAML parse → fails
2. Attempts JSON parse → fails
3. Falls back to plain text → partial success
4. Reports: "Could not parse as YAML/JSON. Parsed as plain text with warnings."
5. Lists specific errors
6. Asks: "Would you like me to help fix the format?"
```

#### Test 5: Wrong Project Type
```bash
# User action (in Use Case 1 project)
User: "Parse my automation directive"

# Expected AI behavior
1. Checks project.user_directives_status → NULL
2. Reports: "This project is not configured for user directive automation (Use Case 2)."
3. Asks: "Would you like to initialize this project for automation?"
```

---

## Common Mistakes

### ❌ Mistake 1: Parsing Without Context Check
**Wrong**: Parse any file user provides without checking project type
**Right**: Always verify `project.user_directives_status` first

### ❌ Mistake 2: Not Tracking Source File
**Wrong**: Insert directive without linking to source file
**Right**: Always insert into both `user_directives` AND `source_files` tables with proper FK relationship

### ❌ Mistake 3: Ignoring Ambiguities
**Wrong**: Parse and immediately try to implement
**Right**: Detect ambiguities, store them, and route to `user_directive_validate` before implementation

### ❌ Mistake 4: Not Handling Multiple Directives
**Wrong**: Assume one file = one directive
**Right**: Always check if parsed structure is array and handle each directive

### ❌ Mistake 5: Proceeding on Parse Failure
**Wrong**: Store partial/broken data
**Right**: If parse fails completely, do NOT insert into database. Prompt user for correction.

---

## References

- [Next Directive: user_directive_validate](./user_directive_validate.md)

---

## Notes

**Important**: This directive is the gateway to Use Case 2. It must be robust and handle various input formats gracefully. Plain text parsing will always have more ambiguities, which is expected and handled by the validation phase.

**User Experience Goal**: Make it easy for users to define automations in whatever format they're comfortable with, then guide them through clarification.
