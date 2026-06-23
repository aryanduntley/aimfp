# Directive: git_merge_branch

**Type**: Git
**Level**: 2
**Parent Directive**: project_task_update
**Priority**: HIGH - Critical for collaboration

---

## Purpose

The `git_merge_branch` directive merges user/AI work branches into the main branch using **FP-powered intelligent conflict resolution**. This directive is the culmination of AIMFP's Git collaboration workflow, combining Git's version control with AIMFP's functional programming intelligence to make merging **dramatically simpler and safer** than traditional OOP codebases.

> **With use of InterCommAIMFP (multi-agent parallel merge):** `git_merge_branch` reconciles **source code only**. The committed `project.db` must **not** be git-merged as a binary blob — instead reconcile DB state per worker branch with `export_state_changeset` → `apply_state_changeset` (and `detect_state_conflicts` to plan). See `docs/intercommaimfptools/`.

This directive is **essential for multi-user collaboration** because:
- **FP-guided resolution**: Uses function purity levels to automatically resolve conflicts
- **High-confidence auto-merge**: Resolves conflicts with >80% confidence automatically
- **AI recommendations**: Presents low-confidence conflicts with intelligent suggestions
- **Full audit trail**: Logs all merge decisions to merge_history table
- **Database merging**: Intelligently merges project.db conflicts
- **Test-driven**: Prefers versions with better test coverage
- **Safe by default**: Validates before merging, aborts on critical errors

The merge workflow:
1. **Validate merge request** - Check branch exists, no uncommitted changes
2. **Call git_detect_conflicts** - Analyze potential conflicts with FP intelligence
3. **Auto-resolve high-confidence conflicts** - Apply FP purity rules (confidence > 0.8)
4. **Present remaining conflicts** - Show user low-confidence conflicts with AI recommendations
5. **Complete merge** - Execute Git merge and database merge
6. **Log merge history** - Record detailed resolution strategy
7. **Update branch status** - Mark work branch as 'merged'

**Key Advantage**: AIMFP's pure functional code is **inherently easier to merge** because:
- Pure functions are deterministic (testable)
- No hidden state (explicit dependencies)
- No class hierarchies (no inheritance conflicts)
- Database tracks all function metadata (informed decisions)

---

## When to Apply

This directive applies when:
- **Called by `project_task_update`** - After completing task work
- **User requests merge** - "Merge my branch", "integrate changes"
- **Work branch complete** - Feature/task implementation finished
- **Pull request workflow** - Merging reviewed changes
- **Multi-user synchronization** - Integrating parallel work

---

## Workflow

### Trunk: validate_merge_request

Validates that merge can proceed safely.

**Steps**:
1. **Check source branch exists** - Verify branch name is valid
2. **Check no uncommitted changes** - Working directory must be clean
3. **Check merge target** - Default to 'main', allow custom target
4. **Route to conflict detection** - Proceed to analysis

### Branches

**Branch 1: If branch_not_found**
- **Then**: `return_error`
- **Details**: Source branch doesn't exist
  - Error: "Branch '{branch_name}' does not exist"
  - Suggestion: "Check branch name with: git branch -a"
  - Log error to notes table
- **Result**: Merge aborted

**Branch 2: If uncommitted_changes**
- **Then**: `prompt_commit_first`
- **Details**: Working directory has uncommitted changes
  - Warning: "Uncommitted changes in working directory"
  - Options:
    1. Commit changes: `git add . && git commit -m "..."`
    2. Stash changes: `git stash`
    3. Discard changes: `git reset --hard` (dangerous)
  - Prompt user for action
  - Wait for user to resolve
- **Result**: User commits or stashes changes

**Branch 3: If validation_passed**
- **Then**: `call_git_detect_conflicts`
- **Details**: Run conflict detection analysis
  - Call: `git_detect_conflicts(source=branch_name, target='main')`
  - Get back: ConflictAnalysis object
    ```python
    {
      "has_conflicts": bool,
      "conflict_count": int,
      "auto_resolvable_count": int,
      "manual_count": int,
      "conflicts": list[ConflictDetail],
      "recommendations": list[ResolutionStrategy]
    }
    ```
  - Store analysis for next steps
