# project_discovery - Project Discovery Directive

**Type**: Project Management
**Level**: 1
**Parent**: `project_init`
**Category**: Initialization

---

## Purpose

`project_discovery` guides AI through a structured conversation with the user to define the full project shape after initialization. This directive bridges the gap between mechanical initialization (`project_init`) and active development work.

**What it does**:
- Executes `git_init` directive for intelligent Git setup (if git available from Phase 1)
- Collaboratively populates the project blueprint (purpose, goals, scope, constraints)
- Maps infrastructure in detail (language, tools, conventions, file organization)
- Establishes themes (logical groupings) and flows (cross-cutting workflows)
- Creates a completion path with meaningful stages
- Breaks the completion path into milestones with acceptance criteria
- For pre-existing FP codebases, delegates to `project_catalog` first

**Use this directive when**:
- `project_init` has just completed successfully (canonical flow)
- A project was initialized but discovery was skipped or interrupted
- User explicitly requests to re-define project shape

**DO NOT use when**:
- Project is not yet initialized (run `project_init` first)
- Project already has a populated completion path and milestones (discovery already done)

---

## When to Use

### Automatic Trigger

`project_init` routes here on successful completion. This is the canonical post-init flow.

### Manual Trigger

Keywords: "discover", "define project", "project shape", "plan project", "blueprint", "milestones", "completion path"

---

## Workflow

### Trunk: `assess_project_state`

Before starting the discovery conversation, assess what exists:

1. Check if pre-existing FP code was detected during init pre-flight
2. Check current state of ProjectBlueprint.md (template vs partially filled)
3. Check infrastructure table for values already detected in init Phase 2

---

### Branch 1: Delegate to project_catalog (if pre-existing code)

**Condition**: Pre-existing FP-compliant code was detected during `project_init` pre-flight scan.

**Action**: Call `project_catalog` directive to scan and register all existing code before proceeding.

**Resume**: After `project_catalog` completes, it loops back here to continue with blueprint discussion. The catalog results (file counts, function counts, suggested themes/flows) inform the discovery conversation.

---

### Branch 2: Discuss Project Blueprint

**Action**: Collaborative blueprint population with user.

This is a **deep conversation**, not a form fill. AI should ask open-ended questions, think through logic gaps, and surface hidden connections. Shallow discovery creates compounding problems during implementation.

**Steps**:
1. Review current ProjectBlueprint.md (template or partially filled from init Phase 2)
2. Discuss with user:
   - What is the project's purpose in detail?
   - What are the goals? (specific, achievable outcomes)
   - What is in scope vs out of scope?
   - Any constraints? (timeline, technology, compatibility)
3. **Second pass** — After the user describes the project, THINK through what they said:
   - What components will this need that the user didn't mention?
   - What data flows between parts? What edge cases exist?
   - What future extensibility concerns should be raised now?
   - What connections or dependencies might the user have missed?
   Present findings with concrete choices — don't just list concerns, offer options:
   "I see two approaches for X: (A) ... or (B) ... — which fits better?"
   Assume the user will miss some details. Assume you will miss some on first pass.
4. Fill out blueprint sections based on user responses
5. Update ProjectBlueprint.md via `project_blueprint_update` helper

**Key principle**: Ask, don't assume. The user knows their project better than AI — but AI should actively help them think through what they haven't considered yet.

---

### Branch 2.5: Determine Project Type (Case 1 vs Case 2)

**Condition**: During blueprint discussion, evaluate user's project description.

**Action**: Detect if user describes automation BEHAVIOR vs SOFTWARE to build.

**Detection Signals for Case 2 (Automation Behavior)**:
- User describes WHAT to automate, not software to build:
  - "Turn off lights at 5pm"
  - "Scale EC2 when CPU > 80%"
  - "Send notification when X happens"
  - "Every Monday generate a report"
  - "When the garage door opens after 10pm, alert me"
- Key phrases: "automate", "when X then Y", "schedule", "trigger", "monitor"
- User wants AI to build everything, not code together

**Detection Signals for Case 1 (Software Development)**:
- User describes SOFTWARE to build:
  - "A web server for..."
  - "A library that does..."
  - "A CLI tool for..."
  - "An automation tool" (they want to BUILD the tool, not USE automation)

