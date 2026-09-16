# Directive: user_directive_validate

**Type**: User System
**Level**: 2
**Parent Directive**: `user_directive_parse`
**Priority**: High

---

## Purpose

Validate parsed user directives through interactive Q&A sessions to resolve ambiguities and complete missing information. This directive ensures that all directives have complete, unambiguous configurations before code generation.

The validation process:
1. Analyzes parsed directive data for ambiguities
2. Generates targeted questions for the user
3. Collects and validates user responses
4. Stores validated configuration for implementation
5. Confirms readiness for code generation

**Goal**: Transform ambiguous or incomplete directive definitions into complete, implementable specifications.

---

## When to Apply

This directive applies when:
- **Automatically after parse**: `user_directive_parse` flags directive with `validation_status: needs_validation`
- User says: "Validate my directives"
- User says: "I've answered the questions, continue"
- Detected trigger: `user_directives` table has entries with `status='parsed'` and `validation_status='needs_validation'`

**Always runs before**: `user_directive_implement` - no directive can be implemented without validation

---

## Workflow

### Trunk: Interactive Validation Process

#### Step 1: Load Directive and Ambiguities

1. **Query directive**:
   ```sql
   SELECT id, name, trigger_type, trigger_config, action_type, action_config,
          conditions_json, ambiguities_json, source_file_path
   FROM user_directives
   WHERE validation_status = 'needs_validation'
   ORDER BY parsed_at ASC
   LIMIT 1;
   ```

2. **Check the stored configs against AIMFP's grammar**:
   ```
   validate_trigger_config(trigger_type, trigger_config)
   validate_action_config(action_type, action_config)
   ```
   A config that does not conform cannot be stored, so any row that needs
   fixing must be fixed before `store_validated_directives`. On failure both
   tools return the expected shape with the errors. For `function_call` and
   `command`, `caller_resolved=true` means AIMFP has checked everything it
   can and whether the target actually resolves is still yours to verify.

3. **Load ambiguities**:
   - Parse `ambiguities_json` array
   - Prioritize by criticality:
     1. **Critical**: Missing required fields (trigger time, API endpoint)
     2. **High**: Security/credentials (API keys, auth)
     3. **Medium**: Optional parameters (retry counts, timeouts)
     4. **Low**: Preferences (notification format, logging level)

#### Step 2: Generate Questions

1. **Create question list** based on ambiguities:
   ```python
   questions = []
   for ambiguity in ambiguities:
       question = {
           "id": generate_id(),
           "field": ambiguity.field,
           "question_text": generate_question(ambiguity),
           "question_type": "text|choice|number|boolean",
           "options": [...] if choice,
           "default_value": infer_default(ambiguity),
           "validation_rules": get_rules(ambiguity)
       }
       questions.append(question)
   ```

2. **Group related questions**:
   - Group by: trigger, action, conditions, dependencies
   - Present logically: "First, let's clarify the trigger..."

#### Step 3: Interactive Q&A Session

1. **Present questions to user**:
   ```
   AI: "I need to ask {N} questions to validate '{directive_name}':"

   === TRIGGER CONFIGURATION ===
   Q1: What timezone should this use? (Default: America/New_York)
   Q2: Should this run every day or specific days?

   === ACTION CONFIGURATION ===
   Q3: What is your HomeAssistant API endpoint?
   Q4: Do you have an API token? If yes, where is it stored?
   ```

2. **Collect user responses**:
   - Validate each answer against rules
   - If invalid: Re-prompt with hint
   - If unclear: Ask follow-up question
   - Allow "skip" for optional fields

3. **Confirm understanding**:
   ```
   AI: "Let me confirm my understanding:

   Trigger: Every weekday at 5pm EST
   Action: Call HomeAssistant API at http://homeassistant.local:8123
           to turn off lights in group 'living_room_lights'
   Credentials: API token from env var HOMEASSISTANT_TOKEN

   Is this correct? (yes/no/modify)"
   ```

#### Step 4: Build Validated Configuration

1. **Merge answers with parsed data**:
   ```python
   validated_config = {
       "trigger": {
           **parsed_trigger,
           **{answer.field: answer.value for answer in trigger_answers}
       },
       "action": {
           **parsed_action,
           **{answer.field: answer.value for answer in action_answers}
       },
       "metadata": {
           "validated_at": timestamp,
           "validation_questions_count": len(questions),
           "user_modifications": modifications_list
       }
   }
   ```

