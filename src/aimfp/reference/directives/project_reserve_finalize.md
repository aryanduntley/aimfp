# project_reserve_finalize

## Purpose

The `project_reserve_finalize` directive manages the reservation and finalization workflow for files, functions, and types in the project database. This workflow enables AI to reserve names BEFORE writing code, receive database IDs, embed those IDs in code for instant lookups, and finalize the reservations after implementation.

**Category**: Project
**Type**: Project
**Priority**: High
**Confidence Threshold**: 0.8

---

## Workflow

### Trunk
**`reserve_entities`**: Reserve names in database before writing code.

### Branches

1. **`reserving_file`** → **`reserve_file_name`**
   - **Condition**: Planning to create new file
   - **Action**: Reserve file name in database
   - **Details**: Call `reserve_file(name, path, language, skip_id_naming=False)`
   - **Parameter**: `skip_id_naming=True` to track without ID embedding (sets `id_in_name=0`)
   - **Output**: Returns file_id for embedding in filename (unless skip_id_naming)

2. **`reserving_function`** → **`reserve_function_name`**
   - **Condition**: Planning to create new function
   - **Action**: Reserve function name in database
   - **Details**: Call `reserve_function(name, file_id, purpose, skip_id_naming=False)`
   - **Parameter**: `skip_id_naming=True` for MCP tools and naming exceptions (sets `id_in_name=0`)
   - **Output**: Returns function_id for embedding in function name (unless skip_id_naming)

3. **`reserving_type`** → **`reserve_type_name`**
   - **Condition**: Planning to create new type/ADT
   - **Action**: Reserve type name in database
   - **Details**: Call `reserve_type(name, definition_json, file_id, skip_id_naming=False)`
   - **Parameter**: `skip_id_naming=True` to track without ID embedding (sets `id_in_name=0`)
   - **Output**: Returns type_id for embedding in type name (unless skip_id_naming)

4. **`reserved_successfully`** → **`write_with_embedded_id`**
   - **Condition**: Reservation returned ID
   - **Action**: Write code with ID embedded in name
   - **Details**: Use naming conventions:
     - Files: `filename_id_42.py`
     - Functions: `function_name_id_99()`
     - Types: `TypeName_id_7`
   - Add ID comment: `# AIMFP:FUNC:42` or `AIMFP-ID: 42` in docstring
   - **Output**: Code written with embedded database ID

5. **`code_written`** → **`finalize_reservation`**
   - **Condition**: Code successfully written to filesystem
   - **Action**: Finalize reservation in database
   - **Details**: Call `finalize_file(file_id, skip_id_naming)`, `finalize_function(function_id, skip_id_naming)`, or `finalize_type(type_id, skip_id_naming)`
   - **Parameter**: `skip_id_naming=True` skips _id_XX pattern validation (use for naming exceptions and MCP tools)
   - Updates name field to include embedded ID (e.g., `calculate_sum` → `calculate_sum_id_99`) unless skip_id_naming=True
   - Sets is_reserved=FALSE
   - **Output**: Entity marked as implemented in database (with ID in name unless skip_id_naming)

6. **Fallback** → **`prompt_user`**
   - **Condition**: Reservation failed or unclear
   - **Action**: Request clarification on entity details

### Module Assignment Check (after reserving a file)

After reserving a file, check if its path falls under an existing module:
1. Call `get_module_for_file(file_id)` — returns the module if the file's path is under a module directory
2. If file is NOT in any module and the directory represents a new domain concern → create the module via `add_module()` BEFORE writing code
3. If file IS in a module → no action needed, membership is automatic via path

This ensures new domain directories get proper module tracking from the start.

### Error Handling
- **On reservation failure**: Check for naming collisions, suggest alternative names
- **On finalization failure**: Verify entity exists, check is_reserved flag

---

## Refactoring Strategies

### Strategy 1: Reserve File Before Creation

**Before (No Reservation)**:
```python
# AI writes file without ID
# File: calculator.py

def add(a, b):
    return a + b

# Later needs to query: "Get file by name 'calculator.py'" (string matching)
```

**After (With Reservation)**:
```python
# Step 1: Reserve
file_id = reserve_file(name='calculator', path='src/calculator_id_42.py', language='python')
# Returns: 42

# Step 2: Write with embedded ID
# File: calculator_id_42.py
# AIMFP:FILE:42

def add_id_101(a, b):  # AIMFP:FUNC:101
    return a + b

# Step 3: Finalize
finalize_file(42)

# Later: get_file(42) - instant integer lookup, no string matching
```

### Strategy 2: Reserve Function Before Implementation

**Before (No Reservation)**:
```python
# Function written directly
def process_payment(amount, user):
    # Implementation
    pass

# Database query needed: get_function_by_name('process_payment')
# Vulnerable to renames, requires string matching
```