**Steps**:
1. During blueprint discussion, evaluate user's project description
2. If Case 2 candidate detected, present explicit choice:
   ```
   "It sounds like you want to define automation rules and have the system
   execute them. Would you like AIMFP to:

   A) Build the automation infrastructure from directive files you provide
      (Use Case 2 — you define WHAT to automate, AI builds everything)

   B) Help you build an automation application as a software project
      (Use Case 1 — you code together with AI assistance)"
   ```
3. **On Case 2 selection**:
   - Set `project.user_directives_status = 'pending_discovery'`
   - Adapt remaining discovery branches for automation context (see below)
   - Continue with Branch 3 using automation-aware settings

**Case 2 Adaptations for Downstream Branches**:
- **Infrastructure (Branch 3)**: Default Python, note scheduler/API client needs
- **Themes (Branch 4)**: Default to automation themes:
  - Trigger Handlers, Action Executors, Scheduling, Monitoring, Error Handling
- **Completion Path (Branch 5)**: Default automation stages:
  - Directive Setup, Implementation, Testing, Activation, Monitoring
- **Milestones (Branch 6)**: Derived from user's automation goals, not software features

---

### Branch 2.7: Git Intelligent Setup

**Condition**: `git_status` from `aimfp_init` is `'created'` or `'pre_existing'`

**Action**: Execute `git_init` directive for intelligent Git configuration.

**Steps**:
1. Check `git_status` value from initialization result
2. If `'created'` or `'pre_existing'`: Execute `git_init` directive
   - `git_init` handles: `.gitignore` creation/update, initial commit (if new repo), hash storage in `project.last_known_git_hash`, branch detection, collaboration tables
3. If `'git_unavailable'`: Skip git setup, inform user that version control is unavailable and suggest installing Git

**Note**: Git setup happens early in discovery (before state database creation) because git hash storage is essential for external change detection throughout the project lifecycle.

**Resume**: After `git_init` completes, it loops back here to continue with infrastructure mapping.

---

### Branch 2.8: Tailor `.watchdogignore`

**Condition**: `.watchdogignore` exists at project root (created by `aimfp_init` Phase 1 with an all-commented template).

**Action**: Activate ignore patterns that fit this project, the same way `git_init` populates `.gitignore`. This is the intelligent counterpart to the mechanical file creation — init ships the template empty (every line commented); discovery turns on the patterns that actually apply.

