# aimfp_init - Project Initialization Directive

**Type**: Project Management
**Level**: 1
**Parent**: `aimfp_run`
**Category**: Initialization

---

## Purpose

`project_init` initializes a new AIMFP project using a **two-phase approach**:

**Phase 1: Mechanical Setup (Code)** - The `aimfp_init` helper atomically creates folders, databases, and templates
**Phase 2: Intelligent Population (AI)** - AI detects language/tools, prompts user for metadata, and populates infrastructure

This separation ensures fast, reliable initialization (code handles mechanics) while leveraging AI for intelligent decisions (language detection, user interaction).

**CRITICAL: AIMFP is FP-only**. This directive scans existing code for OOP patterns and **aborts initialization** if OOP is detected. AIMFP cannot manage OOP projects.

**Use this directive when**:
- Starting a new AIMFP-managed project (empty directory or FP-compliant code)
- Converting an existing **FP-compliant** project to use AIMFP
- Initializing for automation projects (Use Case 2)

**DO NOT use when**:
- Existing codebase is OOP-based (classes, inheritance, mutable objects)
- Project uses class-based patterns

---

## When to Use

### Explicit Initialization

Keywords that trigger `project_init`:
- "initialize AIMFP", "init project", "setup AIMFP"
- "start new project", "create AIMFP project"
- "convert to AIMFP"

**IMPORTANT**: Always call `get_project_status()` first to check if already initialized!

---

## Project Structure Created

### AIMFP Project Management Directory

```
<project-root>/                      # User's project (any structure)
├── .aimfp-project/                   # AIMFP project management folder
│   ├── ProjectBlueprint.md          # High-level project overview (human & AI readable)
│   ├── project.db                   # Project state database
│   ├── user_preferences.db          # User customization database
│   ├── user_directives.db           # Optional: user-defined automation (Use Case 2 only)
│   ├── config.json                  # Project-specific AIMFP configuration
│   ├── .gitkeep                     # Ensures directory tracked in Git
│   ├── backups/                     # Automated backups
│   │   ├── project.db.backup
│   │   ├── ProjectBlueprint.md.backup
│   │   └── ProjectBlueprint.md.v{N} # Versioned backups
│   └── logs/                        # Use Case 2 only: user directive execution logs
│       ├── execution/               # 30-day execution logs
│       └── errors/                  # 90-day error logs
├── .watchdogignore                  # Project root: gitignore-style patterns the watchdog (and project_catalog) skip. Created with a self-documenting, all-commented template; tailored during discovery.
├── .git/                            # Created in Phase 1 if not present
│   └── .aimfp/                       # Optional: archived project state (legacy path)
│       ├── ProjectBlueprint.md      # Snapshot for recovery
│       └── project.db.backup
└── <user's existing project structure>  # Unchanged - AI respects existing layout
```

**IMPORTANT**: `aimfp_core.db` is NOT copied to user projects. It lives in the MCP server installation directory and is accessed via MCP tools only.

### Design Rationale

- **`.aimfp-project/` at project root**: Primary location for AIMFP project management state
- **Respects existing structure**: AI does NOT create or modify user's source code folders during init
- **Flexible code organization**: User's code may be in root, `src/`, `lib/`, `app/`, or any structure
- **`.git/.aimfp/` archive**: Optional backup/recovery mechanism (legacy compatibility)
- **ProjectBlueprint.md**: Documents user's actual project structure, language, and architecture
- **Three-database architecture per project**: project.db, user_preferences.db, and optionally user_directives.db

### Git Repository Initialization

During Phase 1 mechanical setup, the `aimfp_init` helper checks for a `.git` directory:
- **If missing AND git available**: Runs `git init` to create repository
- **If `.git` exists**: Notes the pre-existing repository
- **If git unavailable**: Continues without version control (non-blocking)

The helper returns `git_status` with one of three values:
- `'created'`: Git repository was initialized during Phase 1
- `'pre_existing'`: Git repository already existed
- `'git_unavailable'`: Git command not found or failed

