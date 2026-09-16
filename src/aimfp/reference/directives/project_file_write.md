# Directive: project_file_write

**Type**: Project
**Level**: 3 (Operational Execution)
**Parent Directive**: project_add_path
**Priority**: HIGH - Core file generation and database synchronization

---

## Purpose

The `project_file_write` directive handles writing new or modified files using AIMFP-compliant patterns, validates code through FP directives, and updates `project.db` accordingly. This directive serves as the **bridge between code generation and database synchronization**, ensuring that every file written is tracked and compliant.

Key responsibilities:
- **Apply user preferences** from `directive_preferences` table (docstrings, max function length, guard clauses, code style)
- **Validate FP compliance** by calling `fp_purity`, `fp_immutability`, `fp_side_effect_detection`
- **Enforce DRY principle** - Extract common utilities to shared modules at appropriate scope level
- **Track user directive implementations** when path starts with `.aimfp-project/user-directives/generated/`
- **Update project.db** with file metadata, functions, interactions, and dependencies
- **Link to tasks** via AIMFP_METADATA for completion tracking

This is the **central file generation directive** - all code writing flows through here to maintain consistency and traceability.

---

## When to Apply

This directive applies when:
- **Writing new files** - Creating new source files in the project
- **Modifying existing files** - Updating or refactoring existing code
- **Generating from user directives** - Creating automation code from YAML/JSON/TXT directives
- **Called by other directives**:
  - `project_task_decomposition` - When task requires new file creation
  - `user_directive_implement` - Generates FP-compliant automation code
  - `project_refactor_path` - When refactoring creates new file versions

**User Preferences Integration**:
- Before writing, calls `user_preferences_sync` to load customizations
- Applies preferences like `always_add_docstrings`, `prefer_guard_clauses`, `max_function_length`
- Respects `code_style`, `indent_style` settings

---

## Workflow

### Trunk: check_user_preferences

Loads and applies user-defined preferences for file writing.

**Steps**:
1. **Query directive_preferences** - Load settings for `project_file_write`
2. **Apply to code generation context** - Configure code generator with preferences
3. **Fallback to defaults** - Use system defaults if preferences missing

### Branches

**Branch 1: If directive_preferences_exist**
- **Then**: `load_and_apply_preferences`
- **Details**: Query user preferences and apply to code generation
  - `always_add_docstrings` - Add function docstrings automatically
  - `max_function_length` - Warn if function exceeds line limit
  - `prefer_guard_clauses` - Use guard clauses instead of nested ifs
  - `code_style` - Apply specific coding style (e.g., "compact", "verbose")
  - `indent_style` - Use tabs or spaces
- **Helper**: Use helper to load directive preferences for project_file_write
- **Result**: Preferences loaded and ready to apply

**Branch 2: If preferences_applied**
- **Then**: `generate_file_with_preferences`
- **Details**: Generate code respecting user settings
  - Apply all loaded preferences to code generation
  - Fallback to defaults if specific preference missing
- **Result**: Code generated according to user customization