**Steps**:
1. From init Phase 2 detection, identify directories/globs the watchdog should not track: test directories, generated/build output not already covered by the built-in exclusions, fixtures, vendored code.
2. Uncomment or add matching patterns in `.watchdogignore` (gitignore-style — see the file's own header for syntax; negation `!` is not supported).
3. Keep it minimal: the built-ins (`node_modules`, `__pycache__`, `.git`, `build`, `dist`, `.aimfp-project`, etc.) are already excluded and must not be re-listed.
4. Briefly confirm the activated patterns with the user — these decide what the watchdog and `project_catalog` both skip, so the two stay aligned.

**Note**: `.gitignore` and `.watchdogignore` are siblings — patterns often overlap, but they are not identical (the watchdog tracks source the user may still want in git, and vice versa). Tailor each to its own purpose.

---

### Branch 3: Map Infrastructure

**Action**: Confirm and refine infrastructure entries from init Phase 2 detection.

**Steps**:
1. Present what was auto-detected during init Phase 2 (language, build tool, source dir, etc.)
2. Discuss with user:
   - Confirm or correct detected values
   - Language choices and rationale
   - Testing strategy (framework, coverage goals)
   - Deployment targets (if known)
3. Discuss coding conventions:
   - File organization patterns
   - Naming standards
   - Module boundaries
4. Update infrastructure table with confirmed/corrected values
5. **Create state database**: Once `source_directory` is confirmed, call `create_state_database(source_directory)` to create `<source-dir>/.state/runtime.db`. This provides FP-compliant replacement for mutable global variables.
6. **Create state operations entry point**: Using the template at `src/aimfp/templates/state_db/state_operations.py` as reference, create language-appropriate state operations file in the project's source directory (e.g., `<source-dir>/.state/state_operations.{ext}`). Adapt the template to match the project's primary language.
7. **Register state files in project.db**: After creating state DB and state_operations file, register them in project.db files table via the standard reserve → finalize flow. The `.state/` directory contents are project files that should be tracked.

---

### Branch 4: Define Themes and Flows

**Action**: Establish the project's organizational structure.

**Steps**:
1. Discuss project organization with user
2. Identify **themes** — logical groupings of functionality
   - Examples: "Database Operations", "Authentication", "API Layer", "Data Processing"
   - If catalog ran, use suggested themes as starting point
3. Identify **flows** — cross-cutting workflows and processes
   - Examples: "Request Handling Flow", "Data Pipeline Flow", "Build & Deploy Flow"
   - If catalog ran, use suggested flows as starting point
4. Create theme and flow entries in database via `create_theme`, `create_flow` helpers
5. Update blueprint section 3 (Project Themes & Flows)

**Note**: Themes and flows are initial definitions, not final. They evolve as the project progresses via `project_evolution`.

---

### Branch 5: Create Completion Path

**Action**: Define the project's major stages from start to finish.

**Steps**:
1. Discuss major project phases with user
   - What are the stages from where you are now to "done"?
   - What does each stage achieve?
2. Replace the default completion path placeholder (from init) with user-informed path
3. Each stage should have:
   - Name
   - Description
   - Entry criteria (what must be true to start this stage)
   - Exit criteria (what must be true to consider this stage done)
4. Update `completion_path` table

**Example stages** (AI can suggest, user decides):
- Foundation & Setup
- Core Implementation
- Integration & Testing
- Polish & Documentation
- Release Preparation

**Note**: Stages are high-level. Milestones provide the detail within each stage.

---

### Branch 6: Create Milestones

**Action**: Break stages into concrete milestones.

**Steps**:
1. For each stage in the completion path, discuss concrete milestones with user
2. Create milestone entries with:
   - Name (descriptive, action-oriented)
   - Description (what this milestone achieves)
   - Acceptance criteria (how to know it's done)
   - `order_index` (sequence within stage)
3. **Do NOT create tasks yet** — tasks are created incrementally during `project_progression`
4. Set first milestone of first stage to status `in_progress`
5. Update `milestones` table

**Key principle**: Milestones should be achievable and verifiable, not vague. "Implement user authentication" is good. "Make progress on backend" is not.

---

### Branch 6.5: Create Post-Completion Paths

**Action**: Create two default post-completion development paths at the tail end of the completion path ordering.

**Steps**:
1. After all user-defined completion paths and milestones are created, add two default post-completion paths:
   - **"Added Features"**: `add_completion_path(name='Added Features', status='completed', description='Post-completion path for new features and enhancements. Reopen when new feature work is needed.', order_index=998)`
   - **"Updates"**: `add_completion_path(name='Updates', status='completed', description='Post-completion path for bug fixes, patches, and maintenance updates. Reopen when update work is needed.', order_index=999)`
2. Both paths are marked `completed` by default — they exist as ready-to-reopen containers for future development cycles
3. No milestones or tasks are created for these paths — they are populated on-demand when a path is reopened
4. Log creation: `add_note(note_type='evolution', directive_name='project_discovery', content='Created default post-completion paths: Added Features (order 998) and Updates (order 999), both marked completed by default')`

**Why these exist**: After project completion, new features or bug fixes may arise. Instead of working outside the system, the appropriate path is reopened (status changed to `in_progress`), which automatically reverts project completion status. Milestones, tasks, and items are then created on-demand within the reopened path, following the same progression rules as regular development. When the work is done, the path is completed again and the project returns to complete status.

**Note**: These paths use high `order_index` values (998, 999) so they always appear at the end, after all user-defined stages.

---

### Branch 7: Finalize Discovery

**Action**: Wrap up and prepare for active work.

**Steps**:
1. Backup blueprint to `.aimfp-project/backups/`
2. Log discovery completion in notes (`source=directive`, `directive_name=project_discovery`)
3. Present summary to user:
   - Project shape overview
   - Stages and milestones
   - Themes and flows
   - Post-completion paths (Added Features, Updates) ready for future use
   - First milestone is open
4. Inform user: "Project shape defined. First milestone is open. Ready to begin work."
5. Flow to `aimfp_status` — status routes to `project_progression` for first task creation

---

### Branch 7.5: Case 2 Onboarding (if Case 2 selected)

**Condition**: `project.user_directives_status = 'pending_discovery'` (set in Branch 2.5)

**Action**: Complete Case 2 setup and begin directive discussion with user.

**Steps**:
1. Update `project.user_directives_status = 'pending_parse'`
2. Begin conversational onboarding:
   ```
   "Project configured for automation. Now let's set up your directives.

   Do you already have directive files written?
   - If yes: Tell me where they are (e.g., 'directives/lights.yaml')
   - If no: Describe what you want to automate and I'll help you write them

   Supported formats: YAML, JSON, or plain text descriptions.
   I'll review your directives with you to make sure I understand exactly
   what you want before we start building."
   ```
3. **If user has files**:
   - Ask for file path
   - Read and review the file with user
   - Discuss any ambiguities or improvements
   - Route to `user_directive_parse` when ready
4. **If user needs help creating files**:
   - Discuss what they want to automate
   - Help them structure their requirements
   - Collaboratively create directive file(s)
   - Then route to `user_directive_parse`
5. Route to `aimfp_status` when directive discussion complete

**Key principle**: This is a **conversation**, not automatic processing. AI must understand user's intent fully before adding anything to the database. The raw user file is a starting point, not something to store verbatim.

---

### Fallback

Ask user for missing information needed to define the project shape. If user wants to skip a section, allow it but note the gap.

---

## Interactions with Other Directives

### Called By

- **`project_init`** — Canonical flow after successful initialization
- **`aimfp_status`** — Routes here if discovery not yet complete (priority 95)

### Calls

- **`project_catalog`** — Delegates to catalog for pre-existing FP codebases
- **`project_blueprint_update`** — Updates ProjectBlueprint.md
- **Helpers**: `create_theme`, `create_flow`, `add_completion_path`, `add_milestone`, `update_infrastructure_entry`, `project_notes_log`, `create_state_database`, `add_note`

### Flows To

- **`aimfp_status`** — On completion, status routes to `project_progression` for first task

---

## Edge Cases

### Case 1: User Wants to Skip Discovery

**Response**: Allow skip but warn:
```
⚠️ Skipping discovery means default completion path and no milestones.
You can run project_discovery later to define the project shape.
```
Log skip in notes. Route to `aimfp_status`.

### Case 2: Pre-existing Code Too Large to Catalog

`project_catalog` handles batching internally. Discovery waits for catalog completion before proceeding with the conversation.

### Case 3: User Unsure About Scope

Help user think through goals by asking about:
- Target users / audience
- Key features (top 3-5)
- What does "done" look like?
- Any hard constraints?

Start broad, then narrow.

### Case 4: Discovery Interrupted

If discovery is interrupted mid-conversation, the partially completed state persists in the database. On next session, `aimfp_status` detects discovery is incomplete and routes back here with existing progress.

---

## Database Operations

**Read Operations**:
- Infrastructure table (values from init Phase 2)
- ProjectBlueprint.md (current state)
- Catalog results (if `project_catalog` ran)

**Write Operations**:
- ProjectBlueprint.md (populated with real data)
- Themes table (initial themes)
- Flows table (initial flows)
- Completion path table (stages)
- Milestones table (milestones with acceptance criteria)
- Notes table (discovery log entries)
- Infrastructure table (confirmed/corrected values)

---

## FP Compliance

**Purity**: ⚠️ Effect function — writes to database and files
**Immutability**: ✅ Immutable inputs — user responses collected, not mutated
**Side Effects**: ⚠️ Explicit — all DB writes via helpers, file writes via blueprint_update

---

## Best Practices

1. **Ask, don't assume** — User knows their project better than AI
2. **Build on what exists** — Use catalog results and init detection as conversation starters
3. **Milestones should be verifiable** — Clear acceptance criteria, not vague goals
4. **Don't create tasks** — Tasks come later via `project_progression`
5. **Themes and flows evolve** — These are initial definitions, set expectations with user
6. **Log decisions** — Use `project_notes_log` for important decisions made during discovery

---

## Version History

- **v1.2** (2026-05-23): Added Branch 2.8 — tailor `.watchdogignore` (activate fitting ignore patterns), the intelligent counterpart to the all-commented template `aimfp_init` writes mechanically, mirroring how `git_init` populates `.gitignore`
- **v1.1** (2026-03-09): Added Branch 6.5 — post-completion paths ("Added Features" and "Updates") created during discovery as completed-by-default containers for future development cycles
- **v1.0** (2026-01-30): Initial creation — bridges gap between init and active development

---

## Notes

- Discovery is a **one-time operation** per project (but can be re-run if needed)
- Replaces the implicit init-to-task-decomposition gap that previously existed
- Catalog is optional — only triggered for pre-existing codebases
- The completion path created here is the project's roadmap, but it can evolve via `project_evolution` and `project_progression`
- Two post-completion paths ("Added Features" and "Updates") are created as completed-by-default containers for future development cycles