**Separation of Concerns**:
- **Phase 1 (Mechanical)**: Creates `.git` directory via `git init` (if needed) — no intelligence
- **Discovery (Intelligent)**: Executes `git_init` directive for:
  - Creating/updating `.gitignore` with AIMFP exclusions
  - Making initial commit (if new repo)
  - Storing commit hash in `project.last_known_git_hash`
  - Detecting branch name and remote tracking
  - Creating collaboration tables (`work_branches`, `merge_history`)

See `git_init` directive for the intelligent Git setup workflow.

---

### Use Case Distinction

**Use Case 1: Software Development** (Managing existing or new code projects)
- Creates `.aimfp-project/` with project.db, user_preferences.db
- No logs/ directory (not needed)
- ProjectBlueprint.md documents user's project structure and goals
- AI detects and works with user's existing code organization
- For new projects: AI asks user where to create code files or uses language conventions

**Use Case 2: Automation Projects** (Home automation, cloud infrastructure, custom workflows)
- All of Use Case 1 PLUS:
- `logs/` directory for directive execution and error tracking
- `user_directives.db` created on first directive parse
- Project purpose: Implement and execute user-defined directives
- AI generates implementation code in appropriate folders (determined during directive implementation)

---

## Workflow

### Trunk: `initialize_project`

**Pre-Flight Check: Detect Project State**

AI must determine which scenario applies BEFORE calling any init helpers:

1. **Already initialized** (`.aimfp-project/` exists) → Do NOT re-initialize. Inform user and route to `aimfp_status`.
2. **New project** (empty directory or no code files) → Proceed with clean initialization.
3. **Existing FP-compliant code** (code files present, no OOP patterns) → Initialize, then route to `project_discovery` which delegates to `project_catalog` for comprehensive cataloging. Cataloging is handled by its own directive, not inline in init.
4. **Existing OOP code** (class-based patterns detected) → Reject initialization. Inform user that AIMFP is designed for FP projects only and is not a tool for refactoring OOP to FP. Recommend uninstalling the MCP server.

```python
# ALWAYS check first - never re-initialize
status = get_project_status(project_root)

if status['initialized']:
    return {
        "success": false,
        "error": "Project already initialized",
        "existing_project": status['project_name'],
        "recommendation": "Use `aimfp status` to view project state"
    }
```

**Pre-Flight Check: Scan for OOP Patterns (if existing code present)**

```python
# Check if directory has existing code (not empty or only config files)
existing_files = scan_code_files(project_root, extensions=['.py', '.js', '.ts', '.java', '.cpp', '.cs', '.rb', '.php'])

if len(existing_files) > 0:
    # Scan for OOP patterns
    oop_patterns = {
        "python": ["class .*\\(.*\\):", "self\\.", "__init__", "def .*\\(self"],
        "javascript": ["class ", "this\\.", "extends ", "constructor\\("],
        "typescript": ["class ", "this\\.", "extends ", "implements ", "interface "],
        "java": ["class ", "extends ", "implements ", "interface ", "abstract class"],
        "cpp": ["class ", "this->", "virtual ", "override"],
        "other": ["class ", "self\\.", "this\\.", "extends ", "implements "]
    }

    # Detect OOP usage (threshold: 3+ patterns across multiple files)
    oop_detected = scan_for_patterns(existing_files, oop_patterns, threshold=3)

    if oop_detected:
        return {
            "success": false,
            "error": "OOP_INCOMPATIBLE_PROJECT",
            "message": """
🛑 AIMFP Incompatible Project Detected

This directory contains existing OOP-based code. AIMFP is designed exclusively for Functional Procedural (FP) codebases.

Your options:
1. Convert this project to FP first (major refactor - use AIMFP in a separate directory to help)
2. Disable/uninstall AIMFP MCP server for this project
3. Start a new FP-compliant project in a different directory

AIMFP cannot manage OOP projects - it enforces pure functions, immutability, and no classes with methods.
            """,
            "detected_patterns": oop_detected['patterns'],
            "affected_files": oop_detected['files'],
            "recommendation": "Disable AIMFP MCP server or convert project to FP"
        }
```

---

### Phase 1: Mechanical Setup (Code)

**Helper**: `aimfp_init(project_root)`

**Executes atomically** (all-or-nothing) - Pure mechanical operations with no deep logic:

1. **Create directories**:
   ```bash
   .aimfp-project/
   .aimfp-project/backups/
   ```