2. **Store validated config**:
   ```sql
   UPDATE user_directives
   SET validated_config = ?,
       validation_status = 'validated',
       validated_at = CURRENT_TIMESTAMP
   WHERE id = ?;
   ```

#### Step 5: Identify Dependencies

1. **Extract dependencies from validated config**:
   - APIs: HomeAssistant, AWS, Twilio, etc.
   - Environment variables: API tokens, credentials
   - External packages: `requests`, `boto3`, `pyaudio`, etc.
   - Config files: credentials.json, config.yaml

2. **Store dependencies**:
   ```sql
   INSERT INTO directive_dependencies (
       directive_id,
       dependency_type,
       dependency_name,
       required,
       installation_hint
   ) VALUES (?, ?, ?, ?, ?);
   ```

3. **Check if dependencies exist**:
   - For packages: Check if installed
   - For env vars: Check if set
   - For APIs: Optionally test connectivity

4. **Report to user**:
   ```
   AI: "This directive requires:
   ✅ Python package: requests (already installed)
   ⚠️  Environment variable: HOMEASSISTANT_TOKEN (not set)
   ⚠️  API access: HomeAssistant at http://homeassistant.local:8123 (not tested)

   Should I proceed with implementation? I'll guide you through setup later."
   ```

---

### Branches

#### Branch 1: If No Ambiguities (Clean Parse)
- **Condition**: `ambiguities_json` is empty or null
- **Action**:
  1. Perform basic validation (required fields present)
  2. Ask only critical questions (security, credentials)
  3. Quick confirmation: "Your directive looks complete. Confirm to proceed?"
  4. Move to validated status immediately if confirmed

#### Branch 2: If Minimal Ambiguities (1-3 questions)
- **Condition**: Few ambiguities, all low-priority
- **Action**:
  1. Present questions concisely
  2. Offer defaults: "I'll use {default} if you skip"
  3. Quick validation session

#### Branch 3: If Extensive Ambiguities (>10 questions)
- **Condition**: Plain text parse or very incomplete directive
- **Action**:
  1. Warn user: "This directive needs significant clarification"
  2. Group questions by topic
  3. Allow saving progress: "Answer in batches if needed"
  4. Offer to start over: "Would you prefer to provide a structured file?"

#### Branch 4: If User Provides Contradictory Answers
- **Condition**: Answers conflict with each other or parsed data
- **Action**:
  1. Detect contradiction: "You said X but also Y - these conflict"
  2. Ask for clarification: "Which is correct?"
  3. Update both parsed data and validated config

#### Branch 5: If Validation Reveals Impossible Directive
- **Condition**: User wants something technically infeasible
- **Action**:
  1. Explain limitation: "This trigger type doesn't support that action"
  2. Suggest alternatives: "Would you like to try X instead?"
  3. Offer to abort: "Or we can skip this directive"

---

### Fallback

If validation cannot be completed:
1. Log incomplete validation to notes
2. Keep `validation_status = 'needs_validation'`
3. Store partial answers for resume later
4. Prompt: "Validation incomplete. We can continue later with: 'resume validating {directive_name}'"

---

## Examples

### ✅ Example 1: Time-Based Trigger Validation

**Parsed Directive** (ambiguities flagged):
```json
{
  "name": "backup_database",
  "trigger": {
    "type": "time",
    "time": "2am"  // ← Ambiguous: timezone?, every day?
  },
  "action": {
    "type": "command",
    "description": "backup production database"  // ← Ambiguous: which DB? what command?
  }
}
```

**AI Q&A Session**:
```
AI: "I need to clarify your 'backup_database' directive. I have 5 questions:

=== TRIGGER ===
Q1: What timezone for '2am'?
    Options: America/New_York, America/Los_Angeles, UTC, [other]

User: "America/New_York"

Q2: Run every day or specific days?
    Options: Every day, Weekdays only, Custom schedule

User: "Every day"

=== ACTION ===
Q3: Which database should be backed up?
    (e.g., PostgreSQL at localhost:5432, MySQL prod_db)

User: "PostgreSQL at localhost:5432, database name: production"

Q4: What backup tool should I use?
    Options: pg_dump, custom script, [other]

User: "pg_dump"

Q5: Where should backups be stored?

User: "/backups/database/ with filename format backup_YYYYMMDD.sql"

AI: "Perfect! Let me confirm:

Trigger: Daily at 2:00 AM EST
Action: Run pg_dump on PostgreSQL (localhost:5432/production)
        Save to /backups/database/backup_YYYYMMDD.sql

Dependencies needed:
- PostgreSQL client tools (pg_dump)
- Write access to /backups/database/
- Database credentials (will ask during implementation)

Proceed with implementation? (yes/no)"
```

