# project_catalog - Project Cataloging Directive

**Type**: Project Management
**Level**: 1
**Parent**: `project_discovery`
**Category**: Initialization

---

## Purpose

`project_catalog` comprehensively catalogs an existing FP-compliant codebase into `project.db`. Scans all source files, identifies functions with metadata, maps interactions between them, and infers organizational structure (themes and flows).

**What it does**:
- Scans all source files in the project's source directory
- Registers every file in the `files` table
- Identifies and registers every function in the `functions` table
- Maps function-to-function and file-to-file interactions
- Infers themes and flows from code organization
- Reports purity assessment (pure / side effects / uncertain)

**Use this directive when**:
- Called by `project_discovery` when pre-existing FP code is detected
- User explicitly requests re-cataloging after major refactors

**DO NOT use when**:
- Project is a new empty directory (nothing to catalog)
- Project has OOP code (should have been rejected by `project_init`)
- `project_init` Phase 1 has not completed (needs database first)

---

## When to Use

### Automatic Trigger

`project_discovery` delegates here when pre-existing FP-compliant code was detected during `project_init` pre-flight scan.

### Manual Trigger

Keywords: "catalog", "scan codebase", "register existing code", "index project", "scan files", "import existing"

---

## Workflow

### Trunk: `prepare_catalog_scan`

Verify prerequisites:
1. `.aimfp-project/project.db` exists (Phase 1 complete)
2. `source_directory` is set in infrastructure table
3. If source directory not set, prompt user to provide it

---

### Branch 1: Scan All Source Files

**Action**: Build a complete file inventory.

**Steps**:
1. Read `source_directory` from infrastructure table
2. Read `primary_language` from infrastructure table (for pattern selection)
3. Recursively scan for source files matching language-appropriate extensions
4. Also include other recognized source file types (config, data, scripts)
5. Exclude build artifacts, dependencies, generated files:
   - Use same exclusion patterns as watchdog `config.py` for consistency
   - Excluded dirs: `node_modules`, `venv`, `__pycache__`, `.git`, `build`, `dist`, `.aimfp-project`, etc.
   - Excluded extensions: `.pyc`, `.so`, `.dll`, `.lock`, `.log`, images, fonts, archives
   - Also honor the project-root `.watchdogignore` if present (same file the watchdog reads): skip any file matching its gitignore-style patterns, so the catalog never registers files the watchdog will ignore. Patterns containing `/` are anchored to the project root and match that subtree (`packages/host/extension/`); patterns without `/` match a filename or any directory component anywhere (`tests`, `*_test.py`, `*.test.ts`). `#` comments and blank lines are ignored; negation (`!`) is not supported.
6. Build file inventory: path, size, last_modified, extension, language

**Consistency note**: Exclusion patterns are shared with the watchdog module — both use the same built-in base set from `config.py` plus any user patterns in the project-root `.watchdogignore`. Keeping the catalog and the watchdog aligned prevents the watchdog from re-flagging files the catalog deliberately skipped (and vice versa).

---

### Branch 2: Register Files in Database

**Action**: Create file entries in `project.db`.

**Steps**:
1. For each source file, use `reserve_file` helper to get database ID
2. Set file metadata:
   - `path`: relative path from project root
   - `purpose`: infer from filename and directory location
   - `theme`: infer from directory structure (tentative — confirmed during discovery)
3. Use `finalize_file` helper to confirm registration
4. For large codebases (>100 files): process in batches of 20-50, report progress to user between batches

**Helpers**: `reserve_file`, `finalize_file`

---

### Branch 3: Scan Functions Per File

**Action**: Identify and register all functions in each file.

**Steps**:
1. For each registered file, parse function definitions
2. Use language-appropriate patterns (same patterns as watchdog `analyzers.py`):
   - Python: `def function_name(`
   - JavaScript/TypeScript: `function name(`, `const name = (`, arrow functions
   - Rust: `fn name(`
   - Go: `func name(`
   - Java: access modifier + return type + name
3. For each function, identify:
   - **Name**: function identifier
   - **Parameters**: parameter list (types if available)
   - **Return type**: if typed language or type hints present
   - **Purity assessment**:
     - `pure`: No side effects detected, deterministic based on visible logic
     - `side_effects`: Contains I/O, mutations, or external calls not wrapped
     - `uncertain`: Cannot determine from static analysis alone
   - **Purpose**: infer from name and docstring (if present)
4. Use `reserve_function` and `finalize_function` helpers to register
5. Flag impure functions with a note — informational, not a blocker

**Helpers**: `reserve_function`, `finalize_function`

