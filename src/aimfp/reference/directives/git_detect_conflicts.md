# Directive: git_detect_conflicts

**Type**: Git
**Level**: 3
**Parent Directive**: git_merge_branch
**Priority**: HIGH - Critical for safe merging

---

## Purpose

The `git_detect_conflicts` directive performs **FP-powered dry-run analysis** of branch merge conflicts before attempting an actual merge. This directive goes far beyond Git's basic conflict detection by leveraging AIMFP's database-tracked function metadata (purity levels, dependencies, tests) to intelligently classify conflicts and assign confidence scores for auto-resolution.

> **With use of InterCommAIMFP (multi-agent parallel merge):** this analyzes **source/code** conflicts. To find where parallel-worker branches overlap in **`project.db` state**, use `detect_state_conflicts` (and reconcile with `export_state_changeset` / `apply_state_changeset`). See `docs/intercommaimfptools/`.

This directive is **essential for safe collaborative merging** because:
- **Prevents merge disasters**: Detect conflicts before they corrupt code
- **FP intelligence**: Uses function purity to guide resolution strategies
- **Confidence scoring**: AI provides confidence levels for auto-resolution
- **Database-aware**: Detects conflicts in both code AND project.db
- **AI recommendations**: Suggests best resolution strategy for each conflict
- **Test-driven**: Compares test coverage to recommend better versions

The conflict detection workflow:
1. **Git diff analysis** - Compare source and target branches
2. **Categorize changes** - Files only in source, only in target, modified in both
3. **Function-level analysis** - Extract and compare functions from both branches
4. **FP purity analysis** - Apply FP rules to determine best resolution
5. **Database conflict detection** - Check for project.db conflicts
6. **Build conflict report** - Detailed analysis with AI recommendations

**Key Insight**: Pure functions are **dramatically easier to merge** than stateful OOP code. This directive exploits FP purity to provide intelligent merge guidance.

---

## When to Apply

This directive applies when:
- **Called by `git_merge_branch`** - Before attempting merge operation
- **User requests conflict check** - "Check for conflicts before merge"
- **Pre-merge validation** - Before merging work branch to main
- **Manual merge analysis** - Understanding what changed between branches
- **Risk assessment** - Evaluating merge complexity before proceeding

---

## Workflow

### Trunk: git_diff_branches

Performs Git diff to identify changed files between branches.

**Steps**:
1. **Validate branches exist** - Check source and target branches
2. **Run git diff** - Get list of changed files between branches
3. **Parse diff output** - Categorize file changes
4. **Route to analysis** - Based on change complexity

### Branches

**Branch 1: If no_file_changes**
- **Then**: `return_no_conflicts`
- **Details**: No changes between branches
  - Message: "No changes to merge - branches are identical"
  - No conflicts to resolve
  - Safe to merge (no-op)
- **Result**: Clean merge confirmed

