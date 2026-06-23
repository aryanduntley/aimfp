"""
AIMFP Helper Functions - Semantic Changeset Merge (changeset/)

Tools for exporting and applying semantic changesets of project.db state across
parallel clones — the AIMFP-side machinery that lets InterCommAIMFP merge the
project-state DB of N worker agents without binary-blob conflicts, PK collisions, or
graph corruption.

See docs/intercommaimfptools/toolsneeded.md (design spec) and IMPLEMENTATION-PLAN.md.

Modules:
- _common.py  : shared helpers (slug backfill effect; db-at-commit extraction added in Phase 2)
- backfill.py : backfill_semantic_keys (optional one-shot slug population)
- export.py   : export_state_changeset            (Phase 2)
- apply.py    : apply_state_changeset             (Phase 3)
- conflicts.py: detect_state_conflicts            (Phase 4)
"""
