"""
AIMFP Helper Functions - Semantic Changeset Merge (changeset/)

Tools for exporting and applying semantic changesets of project.db state across
parallel clones — the AIMFP-side machinery that lets InterCommAIMFP merge the
project-state DB of N worker agents without binary-blob conflicts, PK collisions, or
graph corruption.

See docs/intercommaimfptools/toolsneeded.md (design spec) and IMPLEMENTATION-PLAN.md.

Modules:
- _common.py  : shared helpers (slug backfill, db-at-commit extraction, key indexes,
                InterComm presence gating, changeset persistence/handle, summary)
- backfill.py : backfill_semantic_keys   (optional one-shot key population)
- export.py   : export_state_changeset   (persists changeset, returns changeset_id + summary)
- apply.py    : apply_state_changeset    (accepts inline changeset OR changeset_id handle)
- conflicts.py: detect_state_conflicts   (cross-branch overlap planning aid)
- summarize.py: summarize_state_changeset (cheap per-branch counts view)
- merge.py    : merge_worker_branch / merge_worker_branches (branch-ref orchestrators)
- preflight.py: verify_fanout_ready      (pre-fan-out readiness check on main)
- partition.py: plan_disjoint_partitions (work-graph-aware disjoint ownership planner)
- history.py  : get_merge_history        (provenance / idempotency over merge_history)
"""