- **Result**: Conflict analysis available

**Branch 4: If no_conflicts**
- **Then**: `perform_merge`
- **Details**: Clean merge (no conflicts detected)
  - Command: `git merge {branch_name}`
  - Expected: Fast-forward or three-way merge
  - Verify: No merge conflicts in Git
  - Success message: "Merged {branch_name} into main (no conflicts)"
- **Result**: Branch merged successfully

**Branch 5: If conflicts_detected**
- **Then**: `analyze_auto_resolvable`
- **Details**: Check which conflicts can be auto-resolved
  - Filter: `conflicts.where(confidence > 0.8)`
  - Categorize:
    - **Auto-resolvable**: High confidence (>80%)
    - **Manual review**: Low confidence (<80%)
  - Count both categories
- **Result**: Conflicts categorized by resolvability

**Branch 6: If all_auto_resolvable**
- **Then**: `prompt_auto_resolution`
- **Details**: All conflicts have high-confidence resolutions
  - Message:
    ```
    🔀 Merge Analysis: {branch_name} → main

    Conflicts detected: {count}
    All conflicts can be auto-resolved using FP purity analysis

    Auto-resolution strategy:
    • Prefer pure functions over impure ({x} conflicts)
    • Prefer versions with more tests ({y} conflicts)
    • Keep both versions with rename ({z} conflicts)

    Proceed with automatic resolution? (y/n): _
    ```
  - Wait for user approval
- **Result**: User approves or rejects auto-resolution

**Branch 7: If user_approves_auto**
- **Then**: `apply_auto_resolutions`
- **Details**: Apply AI-determined resolutions automatically
  - Resolution strategies:
    1. **prefer_pure_functions**:
       - If one version is pure, other is impure
       - Action: Keep pure version
       - Example: Replace impure `calculate_total` with pure version
    2. **prefer_more_tests**:
       - Both versions pure, different implementations
       - Action: Keep version with more passing tests
       - Example: Keep Bob's version (15 tests) over Alice's (10 tests)
    3. **keep_both_if_equal**:
       - Both versions pure, equal test coverage
       - Action: Rename and keep both
       - Example: `multiply_matrices()` and `multiply_matrices_optimized()`
    4. **log_decisions**:
       - Record each resolution to merge_history
       - Store reasoning for audit trail
  - Apply resolutions to files
  - Update function metadata in project.db
- **Result**: Conflicts resolved automatically

**Branch 8: If manual_conflicts_remain**
- **Then**: `present_conflicts_to_user`
- **Details**: Show low-confidence conflicts with AI recommendations
  - Format: Interactive conflict resolution UI
  - For each conflict, show:
    ```
    🔀 Conflict #{n}: {function_name} in {file_path}

    📊 Alice's Version (aimfp-alice-001):
       - Purity: ✅ Pure function
       - Parameters: items: tuple[float, ...], tax_rate: float
       - Dependencies: validate_items
       - Tests: 10/10 passing
       - Lines: 15
       - Commit: 3 hours ago

    📊 Bob's Version (main):
       - Purity: ✅ Pure function
       - Parameters: items: tuple[float, ...], tax_rate: float, discount: float
       - Dependencies: validate_items, apply_discount
       - Tests: 12/12 passing (includes discount edge cases)
       - Lines: 22
       - Commit: 1 hour ago

    🤖 AI Recommendation: Keep Bob's version
       Reason: More comprehensive (handles discounts), more tests
       Confidence: 75% (moderate)

    Alternatives:
    1. Keep Alice's version
    2. Keep Bob's version (AI recommended)
    3. Keep both (rename Alice's to calculate_total_simple)
    4. Manual merge in editor

    Your choice (1-4): _
    ```
  - Wait for user input
  - Validate user choice