**After (With Reservation)**:
```python
# Step 1: Reserve
function_id = reserve_function(
    name='process_payment',
    file_id=42,
    purpose='Process payment transaction'
)
# Returns: 203

# Step 2: Write with embedded ID
def process_payment_id_203(amount, user):
    """Process payment transaction.

    AIMFP-ID: 203
    """
    # Implementation
    pass

# Step 3: Finalize
finalize_function(203)

# Later: get_function(203) - instant lookup
# Rename-proof: function can be renamed, ID relationships remain intact
```

### Strategy 3: Reserve Type Before Definition

**Before (No Reservation)**:
```python
# Type defined directly
class Result:
    """Result ADT for success/error handling."""
    pass

# Database: get_type_by_name('Result') - ambiguous, requires context
```

**After (With Reservation)**:
```python
# Step 1: Reserve
type_id = reserve_type(
    name='Result',
    file_id=42,
    purpose='Result ADT for success/error handling'
)
# Returns: 15

# Step 2: Write with embedded ID
class Result_id_15:  # AIMFP:TYPE:15
    """Result ADT for success/error handling.

    AIMFP-ID: 15
    """
    pass

# Step 3: Finalize
finalize_type(15)

# Later: get_type(15) - instant, unambiguous
```

---

## Examples

### Example 1: Complete File Creation Workflow

```python
# Planning phase
file_id = reserve_file(
    name='user_service',
    path='src/services/user_service_id_55.py',
    language='python'
)
# Returns: 55

# Reserve functions for this file
func1_id = reserve_function(name='get_user_id_88', file_id=55, purpose='Fetch user by ID')
func2_id = reserve_function(name='update_user_id_89', file_id=55, purpose='Update user data')
# Returns: 88, 89

# Write file with embedded IDs
write_file(
    path='src/services/user_service_id_55.py',
    content="""
# AIMFP:FILE:55

def get_user_id_88(user_id):  # AIMFP:FUNC:88
    \"\"\"Fetch user by ID.\"\"\"
    return query_database('users', user_id)

def update_user_id_89(user_id, data):  # AIMFP:FUNC:89
    \"\"\"Update user data.\"\"\"
    return update_database('users', user_id, data)
"""
)

# Finalize all reservations
finalize_file(55)
finalize_function(88)
finalize_function(89)

# Now all lookups are instant:
# get_file(55), get_function(88), get_function(89)
```

### Example 2: Type with Associated Functions

```python
# Reserve type
type_id = reserve_type(name='Option', file_id=10, purpose='Optional value ADT')
# Returns: 20

# Reserve factory functions
some_id = reserve_function(name='Some_id_41', file_id=10, purpose='Create Some variant')
none_id = reserve_function(name='None_id_42', file_id=10, purpose='Create None variant')
# Returns: 41, 42

# Write code
write_file(
    path='src/types/option_id_10.py',
    content="""
# AIMFP:FILE:10

class Option_id_20:  # AIMFP:TYPE:20
    \"\"\"Optional value ADT.\"\"\"
    pass

def Some_id_41(value):  # AIMFP:FUNC:41
    \"\"\"Create Some variant.\"\"\"
    return Option_id_20(value=value, is_some=True)

def None_id_42():  # AIMFP:FUNC:42
    \"\"\"Create None variant.\"\"\"
    return Option_id_20(value=None, is_some=False)
"""
)

# Finalize
finalize_type(20)
finalize_function(41)
finalize_function(42)

# Create type-function relationships
add_type_function(type_id=20, function_id=41, role='factory')
add_type_function(type_id=20, function_id=42, role='factory')
```

---

## Edge Cases

### Edge Case 1: Reservation Failure (Name Collision)
**Scenario**: Name already reserved or exists
**Issue**: Cannot reserve duplicate name
**Handling**:
- Check database for existing names
- Suggest numbered suffix: `process_user_v2`, `process_user_alt`
- Prompt user for alternative name

### Edge Case 2: Implementation Fails After Reservation
**Scenario**: Code fails to write after reservation
**Issue**: Database has reserved entry, no actual code
**Handling**:
- Cleanup: Delete reservation or mark as abandoned
- Log reason for failure
- Retry with corrected code

### Edge Case 3: Finalization Without Reservation
**Scenario**: Attempt to finalize non-reserved entity
**Issue**: is_reserved flag already FALSE or entity doesn't exist
**Handling**:
- Verify entity exists in database
- Check is_reserved status
- If already finalized, skip (idempotent)

### Edge Case 4: ID Embedding Format Inconsistent
**Scenario**: Multiple ID embedding styles in same project
**Issue**: Some use `# AIMFP:FUNC:42`, others use docstring format
**Handling**:
- Standardize on one format per project
- Document choice in ProjectBlueprint.md
- Maintain consistency across all files

### Edge Case 5: Database Rebuild
**Scenario**: Project database rebuilt from scratch
**Issue**: Code has embedded IDs that may not match new database
**Handling**:
- Use verify_ids directive to detect mismatches
- Use repair_ids directive to fix discrepancies
- Maintain mapping of old_id → new_id during migration

---

## Database Operations