2. **Initialize Git repository** (non-blocking):
   - Check if `.git` exists → set `git_status` to `'pre_existing'`
   - If missing: run `git init` → set `git_status` to `'created'`
   - If git unavailable → set `git_status` to `'git_unavailable'`, continue

3. **Copy template**:
   - Copy `ProjectBlueprint_template.md` to `.aimfp-project/ProjectBlueprint.md`
   - Write `.watchdogignore` to the project root from the built-in template (idempotent — skipped if one already exists). The template is all-commented and self-documenting; discovery activates the patterns that fit (see `project_discovery` Branch 2.8). Syntax lives in the file header and in `project_catalog`.

3. **Create project.db**:
   - Load and execute `schemas/project.sql`
   - Execute `initialization/standard_infrastructure.sql` (8 empty entries)
   - INSERT project_root into infrastructure table

4. **Create user_preferences.db**:
   - Load and execute `schemas/user_preferences.sql`
   - INSERT default tracking_settings (all disabled by default)

5. **Validate**:
   - Check all files exist
   - Check all tables created
   - Return success or error

**Result**: Complete `.aimfp-project/` structure with empty databases and template blueprint

**Note**: State database is NOT created in Phase 1. It's created by AI in Phase 2 after source directory is determined.

---

### Phase 2: Intelligent Population (AI)

**After Phase 1 completes**, AI performs intelligent setup:

**Step 1: Detect Infrastructure**

AI scans the codebase to detect:

```python
# Detect primary language
extensions = scan_file_extensions(project_root)
# .py → Python, .rs → Rust, .js → JavaScript, .go → Go

# Detect source directory
candidates = ['src', 'lib', 'app', 'pkg', 'source']
source_dir = scan_for_code_directory(project_root, candidates)
# Full path: /home/user/my-project/src

# Detect build tool
build_files = {
    'Cargo.toml': 'cargo',
    'package.json': 'npm',
    'Makefile': 'make',
    'pom.xml': 'maven',
    'build.gradle': 'gradle'
}
build_tool = detect_file_presence(project_root, build_files)

# Detect package manager
# Inferred from build_tool or language defaults

# Detect test framework
# Scan dependencies in build files

# Detect runtime version
version_files = ['.tool-versions', '.nvmrc', 'rust-toolchain.toml']
runtime_version = parse_version_files(version_files)

# Detect main branch
main_branch = git_default_branch() or 'main'
```

**Step 2: Prompt User for Metadata**

```python
# Prompt for project details
project_name = prompt_user("Project name?") or infer_from_directory()
purpose = prompt_user("Project purpose?")
goals = prompt_user("Main goals? (comma-separated)") or []

# Confirm detected values
print(f"Detected: {primary_language}, {build_tool}, {source_directory}")
confirm = prompt_user("Are these correct? (y/n)")
if not confirm:
    # Allow user corrections
```

**Step 3: Update Infrastructure Table**

Use helpers to update infrastructure with detected/confirmed values:

```python
# Update each infrastructure entry
update_infrastructure('primary_language', 'Python 3.11')
update_infrastructure('source_directory', '/home/user/my-project/src')
update_infrastructure('build_tool', 'make')
update_infrastructure('package_manager', 'pip')
update_infrastructure('test_framework', 'pytest')
update_infrastructure('runtime_version', 'Python 3.11.2')
update_infrastructure('main_branch', 'main')
```

**Step 4: Populate ProjectBlueprint.md**

Update the template blueprint with real project data:

```python
# Load template
blueprint = load_file('.aimfp-project/ProjectBlueprint.md')

# Replace placeholders
blueprint = blueprint.replace('{{PROJECT_NAME}}', project_name)
blueprint = blueprint.replace('{{PURPOSE}}', purpose)
blueprint = blueprint.replace('{{GOALS}}', format_goals(goals))
blueprint = blueprint.replace('{{LANGUAGE}}', primary_language)
blueprint = blueprint.replace('{{SOURCE_DIR}}', source_directory)

# Save updated blueprint
save_file('.aimfp-project/ProjectBlueprint.md', blueprint)
```

**Step 5: Create Initial Completion Path**