---

### ✅ Example 2: API-Based Action Validation

**Parsed Directive**:
```yaml
name: alert_high_cpu
trigger:
  type: condition
  metric: cpu_usage
  threshold: 80
action:
  type: notification
  message: "High CPU alert"
```

**AI Q&A Session**:
```
AI: "Validating 'alert_high_cpu'. I have 4 questions:

Q1: How often should I check CPU usage?
    (e.g., every 60 seconds)

User: "Every 30 seconds"

Q2: How long must CPU stay above 80% to trigger?
    (To avoid false alarms)

User: "At least 2 minutes"

Q3: How should I send the notification?
    Options: Email, SMS, Slack, Discord, PagerDuty, [other]

User: "Slack"

Q4: What's your Slack webhook URL or API token location?

User: "Environment variable SLACK_WEBHOOK_URL"

AI: "Validated! Here's what will happen:

Trigger: Check CPU every 30 seconds
         Alert if above 80% for 2+ minutes
Action: Send Slack notification: 'High CPU alert'

Dependency: SLACK_WEBHOOK_URL environment variable

Ready to implement? (yes/no)"
```

---

### ⚠️ Example 3: Contradictory Responses

**Scenario**: User provides conflicting information

```
AI: "Q1: Should this run on weekdays or weekends?"
User: "Weekdays"

AI: "Q2: Should this run on Saturdays?"
User: "Yes"

AI: "⚠️  Contradiction detected:
    You said 'weekdays' but also 'yes to Saturdays'

    Clarification needed: Which is correct?
    A) Monday-Friday only (no Saturday)
    B) Monday-Saturday (include Saturday)
    C) Custom schedule"

User: "B - include Saturday"

AI: "Updated: Monday through Saturday. Proceeding..."
```

---

### ❌ Example 4: Validation Failure - Impossible Request

**Parsed Directive**:
```yaml
name: instant_response
trigger:
  type: api_webhook
action:
  type: machine_learning_inference
  model: gpt-4
  max_latency_ms: 10  # ← Impossible
```

**AI Response**:
```
AI: "❌ Validation Issue:

Your directive requests GPT-4 inference within 10ms, but typical
API latency for GPT-4 is 500-2000ms.

Options:
A) Remove latency requirement (I'll respond as fast as possible)
B) Use a local, faster model (e.g., distilbert, ~50ms)
C) Redesign directive with async response

What would you prefer?"
```

---

## Edge Cases

### 1. **User Skips Optional Questions**
- **Scenario**: User types "skip" or leaves answer blank
- **Handling**:
  - Use inferred default value
  - Mark field as "defaulted" in metadata
  - Warn if default might not match intent

### 2. **User Requests to Modify Already-Answered Questions**
- **Scenario**: User realizes earlier answer was wrong
- **Handling**:
  - Allow going back: "Which question do you want to modify?"
  - Re-validate downstream questions that depend on it
  - Update validated_config

### 3. **Multi-Session Validation**
- **Scenario**: User needs to leave mid-validation
- **Handling**:
  - Save progress to database
  - Store: `partial_validation_json`, `last_question_id`
  - Resume later: "Continuing from Q7..."

### 4. **User Provides Invalid Format**
- **Scenario**: User enters text when number expected
- **Handling**:
  - Validate using `validation_rules`
  - Re-prompt: "Please enter a number (e.g., 60)"
  - Offer examples

### 5. **API Credentials in Answers**
- **Scenario**: User pastes API key directly in answer
- **Handling**:
  - **NEVER store directly in database**
  - Detect credential pattern: `sk-...`, `AIza...`, etc.
  - Recommend: "Please store in environment variable instead"
  - Prompt: "Enter env var name where this is stored"

### 6. **Validation Cascades (Dependencies)**
- **Scenario**: One answer makes other questions irrelevant
- **Handling**:
  - Skip downstream questions: "Since you chose X, I don't need to ask Y"
  - Mark skipped questions as "not applicable"

### 7. **User Wants to Abort**
- **Scenario**: User realizes directive is too complex or wrong
- **Handling**:
  - Allow abort: "Type 'cancel' to abort"
  - Update status: `status='cancelled'`
  - Don't delete: User might return to it later

---