**Branch 2: If files_changed**
- **Then**: `categorize_changes`
- **Details**: Classify file changes by type
  - Categories:
    1. **files_only_in_source**: New files added in source branch
       - Example: Alice added src/matrix.py (not in main)
       - Resolution: Safe to add (no conflict)
    2. **files_only_in_target**: New files added in target branch
       - Example: Bob merged src/vector.py to main (not in Alice's branch)
       - Resolution: Safe to keep (no conflict with Alice's work)
    3. **files_modified_in_both**: Same file changed in both branches
       - Example: Both Alice and Bob modified src/calc.py
       - Resolution: **Needs function-level analysis**
  - Count conflicts by category
- **Result**: Change taxonomy built

**Branch 3: If files_only_in_one**
- **Then**: `mark_safe_merge`
- **Details**: Files unique to one branch (no overlap)
  - Conflict type: None
  - Auto-resolvable: True
  - Confidence: 100%
  - Action: Git can merge automatically
- **Result**: Safe merge confirmed for these files

**Branch 4: If files_modified_in_both**
- **Then**: `analyze_function_conflicts`
- **Details**: Deep function-level conflict analysis
  - Actions:
    1. **Extract functions from both versions**:
       - Parse source branch version with AST
       - Parse target branch version with AST
       - Build function dictionaries: `{name: FunctionDef}`
    2. **Compare function signatures**:
       - Same name, same parameters → Check implementation
       - Same name, different parameters → Conflict (signature change)
       - Function added/removed → Mark accordingly
    3. **Query purity levels** from project.db:
       **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
    4. **Query dependencies**:
       **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
    5. **Query test results**:
       - Get test count and pass/fail status for both versions
       - Higher test coverage = higher confidence
  - Build conflict objects with metadata
- **Result**: Function conflicts cataloged with metadata

**Branch 5: If function_conflicts_detected**
- **Then**: `apply_fp_analysis`
- **Details**: Use FP purity rules for resolution strategy
  - FP Resolution Rules:
    1. **One pure, one impure** → Prefer pure version
       - Confidence: 90%
       - Reason: Pure functions are more maintainable, testable, composable
    2. **Both pure, different logic** → Compare tests
       - If one has more passing tests → Prefer that version (Confidence: 85%)
       - If equal tests → Keep both versions with renamed functions (Confidence: 70%)
    3. **Dependencies differ** → Manual review required
       - Confidence: 50%
       - Reason: Architectural decision needed
    4. **Both impure** → Manual review required
       - Confidence: 30%
       - Reason: Side effects make comparison difficult
    5. **Unclear/complex** → Manual review required
       - Confidence: 20%
       - Reason: Human judgment needed
  - Apply rules to all function conflicts
  - Assign confidence scores
  - Generate AI recommendations
- **Result**: Each conflict has resolution strategy

**Branch 6: If fp_analysis_complete**
- **Then**: `check_database_conflicts`
- **Details**: Detect conflicts in project.db between branches
  - Actions:
    1. **Extract both database versions**:
       ```bash
       git show main:.aimfp/project.db > main.db
       git show aimfp-alice-001:.aimfp/project.db > alice.db
       ```
    2. **Connect to both databases**
    3. **Compare critical tables**:
       - `functions`: Same function name, different metadata
       - `tasks`: Same task ID, different status/assignee
       - `themes`: Same theme, different structure
       - `interactions`: Different dependency graphs
    4. **Identify row conflicts**:
       - Same primary key, different data → Conflict
       - New rows unique to each → Safe to merge
    5. **Apply FP analysis to DB conflicts**:
       - Function purity conflicts → Prefer pure version
       - Task status conflicts → Use most recent timestamp
       - Theme conflicts → Merge hierarchies if compatible
- **Result**: Database conflicts cataloged

**Branch 7: If all_conflicts_analyzed**
- **Then**: `build_conflict_report`
- **Details**: Create comprehensive ConflictAnalysis report
  - Report includes:
    - **File conflicts**: List of files modified in both branches
    - **Function conflicts**: Function-level analysis with metadata
      ```python
      {
        "function": "calculate_total",
        "file": "src/calc.py",
        "source_version": {
          "purity": "pure",
          "parameters": ["items: tuple[float, ...]", "tax_rate: float"],
          "tests": {"total": 10, "passing": 10},
          "dependencies": ["validate_items"]
        },
        "target_version": {
          "purity": "pure",
          "parameters": ["items: tuple[float, ...]", "tax_rate: float"],
          "tests": {"total": 12, "passing": 12},
          "dependencies": ["validate_items", "apply_discounts"]
        },
        "recommendation": "Keep target version",
        "reason": "More comprehensive tests, additional feature (discounts)",
        "confidence": 0.85
      }
      ```
    - **Database conflicts**: project.db row conflicts
    - **Auto-resolvable count**: Conflicts with confidence > 0.8
    - **Manual review count**: Conflicts needing user decision
    - **Confidence scores**: For each conflict
    - **AI recommendations**: Suggested resolution for each conflict
  - Format report for presentation to user
- **Result**: Complete conflict analysis ready

**Branch 8: If report_complete**
- **Then**: `return_analysis`
- **Details**: Return ConflictAnalysis object
  - Format: Structured data object
  - Fields:
    - `has_conflicts: bool`
    - `conflict_count: int`
    - `auto_resolvable_count: int`
    - `manual_count: int`
    - `conflicts: list[ConflictDetail]`
    - `recommendations: list[ResolutionStrategy]`
  - This object passed to `git_merge_branch` for action
- **Result**: Conflict analysis complete

**Fallback**: `return_error`
- **Details**: Conflict detection failed
  - Message: "Conflict detection failed - manual merge required"
  - Reasons:
    - Git diff failed (branch doesn't exist)
    - AST parsing failed (syntax errors)
    - Database queries failed (corrupted DB)
  - Fallback: Recommend manual Git merge
  - Log error to notes table
- **Result**: User warned of detection failure

---

## Examples

### ✅ Compliant Usage

**Scenario 1: No Conflicts (Different Files):**
```bash
# Alice added src/matrix.py, Bob merged src/vector.py
# AI calls: git_detect_conflicts(source="aimfp-alice-001", target="main")

# Workflow:
# 1. git diff --name-only main..aimfp-alice-001
#    → src/matrix.py (only in Alice's branch)
# 2. Categorize: files_only_in_source
# 3. mark_safe_merge
#
# Result:
# {
#   "has_conflicts": false,
#   "conflict_count": 0,
#   "auto_resolvable_count": 0,
#   "conflicts": [],
#   "recommendation": "Safe to merge - no conflicts detected"
# }
```

---

**Scenario 2: Function Conflict - One Pure, One Impure:**
```python
# Both Alice and Bob modified calculate_total() in src/calc.py

# Alice's version (main branch):
def calculate_total(items, tax_rate):  # ❌ No type hints, impure
    global discount  # ❌ Uses global state
    subtotal = sum(item.price for item in items)
    return subtotal * (1 + tax_rate) * (1 - discount)

# Bob's version (aimfp-bob-001 branch):
def calculate_total(items: tuple[float, ...], tax_rate: float) -> float:
    """Pure function - calculates total with tax."""  # ✅ Pure
    subtotal = sum(items)
    return subtotal * (1 + tax_rate)

# git_detect_conflicts analysis:
# 1. Files modified in both: src/calc.py
# 2. Extract functions from both versions
# 3. Query purity levels:
#    - Alice's version: purity_level = "impure" (uses global state)
#    - Bob's version: purity_level = "pure"
# 4. Apply FP rule: "one_pure_one_impure -> prefer_pure"
# 5. Confidence: 90%
#
# Result:
# {
#   "has_conflicts": true,
#   "conflict_count": 1,
#   "auto_resolvable_count": 1,
#   "conflicts": [{
#     "type": "function",
#     "function": "calculate_total",
#     "file": "src/calc.py",
#     "alice_purity": "impure",
#     "bob_purity": "pure",
#     "recommendation": "Keep Bob's version",
#     "reason": "Bob's version is pure (no global state, type-safe)",
#     "confidence": 0.90,
#     "auto_resolvable": true
#   }]
# }
```

---

**Scenario 3: Function Conflict - Both Pure, Different Tests:**
```python
# Both Alice and Bob created multiply_matrices() (new function, not in main)

# Alice's version:
def multiply_matrices(a: Matrix, b: Matrix) -> Matrix:
    """Pure matrix multiplication."""
    # Basic implementation
    # Tests: 8/8 passing
    ...

# Bob's version:
def multiply_matrices(a: Matrix, b: Matrix) -> Matrix:
    """Pure matrix multiplication with optimization."""
    # Optimized for large matrices
    # Tests: 15/15 passing (includes edge cases)
    ...

# git_detect_conflicts analysis:
# 1. Both added new function (merge conflict in file)
# 2. Both versions are pure
# 3. Query test results:
#    - Alice: 8 tests passing
#    - Bob: 15 tests passing (includes large matrix edge cases)
# 4. Apply FP rule: "both_pure_different_logic -> compare_tests"
# 5. Confidence: 85% (Bob has more comprehensive tests)
#
# Result:
# {
#   "has_conflicts": true,
#   "conflict_count": 1,
#   "auto_resolvable_count": 1,
#   "conflicts": [{
#     "type": "function",
#     "function": "multiply_matrices",
#     "file": "src/matrix.py",
#     "both_pure": true,
#     "alice_tests": {"total": 8, "passing": 8},
#     "bob_tests": {"total": 15, "passing": 15},
#     "recommendation": "Keep Bob's version",
#     "reason": "Both pure, Bob has more comprehensive tests (edge cases)",
#     "confidence": 0.85,
#     "auto_resolvable": true,
#     "alternative": "Keep both as multiply_matrices() and multiply_matrices_optimized()"
#   }]
# }
```

---

**Scenario 4: Database Conflict:**
```python
# Alice and Bob both modified task #15 status in project.db

# Alice's version:
# UPDATE tasks SET status='completed', updated_at='2024-10-27 10:00:00' WHERE id=15

# Bob's version:
# UPDATE tasks SET status='in_progress', assignee='bob', updated_at='2024-10-27 11:00:00' WHERE id=15

# git_detect_conflicts analysis:
# 1. Extract both database versions
# 2. Compare tasks table:
#    - Same task_id (15), different status and assignee
# 3. Check timestamps: Bob's update is more recent
# 4. Confidence: 75% (more recent timestamp usually correct)
#
# Result:
# {
#   "has_conflicts": true,
#   "conflict_count": 1,
#   "auto_resolvable_count": 0,  # DB conflicts need user review
#   "manual_count": 1,
#   "conflicts": [{
#     "type": "database",
#     "table": "tasks",
#     "row_id": 15,
#     "alice_version": {"status": "completed", "assignee": "alice"},
#     "bob_version": {"status": "in_progress", "assignee": "bob"},
#     "recommendation": "Keep Bob's version (more recent timestamp)",
#     "reason": "Bob's update is more recent",
#     "confidence": 0.75,
#     "auto_resolvable": false,
#     "requires_user_decision": true
#   }]
# }
```

---

### ❌ Non-Compliant Usage

**Not Using FP Analysis:**
```python
# ❌ Just checking Git conflicts
result = subprocess.run(['git', 'merge', '--no-commit', branch])
if result.returncode != 0:
    print("Conflicts detected")
# No FP intelligence, no recommendations
```

**Why Non-Compliant**:
- Doesn't use function purity levels
- No confidence scoring
- No AI recommendations
- Misses database conflicts

**Corrected:**
```python
# ✅ Use git_detect_conflicts directive
analysis = git_detect_conflicts(source_branch="aimfp-alice-001", target_branch="main")
if analysis.has_conflicts:
    print(f"Conflicts: {analysis.conflict_count}")
    print(f"Auto-resolvable: {analysis.auto_resolvable_count}")
    for conflict in analysis.conflicts:
        print(f"AI recommends: {conflict.recommendation} (confidence: {conflict.confidence})")
```

---

**Ignoring Database Conflicts:**
```python
# ❌ Only checking code conflicts
changed_files = get_git_diff_files(source, target)
# Forgot to check project.db conflicts
```

**Why Non-Compliant**:
- Database conflicts not detected
- Merge could corrupt project.db
- Task/function metadata inconsistent

**Corrected:**
```python
# ✅ Check both code AND database
analysis = git_detect_conflicts(source_branch, target_branch)
# Includes both code and database conflict detection
```

---

## Edge Cases

### Edge Case 1: Branch Diverged Too Much (Many Commits Behind)

**Issue**: Source branch is many commits behind target

**Handling**:
```python
# Count commits divergence
commits_behind = run_command(f"git rev-list --count {source}..{target}")

if commits_behind > 50:
    # Too many changes to analyze individually
    return ConflictAnalysis(
        has_conflicts=True,
        conflict_count=-1,  # Unknown
        recommendation="Branch has diverged significantly (50+ commits behind)",
        suggestion="Consider rebasing branch before merging: git rebase main",
        manual_merge_required=True
    )
```

**Directive Action**: Suggest rebasing or manual merge review.

---

### Edge Case 2: project.db Query Fails

**Issue**: Can't query database to get function metadata

**Handling**:
```python
try:
    # Use project query helper
    functions = get_from_project_where('functions', {'name': fn_name})
    purity_levels = [f['purity_level'] for f in functions]
except DatabaseError:
    # Fall back to file-level detection only
    return ConflictAnalysis(
        has_conflicts=True,
        conflicts=[...],  # File-level conflicts only
        note="Database query failed - function-level analysis unavailable",
        recommendation="Manual review recommended (couldn't access function metadata)"
    )
```

**Directive Action**: Graceful degradation to file-level detection.

---

### Edge Case 3: AST Parsing Fails (Syntax Errors)

**Issue**: Can't parse code to extract functions

**Handling**:
```python
try:
    ast_tree = ast.parse(source_code)
except SyntaxError as e:
    # Syntax error in code
    return ConflictAnalysis(
        has_conflicts=True,
        parse_error=True,
        error_message=f"Syntax error in {file_path}: {e}",
        recommendation="Fix syntax errors before merging",
        manual_merge_required=True
    )
```

**Directive Action**: Report syntax error, block merge until fixed.

---

### Edge Case 4: Binary File Conflicts

**Issue**: Binary files (images, PDFs) modified in both branches

**Handling**:
```python
# Detect binary files
if is_binary_file(file_path):
    return ConflictDetail(
        type="binary_file",
        file=file_path,
        recommendation="Manual resolution required (binary file)",
        confidence=0.0,
        auto_resolvable=False,
        note="Binary files cannot be auto-merged"
    )
```

**Directive Action**: Flag as manual resolution required.

---

## Related Directives

- **Called By**:
  - `git_merge_branch` - Analyzes conflicts before merge
  - User commands - Manual conflict check requests
- **Calls**:
  - Git CLI commands for diff analysis
  - Database query helpers for metadata
  - AST parsers for function extraction
- **Triggers**:
  - `git_merge_branch` - If conflicts detected, merge directive handles resolution
- **Related**:
  - `fp_purity` - Validates function purity levels used in analysis
  - `project_update_db` - Updates function metadata after resolution

---

## Helper Functions

Query `get_helpers_for_directive()` to discover this directive's available helpers.
See system prompt for usage.
---

## Database Operations

This directive reads the following tables (does NOT modify):

- **`functions`**: Reads purity_level, parameters, side_effects_json for conflict analysis
- **`interactions`**: Reads dependencies to compare function call graphs
- **`tasks`, `themes`, `flows`**: Compares for database-level conflicts
- **`notes`**: Logs conflict detection failures or warnings

---

## Testing

How to verify this directive is working:

1. **No conflicts** → Returns clean analysis
   ```python
   analysis = git_detect_conflicts("aimfp-alice-001", "main")
   assert analysis.has_conflicts == False
   ```

2. **Different files** → Marked as safe
   ```python
   # Alice: src/matrix.py, Bob: src/vector.py (no overlap)
   analysis = git_detect_conflicts("aimfp-alice-001", "main")
   assert analysis.auto_resolvable_count == analysis.conflict_count
   ```

3. **One pure, one impure** → Recommends pure version
   ```python
   analysis = git_detect_conflicts("aimfp-bob-001", "main")
   conflict = analysis.conflicts[0]
   assert conflict.recommendation == "Keep Bob's version (pure)"
   assert conflict.confidence > 0.8
   ```

4. **Database conflict** → Detected correctly
   ```python
   analysis = git_detect_conflicts("aimfp-alice-001", "main")
   db_conflicts = [c for c in analysis.conflicts if c.type == "database"]
   assert len(db_conflicts) > 0
   ```

---

## Common Mistakes

- ❌ **Not checking database conflicts** - Only analyzing code files
- ❌ **Ignoring FP purity levels** - Not using function metadata
- ❌ **Not handling parse errors** - Assuming code is always valid
- ❌ **Skipping test comparison** - Missing key data for recommendations
- ❌ **No confidence scoring** - Can't determine auto-resolvability

---

## Roadblocks and Resolutions

### Roadblock 1: git_diff_fails
**Issue**: Git diff command fails (branch doesn't exist)
**Resolution**: Check branch existence first, return error if branch not found

### Roadblock 2: project_db_query_fails
**Issue**: Can't query database for function metadata
**Resolution**: Fall back to file-level conflict detection only, warn user of limited analysis

### Roadblock 3: branch_diverged_too_much
**Issue**: Branches have diverged significantly (100+ commits)
**Resolution**: Suggest rebasing or manual merge, provide summary instead of detailed analysis

---

## References

None
---

*Part of AIMFP v1.0 - Git integration directive for FP-powered conflict detection*