Add default completion path based on project scope:

```python
# Add initial completion path
add_completion_path(
    name="Project Setup & Core Development",
    order_index=1,
    status="in_progress",
    description="Initialize project structure and implement core functionality"
)

# AI can adjust based on user goals
```

**Step 6: Initialize State Database**

**Purpose**: Create FP-compliant infrastructure for runtime mutable variables (ALWAYS done, not optional)

**Reference**: See `docs/STATE_DB_IMPLEMENTATION_PLAN.md` for full implementation details.

```python
# AI creates state database infrastructure during Phase 2
# This is NOT a helper call — AI handles this as part of intelligent population

# Creates:
# - {source_dir}/.state/runtime.db (SQLite database)
# - {source_dir}/.state/README.md (documentation)
# - {source_dir}/.state/state_operations.py (Python CRUD template)

if result.needs_language_rewrite:
    # AI Task: Rewrite state_operations.py to project language
    print(f"⚠️ State operations file needs rewriting to {primary_language}")

    # Steps:
    # 1. Read state_operations.py to understand CRUD operations
    # 2. Identify target language's SQLite library
    # 3. Rewrite all functions: set_var, get_var, delete_var, increment_var, list_vars
    # 4. Delete state_operations.py
    # 5. Write new state_operations.{ext} in project language
    # 6. Update README.md with language-specific usage examples
```

---

### Result

After both phases complete:

- ✅ `.aimfp-project/` directory with databases
- ✅ ProjectBlueprint.md populated with real data
- ✅ Infrastructure table populated with detected values
- ✅ Initial completion path created
- ✅ State database created at `{source}/.state/` (Python CRUD template)
- ✅ If non-Python: CRUD operations file rewritten to project language by AI

**Next step**: Route to `project_discovery` for collaborative project shape definition (blueprint, completion path, milestones, themes, flows). If pre-existing FP code exists, discovery delegates to `project_catalog`.

---

## Interactions with Other Directives

### Called By

- **`aimfp_run`** - Routes initialization requests

### Calls

- **`aimfp_init`** (Phase 1) - Mechanical setup helper (includes git init)
- **Helpers** (Phase 2) - Infrastructure detection, metadata updates
- **`git_init` directive** (during `project_discovery`) - Intelligent Git setup (gitignore, initial commit, hash storage)

### Data Flow

**Note**: Phase 1 (`aimfp_init` helper) handles mechanical setup: directories, databases, templates (no state DB). Phase 2 (AI) handles intelligent population: detection, user interaction, infrastructure updates, and state database initialization (Step 9.5). State database is ALWAYS created (in Phase 2) with Python CRUD template, then AI rewrites to project language if needed.

---

```
User: "Initialize AIMFP for my calculator"
  ↓
aimfp_run → project_init
  ├─ Phase 1: aimfp_init helper (mechanical setup)
  │   ├─ Creates .aimfp-project/ structure
  │   ├─ Initializes project.db with standard_infrastructure.sql
  │   ├─ Initializes user_preferences.db
  │   └─ Copies ProjectBlueprint template
  ├─ Phase 2: AI intelligent population
  │   ├─ Detects language, build tool, source directory
  │   ├─ Prompts: "Project purpose?" → "Pure FP calculator"
  │   ├─ Updates infrastructure table with detected values
  │   ├─ Populates ProjectBlueprint.md with project data
  │   └─ Creates initial completion path
  └─ Returns success, routes to project_discovery
  ↓
project_discovery
  ├─ If existing FP code: project_catalog first
  ├─ Discuss blueprint with user
  ├─ Map infrastructure
  ├─ Define themes and flows
  ├─ Create completion path and milestones
  └─ Route to aimfp_status → project_progression (first task)
  ↓
AI presents: "✅ Project initialized. Let's define the project shape."
```

---

## Examples

### Example 1: Basic Initialization

**User**: "Initialize AIMFP for my project"

**AI Processing**:
1. Calls `get_project_status()` → Not initialized
2. Detects directory name: "matrix-calculator"
3. Prompts: "Project purpose?"
4. User: "Pure FP matrix operations library"
5. Executes `project_init` workflow

