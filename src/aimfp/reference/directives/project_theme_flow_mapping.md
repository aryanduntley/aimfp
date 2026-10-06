# Directive: project_theme_flow_mapping

**Type**: Project
**Level**: 3 (Operational Execution)
**Parent Directive**: project_file_write
**Priority**: HIGH - Modularity is how AIMFP projects stay organized and maintainable

---

## Purpose

`project_theme_flow_mapping` maintains the project's **modularity layer**:

```
completion path --themes--> theme --flows--> flow --files--> file --module--> module
milestone ------flows-----> flow
task / sidequest --flow_ids--> flow
```

| Layer | What it is | How it changes |
|---|---|---|
| **Theme** | A stable project area ("Authentication", "Data Layer") | Rarely — like completion paths, which link to it |
| **Flow** | One distinct behaviour the code implements ("Login Flow") | Continuously — milestones add flows and split ones that grow; every flow belongs to ≥ 1 theme |
| **Module** | A reusable domain code boundary at a directory seam | When reusable logic appears; feature files stay thin orchestrators |
| **file → flow** | Which behaviour a file implements | Declared at `reserve_file` (`flow_ids`), or an explicit `null` for config/data files |

Interactions record how functions call each other; themes, flows and modules record **where code belongs**. Without them AI work drifts into bulk: one flow that describes everything, files nobody can place, logic duplicated because no module said it already existed.

---

## When to Apply

- **Session start / status**: `aimfp_status` and `aimfp_run` carry `structure_summary` and `structure_health`. If `structure_health.ok` is false, fix the gaps during the session.
- **Watchdog reminders** typed `structure_*` (they are recomputed at every checkpoint and persist until fixed).
- **New behaviour**: a task or milestone builds something no flow describes.
- **A flow grows**: its description starts covering several behaviours.
- **Milestone completion**: `project_milestone_complete` runs a flow review that routes here.
- **Codebase adoption**: after `project_catalog` registers files.

---

## Workflow

### Trunk: assess_structure

Call `get_structure_health()`. Read the summary (themes → flows → file counts, open paths → themes, open milestones → flows) and the gaps. Every tool below is batch-native: pass many pairs in one call.

### Branches

**new_behaviour_without_flow → create_flow_for_behaviour**
1. Pick the theme(s) from the summary; `add_theme` only if no area fits.
2. `add_flow(name, theme_ids, description)` — one flow per distinct behaviour.
3. `add_milestone_flows([[milestone_id, flow_id]])` for the milestone building it.
4. Include it in the task's `flow_ids` (`update_task`).

Do **not** stretch an unrelated flow's description to cover new behaviour. That is the failure this directive exists to prevent.

**files_without_flow → link_files_to_flows**
- `add_file_flows([[file_id, flow_id], ...])` for files implementing a flow.
- `update_file(file_id, no_flow_reason=...)` for config, data or fixture files that truly have no flow. New files declare this at reserve time with `flow_ids=null`.

**files_outside_module → assign_files_to_modules**
- A file under a module's path that is not a member: `add_files_to_module`.

**flows_without_theme → link_flows_to_themes**
- `add_flow_themes([[flow_id, theme_id], ...])`.

**paths_or_milestones_unlinked → link_paths_and_milestones**
- `add_path_themes([[completion_path_id, theme_id], ...])`
- `add_milestone_flows([[milestone_id, flow_id], ...])`

**flow_oversized_or_mixed → split_flow**
A description past ~1500 characters (`oversized_flows`) has usually absorbed several behaviours.
1. `add_flow` per distinct behaviour (same `theme_ids` unless it belongs elsewhere).
2. `move_files_to_flow(file_ids, from_flow_id, to_flow_id)` for each new flow's files — links only, files are never touched.
3. `add_milestone_flows` for affected open milestones; update open tasks' `flow_ids`.
4. `update_flow` the original to describe only what remains.

**flows_change_theme → regroup_flows**
- `move_flows_to_theme(flow_ids, from_theme_id, to_theme_id)`.

**links_removed → remove_links_with_note**
- `remove_flow_themes`, `remove_path_themes`, `remove_milestone_flows` require `note_reason`, `note_severity`, `note_source` (an `entry_deletion` note is written per owner).
- Refused while something depends on the link: a flow's last theme (use `move_flows_to_theme`), a milestone flow still listed by that milestone's open tasks or sidequests.
- Deletes treat these links as associations too: `delete_theme`, `delete_flow`, `delete_milestone` and `delete_completion_path` are blocked until linked paths, milestones, flows or themes are unlinked.

**structure_changed → call_project_evolution**
- `add_note(note_type='evolution')` for every theme/flow added, split, regrouped or retired, and update blueprint section 3.

**Fallback → prompt_user**: "Which theme/flow should this work belong to?"

---

## Examples

### Splitting a catch-all flow

`structure_health.oversized_flows` reports flow 11 "Automation Flow" at 7,400 characters covering scheduling, config validation and monitoring.

```
add_flow("Schedule Arithmetic Flow", theme_ids=[3], description="...")      -> 21
add_flow("Config Grammar Flow", theme_ids=[3], description="...")           -> 22
move_files_to_flow([40, 41], from_flow_id=11, to_flow_id=21)
move_files_to_flow([42], from_flow_id=11, to_flow_id=22)
add_milestone_flows([[6, 21], [6, 22]])
update_flow(11, description="<monitoring only>")
add_note(note_type='evolution', reference_table='flows', reference_id=11, content="Split ...")
```

### Fixing a backlog after adoption

```
add_flow_themes([[1, 2], [2, 2], [3, 5]])
add_file_flows([[10, 1], [11, 1], [12, 3]])
update_file(13, no_flow_reason="build config")
add_files_to_module([10, 11], module_id=4)
get_structure_health()   # re-check; lists are capped, totals are exact
```

---

## Integration with Other Directives

- **Called by**: `project_file_write`, `project_milestone_complete` (flow review), `project_discovery` (initial layer), `project_catalog` (adoption), user requests.
- **Calls**: `project_evolution` when the layer changes.
- **Helpers**: query `get_helpers_for_directive('project_theme_flow_mapping')` for the current set.

---

## Database Updates

- `themes`, `flows` — created and described
- `flow_themes`, `completion_path_themes`, `milestone_flows`, `file_flows`, `module_files` — links
- `files.no_flow_reason` — explicit flow opt-out
- `notes` — evolution and entry_deletion audit trail

---

## Roadblocks and Resolutions

- **ambiguous_mapping**: a file serves two behaviours → link both flows; if that keeps happening, the file probably needs splitting.
- **no_fitting_theme**: create the theme only after confirming with the user; themes are long-lived.
- **removal_refused**: resolve the listed associations first (link a replacement theme, update task `flow_ids`), then remove.
- **legacy_project_gaps**: hundreds of gaps after an upgrade → work in batches per theme; the watchdog shows 15 per gap type plus a count.

---

## Notes

- Flows are not tasks: a flow describes lasting behaviour, a task describes work. Many tasks touch one flow.
- Themes are not modules: a theme is an area of the product, a module is a code boundary. A module's files often span one theme; a theme usually spans several modules.
- The same report drives `aimfp_status`, `aimfp_end`, `get_structure_health` and the watchdog, so they never disagree.