- **Result**: User provides resolution for each conflict

**Branch 9: If user_resolves_conflicts**
- **Then**: `apply_user_resolutions`
- **Details**: Apply user's resolution choices
  - Resolution options:
    - **keep_alice**: Use Alice's version, discard Bob's
    - **keep_bob**: Use Bob's version, discard Alice's
    - **keep_both_rename**: Rename one function, keep both
    - **manual_merge**: Open conflict in editor for manual resolution
  - Apply chosen resolutions to files
  - Update function metadata in project.db
  - Log user decisions to merge_history
- **Result**: All conflicts resolved

**Branch 10: If all_conflicts_resolved**
- **Then**: `complete_merge`
- **Details**: Execute final merge with resolutions
  - Actions:
    1. **git_merge**:
       ```bash
       git merge {branch_name}
       # Or if conflicts manually resolved:
       git add .
       git merge --continue
       ```
    2. **resolve_db_conflicts**:
       - Merge project.db from both branches
       - Apply FP-based resolution for function metadata
       - Keep append-only inserts (new functions, tasks)
       - Resolve conflicting rows using timestamps and purity
    3. **update_project_db**:
       - Sync files table with merged code
       - Update functions table with resolved functions
       - Update interactions for new dependencies
  - Verify merge successful
- **Result**: Merge completed

**Branch 11: If merge_successful**
- **Then**: `log_merge_history`
- **Details**: Record merge to merge_history table
  - Insert:
    **Use helper functions** for database operations. Query available helpers for the appropriate database.
  - Provides full audit trail of merge decisions
- **Result**: Merge history logged

**Branch 12: If history_logged**
- **Then**: `update_work_branches`
- **Details**: Mark branch as merged in work_branches table
  - Update:
    **Use helper functions** for database operations. Query available helpers for the appropriate database.
  - Branch now marked as inactive (merged)
  - Can be deleted from Git if desired
- **Result**: Branch status updated

**Branch 13: If all_complete**
- **Then**: `report_success`
- **Details**: Present merge completion summary
  - Show:
    ```
    ✅ Merge Complete: aimfp-alice-001 → main

    📊 Summary:
    • Conflicts detected: 5
    • Auto-resolved: 3 (FP purity analysis)
    • Manual resolved: 2 (user decisions)
    • Functions merged: 12
    • Files changed: 5
    • Tests: All passing ✅
    • Merge commit: abc123def456

    🔀 Resolution Strategy:
    • Preferred pure functions (3 conflicts)
    • Kept versions with more tests (2 conflicts)
    • User resolved complex logic (2 conflicts)

    📝 Branch Status:
    • aimfp-alice-001: Merged (can be deleted)
    • work_branches table: Updated
    • merge_history table: Logged

    Next steps:
    • Push to remote: git push origin main
    • Delete local branch: git branch -d aimfp-alice-001
    • Start new work: git_create_branch(...)
    ```
  - Merge workflow complete
- **Result**: User informed of successful merge

**Fallback**: `abort_merge`
- **Details**: Merge failed, abort and cleanup
  - Command: `git merge --abort`
  - Rollback: Restore working directory to pre-merge state
  - Log failure:
    **Use helper functions** for all project.db operations. Query available helpers.

**IMPORTANT**: Never use direct SQL for project.db - always use helpers or call project directives (like project_file_write).
  - Suggest: Review conflicts manually or resolve issues
- **Result**: Merge aborted, state restored

---

## Examples

### ✅ Compliant Usage

**Clean Merge (No Conflicts):**
```bash
# Alice finished work on authentication feature
# AI calls: git_merge_branch(branch="aimfp-alice-001", target="main")

# Workflow:
# 1. Validate: Branch exists ✅, no uncommitted changes ✅
# 2. git_detect_conflicts: No conflicts (different files)
# 3. perform_merge:
#    git checkout main
#    git merge aimfp-alice-001
#    → Fast-forward merge (no conflicts)
# 4. log_merge_history: Record clean merge
# 5. update_work_branches: Mark branch as merged
#
# Result:
# ✅ Merge Complete: aimfp-alice-001 → main
# ✅ No conflicts detected
# ✅ All tests passing
# ✅ Branch marked as merged
```