**Result**:
```
✅ Project initialized: Matrix Calculator

Created:
  • .aimfp-project/project.db (with infrastructure entries)
  • .aimfp-project/user_preferences.db
  • .aimfp-project/ProjectBlueprint.md (populated with project data)
  • Optional: {source}/.state/ (state database infrastructure)

Next: Proceeding to project discovery to define your project shape
  (blueprint, themes, flows, completion path, milestones)
```

---

### Example 2: Automation Project (Use Case 2)

**User**: "Initialize AIMFP for home automation"

**AI Processing**:
1. Recognizes "automation" keyword
2. Prompts: "Is this an automation project? (yes/no)"
3. User: "Yes"
4. Creates additional structure:
   - `.aimfp-project/logs/` for execution logs
   - Prepared for `user_directives.db` (created on first directive)

**Result**:
```
✅ Automation project initialized: Home Automation

Created:
  • .aimfp-project/project.db (with infrastructure entries)
  • .aimfp-project/user_preferences.db
  • .aimfp-project/ProjectBlueprint.md (populated with project data)
  • Optional: {source}/.state/ (state database infrastructure)
  • .aimfp-project/logs/ (for automation execution)

Next steps:
  1. Create directive files (e.g., directives/lights.yaml)
  2. Tell AI: "Parse my directive file at directives/lights.yaml"
  3. AI will generate automation code in src/
```

---

### Example 3: Converting Existing FP Project

**User**: "Convert my existing project to AIMFP"

**AI Processing**:
1. Checks `get_project_status()` → Not initialized
2. Scans existing codebase for OOP patterns → None detected ✅
3. Detects language: Python
4. Detects existing files: src/main.py, src/utils.py (all pure functions)
5. Prompts: "Import existing code into AIMFP tracking?"
6. User: "Yes"
7. Initializes + indexes existing files

**Result**:
```
✅ Project converted to AIMFP: MyApp

Scanned existing code: FP-compliant ✅

Indexed existing files:
  • src/main.py (3 pure functions)
  • src/utils.py (5 pure functions)

Databases created:
  • .aimfp-project/project.db (8 functions tracked)
  • .aimfp-project/user_preferences.db
  • .aimfp-project/ProjectBlueprint.md (generated from existing code)

Next: Run FP compliance check to identify issues
```

---

### Example 4: OOP Project Rejected

**User**: "Convert my Django project to AIMFP"

**AI Processing**:
1. Checks `get_project_status()` → Not initialized
2. Scans existing codebase for OOP patterns
3. Detects: 15+ class definitions, inheritance, self references
4. **Aborts initialization**

**Result**:
```
🛑 AIMFP Incompatible Project Detected

This directory contains OOP-based code in:
  • myapp/models.py (5 Django model classes)
  • myapp/views.py (3 class-based views)
  • myapp/serializers.py (4 serializer classes)

AIMFP is designed for Functional Procedural codebases only.

Your options:
1. Convert to FP first (major refactor - use AIMFP in separate directory)
2. Disable/uninstall AIMFP MCP server for this project
3. Start new FP-compliant project elsewhere

Initialization aborted.
```

---

## Edge Cases

### Case 1: Already Initialized

**Trigger**: `.aimfp-project/` already exists

**Response**:
```json
{
  "success": false,
  "error": "Project already initialized",
  "existing_project": "Matrix Calculator",
  "recommendation": "Use `aimfp status` to view current state"
}
```

**AI presents**:
```
⚠️ Project already initialized: Matrix Calculator
Use "status" to view current state or "continue" to resume work.
```

---

### Case 2: OOP Code Detected

**Trigger**: Existing codebase contains OOP patterns (3+ detected)

**Response**:
```json
{
  "success": false,
  "error": "OOP_INCOMPATIBLE_PROJECT",
  "message": "🛑 AIMFP Incompatible Project Detected\n\nThis directory contains existing OOP-based code. AIMFP is designed exclusively for Functional Procedural (FP) codebases.\n\nYour options:\n1. Convert this project to FP first\n2. Disable/uninstall AIMFP MCP server\n3. Start new FP project elsewhere",
  "detected_patterns": ["class definitions", "self references", "inheritance"],
  "affected_files": ["src/main.py", "src/models.py"],
  "recommendation": "Disable AIMFP MCP server or convert project to FP"
}
```