**Branch 3: If file_path_starts_with_.aimfp-project/user-directives/generated/**
- **Then**: `mark_as_user_directive_implementation`
- **Details**: Identify as user directive generated code
  - Set metadata tag: `user_directive_generated`
  - Still apply FP compliance checks
  - Track in both `project.db` AND `user_directives.db`
- **Result**: File marked for dual database tracking

**Branch 4: If user_directive_file_and_compliant**
- **Then**: `write_file_and_link_to_directive`
- **Details**: Write file and update both databases
  - Write to filesystem
  - Update `project.db` (files, functions, interactions)
  - Update `user_directives.db` (directive_implementations table)
  - Link to parent user directive
- **Result**: File written and tracked in both databases

**Branch 4.5: If using_reservation_system**
- **Then**: `reserve_finalize_workflow`
- **Details**: Use reservation system for names with embedded IDs
  - **When to use**: Creating files/functions that need database IDs in their names
  - **Step 1**: Call `project_reserve_finalize` to reserve names (sets is_reserved=TRUE)
  - **Step 2**: Get returned IDs from reservation
  - **Step 3**: Generate code with IDs embedded in names (e.g., 'calculator-ID_42.py')
  - **Step 4**: Write file to filesystem
  - **Step 5**: Update database to finalize (sets is_reserved=FALSE, updates actual names)
  - **Purpose**: Ensures unique names and prevents race conditions
- **Result**: File/function created with embedded ID, reservation finalized

**Branch 5: If code_compliant**
- **Then**: `write_file`
- **Details**: Standard file write with tracking
  - Include AIMFP_METADATA in file header
  - Write to filesystem
  - Update `project.db` tables using helpers:
    - `files` - file path, name, language, is_reserved (NO checksums - Git handles this)
    - `functions` - function names, purposes, parameters, returns, is_reserved
    - `interactions` - function dependencies
    - `file_flows` - theme/flow associations
  - Finalize any reservations (set is_reserved=FALSE, update names with IDs)
  - Mark associated task/item as completed if linked
- **Result**: File written and fully tracked

**Branch 6: If non_compliant**
- **Then**: `fp_compliance_check`
- **Details**: Escalate to FP directives for correction
  - Call `fp_purity` - Check for side effects
  - Call `fp_immutability` - Check for mutations
  - Call `fp_side_effect_detection` - Identify and isolate effects
  - If correctable: Apply automatic refactoring
  - If uncertain: Prompt user for guidance
- **Result**: Code refactored to meet FP compliance or user clarifies

**Branch 7: If low_confidence**
- **Then**: `prompt_user`
- **Details**: Uncertain about file write operation
  - Present analysis findings
  - Ask: "Should I write this file? Is it compliant?"
  - Log uncertainty to `notes` table
- **Result**: User confirms or provides corrections

**Fallback**: `prompt_user`
- Present issue and ask for clarification
- Log to `notes` for future reference

### Error Handling

**on_failure**: `prompt_user`
- If file write fails, present error and ask user to resolve
- Common issues: File permissions, path invalid, compliance violations

---

## Code Organization and DRY Principle

**CRITICAL**: Before writing any function, search existing domain modules for overlapping logic (DB first: get_function_by_name, get_file_by_name). If reusable code exists, import and compose — do not rewrite. Actively enforce DRY by extracting common utilities to shared modules. This is essential for AI efficiency at scale.

### Why DRY Matters for AI Efficiency

At scale (hundreds/thousands of files), code duplication causes massive overhead:

- **Token Generation**: Duplicating 50 lines × 1,000 files = 50,000 wasted lines (vs. 1,050 with DRY)
- **Database Bloat**: Same function stored 1,000 times with different file_ids (vs. 1 definition with DRY)
- **Maintenance Burden**: Logic change requires updating 1,000 files instead of 1
- **Context Window Waste**: Every file carries boilerplate instead of unique logic
- **Memory/Cache Overhead**: AI loads duplicated code repeatedly

**Database Provides Context**: AI queries database for function metadata (signatures, purposes, relationships), not file contents. No "readability penalty" from imports—AI gets full context from database queries.

### The Right DRY Philosophy

✅ **Extract when** (GOOD DRY):
- Code is **IDENTICAL** across 2+ files
- Function has **single responsibility**
- Use cases are **truly the same**
- No conditionals or parameters needed to handle variations

⚠️ **When code is similar but not identical**: Do NOT duplicate. Extract the shared core into a domain module, then compose or parameterize at each call site. "Similar but not identical" is a signal to modularize, not a license to copy-paste.

❌ **Don't extract when** (FORCED DRY—avoid this):
- Would require adding many parameters/conditionals to handle wildly different variations
- Use cases are **fundamentally different** (not just superficially similar)
- Would create "god functions" that try to do everything

### Examples

```python
# ✅ GOOD DRY - Extract this (identical everywhere)
def _open_connection(db_path: str) -> sqlite3.Connection:
    """Effect: Open database connection with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# ❌ FORCED DRY - Don't do this (forced reuse with many parameters)
def update_entity(conn, table, entity_id, field_name, new_value,
                  validate=True, cascade=False, log_change=True, ...):
    # 200 lines of conditionals trying to handle every case
    # This becomes unmaintainable

# ✅ GOOD DRY - Make separate focused functions instead
def update_file(conn, file_id, name, path): ...
def update_function(conn, func_id, name, purpose): ...
def update_type(conn, type_id, name, variants): ...
```

### Scope-Based Extraction Strategy

Files are NOT required to be self-contained. Extract common functions at the **highest appropriate scope level**:

**1. Global Level** (`src/aimfp/_common.py` or `src/aimfp/helpers/_common.py`)
- Use when: Function is used across ALL helper categories (project, core, git, user_preferences, user_directives)
- Examples: Database connections, generic Result types, universal validation utilities
- Extract if code appears in multiple categories

**2. Category Level** (`src/aimfp/helpers/project/_common.py`)
- Use when: Function is used across multiple files within ONE category
- Examples: Category-specific entity checks, domain-specific query builders
- Extract if code appears in 2+ files within same category

**3. File Level** (keep in the specific file)
- Use when: Function is used only in ONE file
- Examples: File-specific data transformations, unique business logic
- Keep local if truly file-specific

### Decision Rules

When writing a file, ASK:
1. **Is this function identical to one in another file?** → Extract to shared module immediately
2. **Is this function similar but requires variations?** → Make separate focused functions (don't force extraction)
3. **Is this function truly generic and focused?** → Extract proactively to appropriate scope
4. **Is this function domain-specific to this file only?** → Keep local

### Import Pattern

```python
# In src/aimfp/helpers/project/files_1.py
from ._common import (
    _open_connection,
    _check_file_exists,
    _create_deletion_note
)
from helpers._common import _validate_result  # Global utility
```

**This is standard behavior for all AIMFP projects** unless user explicitly overrides.

### State Management Pattern

**State Database Available**: For runtime mutable state needs, use the auto-generated state database instead of mutable globals.

**Location**: `<source-dir>/.state/runtime.db`

**Operations**: Import from `state_operations.{ext}`:
- `set_var` - Store a variable
- `get_var` - Retrieve a variable
- `delete_var` - Remove a variable
- `increment_var` - Increment a counter

These are NOT MCP tools and `get_helper_by_name` will not find them. They are
generated INTO the project from `templates/state_db/`, so you write code calling
them rather than invoking them — read the generated `state_operations` file for
current signatures. This list previously carried `set_var(var_name, value)` after
the real function had gained `var_type`, which is why the signatures are gone.

**When to Use**:
- ✅ Replacing mutable global variables
- ✅ Application state that changes at runtime
- ✅ Counters, toggles, runtime config

**When NOT to Use**:
- ❌ Session management (create separate DB)
- ❌ User data (use application database)
- ❌ Project-specific business logic (create separate DB)

**Example**:
```python
from .state.state_operations import set_var, get_var, increment_var

# Initialize counter
result = set_var('request_count', 0)

# Increment on each request
def handle_request(request_data):
    increment_var('request_count', 1)
    # ... process request

# Get current count
count_result = get_var('request_count')
if count_result.success:
    print(f"Total requests: {count_result.data}")
```

---

## Module Gate (MANDATORY before writing)

**BEFORE writing any code**, determine if this file belongs in a module. Call `project_module_check` to evaluate.

### Module Candidate Signals (any YES = module)

1. **External Dependency** — Is this code importing an external library/service? → MUST wrap in module. Never import libraries directly from orchestrator code.
2. **Cross-File Reuse** — Does this logic exist or could exist in multiple files? → Module.
3. **Domain Vocabulary** — Does the code have its own concepts (wallets, transactions, cart items)? → Module.
4. **Change Impact** — If internals changed, would >1 file break? → Definitely module.

### Module Gate Steps

1. `search_modules(domain_keywords)` — check if existing module covers this domain
2. `get_module_for_file(file_id)` — check if file path already falls under a module
3. If match found → write file under existing module path
4. If no match and candidate → `add_module(name, path, description, purpose, external_dependencies)`
5. If NOT a candidate (orchestrator, config, one-off) → proceed with write. Orchestrators must ONLY import from modules, never embed domain logic.

See `project_module_check` directive for full evaluation workflow and cross-software-type examples.

---

## Examples

### Example 1: Writing a Pure Function with User Preferences

**User Preference**:
**Use helper functions** for all user_preferences.db operations. Query available helpers for settings operations.

**User Request**: "Write multiply_matrices function"

**AI Execution**:
1. Calls `user_preferences_sync`
2. Loads preference: `always_add_docstrings = true`
3. Generates code with docstring:

```python
# AIMFP_METADATA: {"function_names": ["multiply_matrices"], "deps": ["validate_dimensions"], "theme": "matrix_operations", "flow": "calculation_flow", "version": 1, "task_id": 15}

def multiply_matrices(a: List[List[float]], b: List[List[float]]) -> Result[List[List[float]], str]:
    """
    Multiplies two matrices A and B.

    Args:
        a: First matrix (m x n)
        b: Second matrix (n x p)

    Returns:
        Result containing product matrix (m x p) or error message
    """
    # Validate dimensions
    validation = validate_dimensions(a, b)
    if validation.is_err():
        return Err(validation.unwrap_err())

    # Pure multiplication logic
    rows_a, cols_a = len(a), len(a[0])
    rows_b, cols_b = len(b), len(b[0])

    result = [[sum(a[i][k] * b[k][j] for k in range(cols_a))
               for j in range(cols_b)]
              for i in range(rows_a)]

    return Ok(result)
```

4. Validates with FP directives:
   - `fp_purity`: ✓ Pure (no side effects, deterministic)
   - `fp_immutability`: ✓ No mutations
5. Writes file to `src/matrix.py`
6. Updates `project.db`:
   - `files` table: New entry for `src/matrix.py`
   - `functions` table: `multiply_matrices` with purity_level='pure'
   - `interactions` table: Depends on `validate_dimensions`

### Example 2: User Directive Implementation

**User Directive File**: `directives/lights.yaml`
```yaml
name: turn_off_lights_5pm
trigger:
  type: time
  time: "17:00"
action:
  type: api_call
  api: homeassistant
```

**AI Generates**: `.aimfp-project/user-directives/generated/lights_controller.py`

**Workflow**:
1. Path detected: `.aimfp-project/user-directives/generated/` → Mark as user directive
2. Generate FP-compliant code
3. Validate with FP directives
4. Write to filesystem
5. Update `project.db` (files, functions)
6. Update `user_directives.db` (directive_implementations table)
7. Link to parent directive: `turn_off_lights_5pm`

### Example 3: Compliance Failure and Correction

**Initial Code** (non-compliant):
```python
global config

def process_order(order):
    discount = config['discount']  # External state access
    order['total'] = order['total'] * (1 - discount)  # Mutation
    save_to_db(order)  # Side effect
    return order
```

**AI Detection**:
1. `fp_purity` detects: Global state access, side effect
2. `fp_immutability` detects: Mutation of `order` dict
3. Non-compliant → Trigger refactoring

**Refactored Code** (compliant):
```python
def process_order(order: Dict[str, Any], discount: float) -> Dict[str, Any]:
    """Pure function: Calculate discounted order total."""
    return {
        **order,
        'total': order['total'] * (1 - discount)
    }

def save_order_effect(order: Dict[str, Any]) -> Result[None, str]:
    """Effect function: Save order to database (isolated side effect)."""
    try:
        save_to_db(order)
        return Ok(None)
    except Exception as e:
        return Err(str(e))
```

4. Validates again: ✓ Compliant
5. Writes refactored file
6. Updates database with corrected functions

---

## Integration with Other Directives

### Called By:
- `project_task_decomposition` - Creates files for new tasks
- `user_directive_implement` - Generates automation code
- `project_refactor_path` - Updates files during refactoring

### Calls:
- `user_preferences_sync` - Loads user customizations
- `fp_purity` - Validates function purity
- `fp_immutability` - Checks for mutations
- `fp_side_effect_detection` - Identifies side effects
- `project_update_db` - Syncs file metadata to database
- `project_metadata_sync` - Ensures DB-file consistency

---

## Database Updates

### Tables Modified:

**project.db**:
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

**user_directives.db** (if user directive generated):
**Use helper functions** for database operations. Query available helpers for user_directives.db.

**Alternative**: Direct SQL queries are acceptable for user_directives.db if helpers are insufficient, but helpers should be preferred for efficiency.

---

## Roadblocks and Resolutions

### Roadblock 1: missing_metadata
**Issue**: Generated code lacks AIMFP_METADATA header
**Resolution**: Add AIMFP_METADATA automatically, infer from context, prompt user if uncertain

### Roadblock 2: fp_violation
**Issue**: Code violates FP principles (mutations, side effects, etc.)
**Resolution**: Trigger `fp_compliance_check`, apply automatic refactoring, or prompt user

### Roadblock 3: user_directive_link_missing
**Issue**: User directive implementation file but no link to parent directive
**Resolution**: Parse file for directive reference in AIMFP_METADATA, or prompt user to link manually

### Roadblock 4: database_update_failed
**Issue**: File written but database update fails
**Resolution**: Rollback file write, log error, prompt user to resolve DB issue

---

## Intent Keywords

- "create file"
- "write code"
- "generate file"
- "write function"
- "implement"
- "add file"

**Confidence Threshold**: 0.7

---

## Related Directives

- `project_reserve_finalize` - Reserve names BEFORE writing to get database IDs for embedding in filenames/function names. Finalizes after write to set is_reserved=FALSE
- `project_update_db` - Synchronizes file metadata to database using helpers (no checksums)
- `project_task_decomposition` - Creates files for tasks
- `fp_purity` - Validates function purity
- `fp_immutability` - Checks immutability
- `fp_side_effect_detection` - Identifies side effects
- `user_directive_implement` - Generates user automation code
- `user_preferences_sync` - Loads user customizations

---

## Notes

- **Always apply user preferences** before generating code
- **Dual database tracking** for user directive implementations
- **AIMFP_METADATA required** in all generated files
- **FP compliance mandatory** - no exceptions
- **Transactional updates** - rollback on failure
- **No checksums** - Git handles file change detection
- **Reservation system** - Use when IDs need to be embedded in names
- **Use helpers** - All database operations use project CRUD/query helpers, not direct SQL
- **Link to tasks** via `task_id` in metadata for completion tracking