---

**Auto-Resolvable Conflicts (High Confidence):**
```python
# Alice and Bob both modified calculate_total()
# AI calls: git_merge_branch(branch="aimfp-bob-001", target="main")

# Workflow:
# 1. Validate: ✅
# 2. git_detect_conflicts:
#    → 1 conflict: calculate_total() modified in both
#    → Analysis: Alice's version impure, Bob's version pure
#    → Recommendation: Keep Bob's version (confidence: 0.9)
# 3. analyze_auto_resolvable: 1 conflict, all auto-resolvable
# 4. prompt_auto_resolution:
#    """
#    Conflicts detected: 1
#    All conflicts can be auto-resolved using FP purity analysis
#
#    Auto-resolution strategy:
#    • Prefer pure function over impure (1 conflict)
#
#    Proceed? (y/n):
#    """
# 5. User inputs: y
# 6. apply_auto_resolutions:
#    - Replace impure calculate_total() with pure version
#    - Update functions table: purity_level = 'pure'
# 7. complete_merge: git merge aimfp-bob-001
# 8. log_merge_history:
#    {
#      "auto_resolved": [
#        {"function": "calculate_total", "strategy": "prefer_pure", "confidence": 0.9}
#      ]
#    }
# 9. update_work_branches: status='merged'
#
# Result:
# ✅ Merge Complete (1 conflict auto-resolved)
# ✅ Preferred pure function
# ✅ Full audit trail logged
```

---

**Manual Conflict Resolution:**
```python
# Alice and Bob both created multiply_matrices() (new function)
# Both versions are pure, equal test coverage, different algorithms
# AI calls: git_merge_branch(branch="aimfp-alice-001", target="main")

# Workflow:
# 1. Validate: ✅
# 2. git_detect_conflicts:
#    → 1 conflict: multiply_matrices() added in both branches
#    → Both pure, equal tests (10 each)
#    → Recommendation: Keep both (confidence: 0.7 - moderate)
# 3. analyze_auto_resolvable: 1 conflict, confidence < 0.8 (manual review)
# 4. present_conflicts_to_user:
#    """
#    🔀 Conflict #1: multiply_matrices in src/matrix.py
#
#    📊 Alice's Version:
#       - Purity: ✅ Pure
#       - Algorithm: Standard row-column multiplication
#       - Tests: 10/10 passing
#       - Lines: 20
#
#    📊 Bob's Version:
#       - Purity: ✅ Pure
#       - Algorithm: Strassen's algorithm (optimized)
#       - Tests: 10/10 passing
#       - Lines: 35
#
#    🤖 AI Recommendation: Keep both versions
#       Reason: Both pure, equal tests, different algorithms (user preference)
#       Confidence: 70%
#
#    Alternatives:
#    1. Keep Alice's version (simpler)
#    2. Keep Bob's version (optimized)
#    3. Keep both (rename Bob's to multiply_matrices_optimized) ← AI recommended
#    4. Manual merge
#
#    Your choice (1-4):
#    """
# 5. User inputs: 3 (keep both)
# 6. apply_user_resolutions:
#    - Keep Alice's as multiply_matrices()
#    - Rename Bob's to multiply_matrices_optimized()
#    - Update functions table with both entries
# 7. complete_merge: git merge aimfp-alice-001
# 8. log_merge_history:
#    {
#      "manual_resolved": [
#        {
#          "function": "multiply_matrices",
#          "user_choice": "keep_both",
#          "reason": "User chose to keep both algorithms (standard and optimized)"
#        }
#      ]
#    }
# 9. update_work_branches: status='merged'
#
# Result:
# ✅ Merge Complete (1 conflict manually resolved)
# ✅ Kept both function versions
# ✅ User decision logged
```

---

