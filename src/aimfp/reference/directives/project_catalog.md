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
1. Call `scan_source_tree()` — one call does all of the below. It reads
   `source_directory` from infrastructure, walks the tree, applies the full
   exclusion stack, and extracts signatures. Pass `module_path` to narrow the scan
   to one subtree and adopt a large codebase a module at a time.
2. The scan is **read-only** — nothing is written. Review the inventory first.
3. Check each file's `fidelity`:
   - `full` (Python, via stdlib `ast`) — real parameters, return annotations, and
     docstring-seeded purposes
   - `names_only` (JS/TS/Rust/Go/Java, via regex) — names and line numbers only.
     You **must** supply purpose, parameters, and returns yourself.
4. Review `parse_failures` and `skipped_unsupported`: those files were not
   extracted and need manual handling or deliberate omission.

The scan applies these exclusions for you:
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
1. Call `catalog_files(files=[{name, path, language}, ...])` with the scan results
2. Capture the returned IDs — they come back in input order, and functions and
   types need `file_id`
3. For large codebases (>100 files): register one module at a time and report
   progress to the user between modules

**Do NOT use `reserve_file`/`finalize_file` here.** That protocol is two-phase
because it serves writing *new* code: reserve an ID, embed it in the filename,
write the file, finalize. Code being adopted already exists on disk with a fixed
name that no ID will ever be embedded into, so the reserve phase has nothing left
to do. `catalog_files` writes rows already finalized (`is_reserved=0`,
`id_in_name=0`).

`catalog_files` is **idempotent on path** — re-cataloging after a re-scan updates
rows instead of duplicating them, so it is safe to re-run as source changes.

**Helpers**: `catalog_files`

---

### Branch 3: Scan Functions Per File

**Action**: Identify and register all functions in each file.

**Steps**:
1. Signatures already came back from `scan_source_tree` — **do not re-parse the
   files**
2. **Enrich before writing.** Extraction seeds `purpose` from the first line of the
   docstring, so functions without docstrings arrive with `purpose: null`. Fill
   those in. Null purposes make functions invisible to future lookup and reduce the
   database to a name index — which defeats the point of cataloging.
3. Call `catalog_functions(functions=[{name, file_id, purpose, parameters, returns}, ...])`
4. Call `catalog_types(types=[{name, file_id, definition, description}, ...])` for
   extracted type definitions (dataclasses, Enum, TypedDict, NamedTuple, Protocol)
5. The `is_effect` flag on each function is a **hint read from declared intent** —
   the `_effect_` naming convention (prefix or suffix) and the `Effect:` docstring
   marker. It deliberately does not inspect function bodies: guessing purity from
   call sites produces false confidence. Treat impurity as informational, never a
   blocker.

**Do NOT use `reserve_function`/`finalize_function` or `reserve_type`/`finalize_type`**
for existing code — same reasoning as `catalog_files` above. Both catalog tools are
idempotent on `(file_id, name)`.

**Helpers**: `catalog_functions`, `catalog_types`

---

### Branch 4: Map Interactions

**Action**: Build the project's dependency graph.

**Steps**:
1. For each function, identify which other project functions it calls
2. For each file, identify which other project files it imports from
3. Identify external library dependencies per file
4. Record dependencies via `add_interactions` (batch) — tuples of
   `(source_function_id, target_function_id, interaction_type, description)`
5. Link types to the functions that use them via `add_types_functions`
6. Assign files to modules via `add_files_to_module` and to flows via
   `add_file_flows` — a cataloged file with no flow is architecturally disconnected

**Helpers**: `add_interactions`, `add_types_functions`, `add_files_to_module`,
`add_file_flows`

> The catalog tools stop at registration on purpose. Everything past that point is
> the ordinary batch surface, unchanged — cataloged rows are not special.

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

- **Catalog helpers** (single-phase, for existing code): `scan_source_tree`,
  `catalog_files`, `catalog_functions`, `catalog_types`
- **Linking helpers** (shared with normal authoring): `add_interactions`,
  `add_types_functions`, `add_files_to_module`, `add_file_flows`
- **Structure helpers**: `add_theme`, `add_flow`, `add_note`

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