### Reserve File
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

### Reserve Function
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

### Reserve Type
**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

### Finalize Reservation
Updates name to include embedded ID and sets is_reserved to FALSE:

**Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).

---

## Related Directives

### Project Directives
- **project_file_write**: Uses reservations when creating new files
- **project_update_db**: Updates metadata after finalization
- **project_integrity_check**: Verifies reservation consistency

---

## Helper Functions

Query `get_helpers_for_directive()` to discover this directive's available helpers.
See system prompt for usage.

---

## Intent Keywords

- `reserve`
- `finalize`
- `embed ID`
- `database ID`
- `collision prevention`

---

## Confidence Threshold

**0.8** - High confidence for reservation workflow execution.

---

## Naming Exceptions

Certain files and functions follow the reserve → finalize pattern but **skip the ID embedding step**. Use `skip_id_naming=True` parameter and database tracks this via `id_in_name=0`.

### Database Field: `id_in_name`

- `id_in_name=1` (default): Entity name contains `_id_XX` pattern
- `id_in_name=0`: Entity tracked without ID in name (clean name preserved)
- Set automatically based on `skip_id_naming` parameter

### Files That Skip ID Embedding

1. **`__init__.py` files** - Python package initialization files must be named exactly `__init__.py`
   - Reserve with `skip_id_naming=True`, finalize with `skip_id_naming=True`
   - Database: `id_in_name=0`, name field stores `__init__.py`

2. **Database files (`.db`)** - SQLite database files are data stores, not source code
   - Examples: `project.db`, `aimfp_core.db`, `user_preferences.db`
   - Use `skip_id_naming=True` - do not embed IDs in database filenames

3. **Other required naming** - Files where external tools or language conventions require specific names
   - Examples: `pyproject.toml`, `setup.py`, `conftest.py`
   - Reserve/finalize with `skip_id_naming=True`, preserve required name

### Functions That Skip ID Embedding (But ARE Tracked)

1. **MCP tool functions** - Functions exposed as MCP tools must have clean callable names
   - Examples: `reserve_file`, `finalize_function`, `get_file_by_path`
   - These ARE tracked in database with `id_in_name=0`
   - Use `skip_id_naming=True` when reserving and finalizing

### Functions Whose Tracking Is Optional

1. **Private/internal functions (underscore prefix)** - Functions starting with `_` are implementation details
   - Examples: `_open_connection`, `_reserve_file_effect`, `_validate_input`
   - These are internal helpers, not part of the public API
   - **Tracking is not required, and allowed** - reserve one when it is worth looking up by name
     (a shared effect, a helper other files call); catalog_functions includes them by default
   - Untracked ones are invisible to audits: the call graph credits their calls to the tracked
     functions that use them, so the edges stay right either way

### User-Defined Exclusions

Users can add custom exclusions via `user_preferences.db`:
- `project_reserve_finalize.exclude_patterns` - glob patterns to exclude from ID embedding
- `project_reserve_finalize.exclude_folders` - folders to skip entirely

### Workflow for Exceptions

```python
# For __init__.py files:
file_id = reserve_file(name='__init__', path='src/helpers/__init__.py', language='python', skip_id_naming=True)
# Returns: 55, sets id_in_name=0

# Write file (NO ID in filename - it stays __init__.py)
write_file(path='src/helpers/__init__.py', content="...")

# Finalize (skip validation, name remains __init__)
finalize_file(55, skip_id_naming=True)
# Database: name='__init__', path='src/helpers/__init__.py', is_reserved=0, id_in_name=0

# For MCP tool functions:
func_id = reserve_function(name='reserve_file', file_id=55, purpose='Reserve file ID', skip_id_naming=True)
# Returns: 88, sets id_in_name=0

# Write function with clean name (NO ID suffix)
# def reserve_file(db_path, name, path, language): ...

# Finalize (skip _id_XX validation)
finalize_function(88, skip_id_naming=True)
# Database: name='reserve_file', is_reserved=0, id_in_name=0

# For private functions - tracking optional:
# Usually skipped: _open_connection, _reserve_file_effect, _get_file_by_path_effect
# Reserve/finalize one like any function when it is worth a lookup; audits accept either
```

---

## Notes

- Reservation workflow prevents naming collisions during multi-step implementations
- Embedded IDs enable instant database lookups (bypass string matching)
- IDs are rename-proof: function name can change, relationships stay intact
- Finalization updates name with embedded ID and sets is_reserved=FALSE, marking entity as fully implemented
- ID formats: `# AIMFP:FILE:42`, `# AIMFP:FUNC:99`, `# AIMFP:TYPE:15`
- Alternative: Embed in docstring `AIMFP-ID: 42`
- Reservation enables batch operations: reserve multiple entities, write all, finalize all
- Database IDs save API costs: single integer lookup vs multiple string queries
- Benefits compound in large codebases with hundreds of functions/types
- **Exception files** (`__init__.py`, `.db`, required naming) follow reserve → finalize but skip ID embedding