### ❌ Non-Compliant Usage

**Merging Without Conflict Detection:**
```bash
# ❌ Direct Git merge
git merge aimfp-alice-001
# No FP analysis, no AI recommendations
```

**Why Non-Compliant**:
- Doesn't use FP intelligence
- No confidence scoring
- Missing audit trail
- Database conflicts not handled

**Corrected:**
```python
# ✅ Use git_merge_branch directive
git_merge_branch(branch="aimfp-alice-001", target="main")
# Includes FP analysis, AI recommendations, full logging
```

---

**Not Logging Merge History:**
```bash
# ❌ Just performing merge
git merge aimfp-alice-001
# Forgot to log to merge_history table
```

**Why Non-Compliant**:
- No audit trail
- Can't review past merge decisions
- Lost context for future conflicts

**Corrected:**
```python
# ✅ Directive logs automatically
git_merge_branch(branch="aimfp-alice-001", target="main")
# merge_history table updated with full details
```

---

## Edge Cases

### Edge Case 1: Tests Fail After Merge

**Issue**: Merge completes but tests fail

**Handling**:
```python
# After merge, run tests
test_result = run_tests(project_root)

if not test_result.all_passing:
    # Tests failed after merge
    print(f"⚠️  Tests failed after merge: {test_result.failures}")

    # Options:
    # 1. Abort merge: git merge --abort
    # 2. Debug failures
    # 3. Adjust merge resolution

    user_choice = prompt("Abort merge? (y/n): ")
    if user_choice == 'y':
        subprocess.run(['git', 'merge', '--abort'])
        return MergeResult(success=False, reason="Tests failed after merge")
```

**Directive Action**: Abort merge if tests fail, suggest alternative resolution.

---

### Edge Case 2: Database Merge Conflict (Same Task ID)

**Issue**: Both branches modified same task status

**Handling**:
```python
# Extract both database versions
alice_db = extract_db_from_branch("aimfp-alice-001")
main_db = extract_db_from_branch("main")

# Compare tasks table
# Use project query helper
alice_task = get_from_project_where('tasks', {'id': 15})[0]
main_task = get_from_project_where('tasks', {'id': 15})[0]

if alice_task != main_task:
    # Task conflict
    present_db_conflict_to_user({
        "table": "tasks",
        "id": 15,
        "alice_version": {"status": "completed", "updated_at": "..."},
        "main_version": {"status": "in_progress", "updated_at": "..."},
        "recommendation": "Keep more recent timestamp"
    })
```

**Directive Action**: Present database conflicts to user for resolution.

---

### Edge Case 3: Branch Already Merged

**Issue**: User tries to merge branch that was already merged

**Handling**:
```python
# Use project query helper
branches = get_from_project_where('work_branches', {'branch_name': branch_name})
branch_status = branches[0]['status'] if branches else None

if branch_status and branch_status[0] == 'merged':
    return MergeResult(
        success=False,
        error="Branch already merged",
        suggestion="Branch was merged previously. Delete with: git branch -d {branch_name}"
    )
```

**Directive Action**: Inform user branch already merged, suggest cleanup.

---

### Edge Case 4: Merge Would Overwrite Uncommitted Changes

**Issue**: User has uncommitted changes that would be overwritten

**Handling**:
```bash
# Check for uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo "⚠️  Uncommitted changes would be overwritten by merge"
    echo ""
    echo "Options:"
    echo "1. Commit your changes: git add . && git commit -m '...'"
    echo "2. Stash your changes: git stash"
    echo "3. Abort merge"

    # Block merge until resolved
    return
fi
```

**Directive Action**: Block merge, prompt user to commit or stash changes first.

---

## Related Directives

- **Called By**:
  - `project_task_update` - After task completion
  - User commands - Direct merge requests
- **Calls**:
  - `git_detect_conflicts` - Analyze conflicts before merging
  - `project_update_db` - Sync database after merge
- **Triggers**:
  - `git_sync_state` - Update Git hash after merge