---

### Branch 4: Map Interactions

**Action**: Build the project's dependency graph.

**Steps**:
1. For each function, identify which other project functions it calls
2. For each file, identify which other project files it imports from
3. Identify external library dependencies per file
4. Create interaction entries via `create_interaction` helper
5. Build a dependency understanding for the project

**Helpers**: `create_interaction`

---

### Branch 5: Infer Themes and Flows

**Action**: Suggest organizational structure from existing code patterns.

**Steps**:
1. Analyze file/directory structure for logical groupings → suggest themes
   - Files in same directory often share a theme
   - Naming patterns can indicate themes (e.g., `auth_*.py`, `db_*.py`)
2. Analyze call patterns and data flow for cross-cutting workflows → suggest flows
   - Entry points that chain through multiple files suggest a flow
   - Common utilities called by many files may indicate shared infrastructure
3. Present inferred themes and flows to user for confirmation or adjustment
4. These feed into `project_discovery`'s theme/flow definition step (discovery finalizes)

**Note**: Catalog **suggests**; discovery **confirms**. User has final say on themes and flows.

---

### Branch 6: Report Catalog Summary

**Action**: Summarize results and return to discovery.

**Report includes**:
- Total files registered
- Total functions registered
- Total interactions mapped
- Purity assessment summary: N pure, N side effects, N uncertain
- Suggested themes (pending user confirmation in discovery)
- Suggested flows (pending user confirmation in discovery)

**Steps**:
1. Log catalog completion in notes (`source=directive`, `directive_name=project_catalog`)
2. Return to `project_discovery` flow to continue with blueprint discussion

---

### Fallback

Ask user about unrecognized file types or ambiguous code patterns. If a file cannot be parsed, log it and continue with the rest.

---

## Error Handling

Catalog should **not abort** on individual file failures. Log errors, skip problematic files, continue with the rest. Report skipped files in the summary.

---

## Interactions with Other Directives

### Called By

- **`project_discovery`** — Delegates here when pre-existing FP code detected

### Calls

- **Helpers**: `reserve_file`, `finalize_file`, `reserve_function`, `finalize_function`, `create_interaction`, `create_theme`, `create_flow`, `project_notes_log`

### Flows To

- **`project_discovery`** — Returns to discovery after catalog completes

---

## Edge Cases

### Case 1: Large Codebase (>100 files)

Process in batches of 20-50 files. Report progress to user between batches:
```
Cataloging progress: 45/230 files registered...
```

### Case 2: Mixed Purity

Catalog everything. Flag impure functions with notes but do not abort or refuse. Discovery will address with user.

### Case 3: No Functions in File

Register the file without functions. Valid for: configuration files, data files, entry point scripts, type definition files.

### Case 4: Unrecognized Language

Use generic function patterns (look for common keywords: `function`, `def`, `fn`, `func`). Warn user about reduced accuracy.

### Case 5: Source Directory Not Set

Prompt user to set `source_directory` before cataloging can proceed. Cannot scan without knowing where to look.

---

## Database Operations

**Read Operations**:
- Infrastructure table: `source_directory`, `primary_language`
- File system: scan source directory recursively

**Write Operations**:
- Files table: register all source files
- Functions table: register all functions per file
- Interactions table: map dependencies
- Notes table: catalog log entries

---

## FP Compliance

**Purity**: ⚠️ Effect function — reads file system, writes to database
**Immutability**: ✅ File inventory built as immutable data, not mutated
**Side Effects**: ⚠️ Explicit — all DB writes via helpers

---

## Best Practices

1. **Use consistent patterns** — Same exclusions and function patterns as watchdog
2. **Batch for large projects** — Don't try to register 500 files in one pass
3. **Suggest, don't decide** — Themes and flows are suggestions for user to confirm in discovery
4. **Log everything** — Use notes for catalog progress and any anomalies
5. **Don't block on impurity** — Flag it, don't refuse to catalog it
6. **Reserve-finalize pattern** — Always use reserve → finalize for DB entries

---

## Version History

- **v1.0** (2026-01-30): Initial creation — extracted from project_init Phase 2 catalog branch

---

## Notes

- Extracted from `project_init` Phase 2's `catalog_existing_fp_code` branch for proper separation of concerns
- Only triggered for pre-existing codebases (not empty projects)
- Requires Phase 1 (`aimfp_init`) to be complete — needs database before cataloging
- Exclusion patterns are intentionally aligned with watchdog `config.py` for consistency
- Function patterns are intentionally aligned with watchdog `analyzers.py` for consistency