**AI presents**:
```
🛑 AIMFP Incompatible Project Detected

This directory contains OOP-based code in:
  • src/main.py (class User, class Database)
  • src/models.py (class inheritance detected)

AIMFP is designed for Functional Procedural codebases only.

Your options:
1. Convert to FP first (major refactor - use AIMFP in separate directory to help)
2. Disable/uninstall AIMFP MCP server for this project
3. Start new FP-compliant project elsewhere
```

**Rationale**: AIMFP cannot effectively manage OOP projects. The system enforces pure functions, immutability, and no classes with methods. Attempting to manage OOP code would require constant refactoring that contradicts the project's existing design.

---

### Case 3: No Write Permissions

**Trigger**: Cannot create `.aimfp-project/` directory

**Response**:
```json
{
  "success": false,
  "error": "Permission denied: Cannot create .aimfp-project/",
  "recommendation": "Check directory permissions or run with appropriate access"
}
```

---

### Case 4: Invalid Project Root

**Trigger**: project_root not provided or invalid

**Response**:
```json
{
  "success": false,
  "error": "Invalid project root path",
  "recommendation": "Provide valid project directory path"
}
```

---

## Related Directives

### Primary Relationships

- **`aimfp_run`** - Routes to this directive
- **`project_discovery`** - Next step after initialization (defines blueprint, themes, flows, completion path, milestones)
- **`project_catalog`** - Called by discovery for pre-existing FP codebases (cataloging extracted from init)
- **`get_project_status()`** - Pre-check helper

### Helper Functions

**Phase 1 (Mechanical Setup):**
- **`aimfp_init(project_root)`** — Atomically creates `.aimfp-project/` directory, databases, and templates

**Phase 2 (AI-Driven):**
- AI uses project helpers (infrastructure updates, completion path creation, etc.) as needed during intelligent population
- Query available helpers via the MCP server for current helper catalog

---

## Database Operations

**Read Operations**:
- Checks if `.aimfp-project/` exists (file system)
- Scans existing files for conversion (optional)

**Write Operations**:
- Creates `.aimfp-project/` directory
- Initializes `project.db` with schema
- Inserts project metadata
- Inserts default completion path
- Creates `user_preferences.db` with defaults
- Writes `ProjectBlueprint.md`

---

## FP Compliance

**Purity**: ⚠️ Effect function
- Has side effects (creates files/directories)
- But isolated: all effects explicit in workflow

**Immutability**: ✅ Immutable inputs
- project_info frozen after collection
- No mutation of parameters

**Side Effects**: ⚠️ Explicit
- File system writes (directory creation)
- Database initialization (DDL operations)
- All effects documented in workflow

---

## Error Handling

### Schema Load Failure

**Trigger**: Cannot load database schema files

**Response**:
```json
{
  "success": false,
  "error": "Schema file not found: schemaExampleProject.sql",
  "recommendation": "Verify AIMFP MCP server installation"
}
```

### Database Creation Failure

**Trigger**: SQLite errors during initialization

**Response**:
```json
{
  "success": false,
  "error": "Database initialization failed: disk full",
  "partial_cleanup": true,
  "recommendation": "Free disk space and retry"
}
```

---

## Best Practices

1. **Always check first** - Call `get_project_status()` before initializing
2. **Gather info upfront** - Get project name, purpose, goals from user
3. **Detect context** - Infer language, architecture from existing files
4. **Transaction safety** - Use transactions for database operations
5. **Fail gracefully** - Clean up partial initialization on errors
6. **Provide next steps** - Tell user what to do after initialization
7. **Support both use cases** - Regular dev vs automation projects

---

## Version History

- **v1.0** (2025-10-22): Initial project initialization
- **v1.1** (2025-10-24): Added ProjectBlueprint.md generation
- **v1.2** (2025-10-26): Added support for automation projects (Use Case 2)

---

## Notes

- This is a **one-time operation** per project
- Creates immutable project structure (directories don't change)
- Databases are mutable, but schema is fixed
- ProjectBlueprint.md is **living document** (updated via `project_blueprint_update`)
- For automation projects, `user_directives.db` created on first directive parse