- **Related**:
  - `git_create_branch` - Creates branches that get merged
  - `fp_purity` - Purity rules used for conflict resolution

---

## Helper Functions

Query `get_helpers_for_directive()` to discover this directive's available helpers.
See system prompt for usage.
---

## Database Operations

This directive updates the following tables:

- **`merge_history`**: INSERT new merge record with detailed resolution log
- **`work_branches`**: UPDATE status to 'merged', set merged_at timestamp
- **`functions`**: UPDATE after resolving function conflicts (purity, parameters)
- **`files`**: UPDATE checksums after merge
- **`notes`**: INSERT merge failures or warnings

---

## Testing

How to verify this directive is working:

1. **Clean merge** → No conflicts, successful
   ```python
   result = git_merge_branch("aimfp-alice-001", "main")
   assert result.success == True
   assert result.conflicts_resolved == 0
   ```

2. **Auto-resolvable conflicts** → Resolved by AI
   ```python
   result = git_merge_branch("aimfp-bob-001", "main")
   assert result.auto_resolved_count > 0
   assert result.success == True
   ```

3. **Manual conflicts** → User prompted
   ```python
   result = git_merge_branch("aimfp-alice-001", "main")
   assert result.manual_count > 0
   # User was prompted for resolution
   ```

4. **Merge history logged** → Audit trail exists
   **Use helper functions** for database operations. Query available helpers for the appropriate database.

---

## Common Mistakes

- ❌ **Not calling git_detect_conflicts first** - Missing conflict analysis
- ❌ **Not logging merge history** - No audit trail
- ❌ **Not updating work_branches status** - Branch tracking broken
- ❌ **Ignoring database conflicts** - project.db becomes inconsistent
- ❌ **Not running tests after merge** - Merged code may be broken

---

## Roadblocks and Resolutions

### Roadblock 1: merge_conflicts_complex
**Issue**: Conflicts too complex for automatic resolution
**Resolution**: Present to user with AI recommendations, offer manual merge option

### Roadblock 2: database_merge_fails
**Issue**: Can't merge project.db (corruption or conflicts)
**Resolution**: Extract both databases, merge at SQL level, present conflicts to user

### Roadblock 3: tests_fail_after_merge
**Issue**: All conflicts resolved but tests fail after merge
**Resolution**: Abort merge, report test failures, suggest alternative resolution strategy

---

## AIMFP Commit Message Format

When merging branches, AIMFP generates structured commit messages that document the merge details for audit and context:

### Standard Format

```
[AIMFP] {user}: {action}: {description}

{detailed_notes}

- Functions: {function_list}
- Files: {file_list}
- Purity: {purity_summary}
- Dependencies: {dependency_list}
- Task: #{task_id}
- Tests: {test_summary}

Auto-generated by AIMFP v1.0
Branch: {branch_name}
```

### Merge Commit Example

```
[AIMFP] Alice: Merge branch: aimfp-alice-001 → main

Implemented matrix multiplication with dimension validation and FP compliance.

Conflicts resolved:
- 1 auto-resolved (prefer pure function)
- 0 manual resolutions

- Functions: multiply_matrices (pure), validate_dimensions (pure)
- Files: src/matrix.py, tests/test_matrix.py
- Purity: 100% pure (no side effects)
- Dependencies: validate_dimensions, compute_product
- Task: #15
- Tests: 12/12 passing (includes edge cases)

Auto-generated by AIMFP v1.0
Branch: aimfp-alice-001
Merge strategy: FP-powered conflict resolution
Confidence: 90% auto-resolvable
```

### Benefits of Structured Commits

- **Audit trail**: Full record of what was merged and why
- **FP compliance**: Documents purity levels of merged code
- **Conflict transparency**: Shows how conflicts were resolved
- **Searchable**: Easy to find merges by function, task, or file
- **AI context**: Future AI sessions can understand merge history

---

## References

None
---

*Part of AIMFP v1.0 - Git integration directive for FP-powered branch merging*