## Related Directives

### Pipeline Position
```
user_directive_parse
   ↓
user_directive_validate (YOU ARE HERE)
   ↓
user_directive_implement
```

### Receives From
- **user_directive_parse**: Provides parsed directive with ambiguities

### Triggers After Completion
- **user_directive_implement**: Automatically called when validation_status='validated'
  - AI should say: "Validation complete! Now generating implementation code..."

### Depends On
- **user_directive_parse**: Must run first to create directive entry
- **aimfp_run**: Entry point that routes to validation

### Works With
- **project_notes_log**: Log validation questions and answers for audit trail
- **user_preferences_sync**: May load user preferences for default values

### Escalates To (on failure)
- **user_directive_parse**: If validation reveals parse was incorrect, re-parse
- **project_user_referral**: If user needs documentation on directive format

---

## Helper Functions

Query `get_helpers_for_directive()` to discover this directive's available helpers.
See system prompt for usage.

---

## Database Operations

**Primary Method**: Use helper functions for all database operations. Query available helpers for user_directives.db operations.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

### Tables Read
Load directives where validation_status = 'needs_validation'

### Tables Updated

#### user_directives
**Use helpers** to update validation status.
Fields to update: validated_config (JSON), validation_status='validated', validated_at (timestamp), validation_questions_count, validation_answers_json (JSON audit log)

#### directive_dependencies
**Use helpers** to insert dependency records.
Fields: directive_id, dependency_type ('api'/'package'/'env_var'/'file'), dependency_name, required (boolean), installation_hint, availability_status

#### notes (optional AI record-keeping)
**Use helpers for notes operations** - query available note helpers for user_directives.db.

**Note**: The notes table in user_directives.db is for flexible AI record-keeping. Useful for tracking:
- Q&A session transcripts and user answers
- Ambiguities identified and resolutions chosen
- Validation decisions and reasoning
- Edge cases and special handling requirements
- User preferences revealed during validation

---

## Testing

### Test 1: Validate Simple Directive (Minimal Ambiguities)
```
User: "Validate my lights directive"

Expected:
1. Load directive with 2-3 ambiguities
2. Ask questions
3. User answers
4. Confirm understanding
5. Update to 'validated' status
6. Report: "Validation complete! Ready to implement."
```

### Test 2: Validate Complex Directive (Many Ambiguities)
```
User: "Validate automation.txt directive"

Expected:
1. Load directive with 12 ambiguities
2. Group questions by topic
3. Present progressively
4. Allow multi-session if needed
5. Store partial progress
```

### Test 3: Handle Contradiction
```
During validation:
User provides contradictory answers

Expected:
1. Detect contradiction
2. Present both answers to user
3. Ask for clarification
4. Update configuration
5. Re-validate dependent fields
```

### Test 4: Detect Missing Dependency
```
User specifies API action but no credentials

Expected:
1. Extract API dependency
2. Check for credentials
3. Flag as missing
4. Prompt: "Where should I get credentials?"
5. Store env var reference
```

### Test 5: Abort Validation
```
User: "cancel"

Expected:
1. Stop Q&A session
2. Update status to 'cancelled'
3. Preserve partial answers
4. Confirm: "Validation cancelled. Resume later with 'validate {name}'"
```

---

## Common Mistakes

### ❌ Mistake 1: Storing Credentials Directly
**Wrong**: Store API keys in validated_config
**Right**: Store environment variable names or file paths

### ❌ Mistake 2: Not Confirming Understanding
**Wrong**: Move to implementation immediately after Q&A
**Right**: Always summarize and confirm with user first

### ❌ Mistake 3: Skipping Dependency Check
**Wrong**: Assume all dependencies are available
**Right**: Extract, check, and report dependency status

### ❌ Mistake 4: Not Grouping Questions
**Wrong**: Ask all questions in random order
**Right**: Group logically (trigger, action, conditions)

### ❌ Mistake 5: Ignoring Invalid Answers
**Wrong**: Accept any answer format
**Right**: Validate, re-prompt if invalid

---

## References

- [Previous: user_directive_parse](./user_directive_parse.md)
- [Next: user_directive_implement](./user_directive_implement.md)

---

## Notes

**Security Priority**: Never store credentials in database. Always use environment variables or secure credential stores.

**User Experience**: Keep Q&A sessions conversational and logical. Group related questions and explain why you're asking.

**Validation Quality**: The better the validation, the cleaner the implementation. Invest time here to avoid issues later.
