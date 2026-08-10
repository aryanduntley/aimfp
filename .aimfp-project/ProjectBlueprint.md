# AIMFP - Project Blueprint

**Version**: 1.47.0
**Status**: Released on PyPI — active development & refinement
**Last Updated**: 2026-08-09
**AIMFP Compliance**: Strict
**Self-Tracked**: Yes — AIMFP tracks its own source (see §10)

> This file is the authoritative blueprint and is committed to git.
> `docs/blueprint/ProjectBlueprint.md` is a historical copy; `docs/` is gitignored.

---

## 1. Project Overview

### Idea

Build a Model Context Protocol (MCP) server that gives AI assistants database-driven
directives for **modular functional-procedural** code and full project lifecycle
management. The server enables AI to write pure, modular functional code, manage
project lifecycles, and (Use Case 2) generate/manage automation codebases from
user-defined directives.

AIMFP = **AI Modular Functional Procedural**. The rename from AIFP (2026-03-29)
reflects the central addition of a first-class **modularity** layer: domain logic
lives in reusable modules; feature files are thin orchestrators that compose them.

### Current Phase

**Released & iterating.** The package ships on PyPI (`pip install aimfp`) and as a
Claude Code plugin. Core architecture is complete:

- Four-database architecture (schemas complete, `aimfp_core.db` pre-populated)
- Directive system: 131 MD reference docs + JSON source in `dev/`
- Helper/MCP tool library implemented across `src/aimfp/helpers/`
- Modularity system (modules + module_files, dedicated tools/directives)
- Watchdog: filesystem monitoring + startup reconciliation
- Plugin packaging, system-prompt + permissions automation tools
- Changeset export/apply for parallel-worker `project.db` reconciliation
- svamanas embedding hooks (2026-07-17)

Ongoing work: refinement, bug fixes, tooling ergonomics, watchdog accuracy.
Because the project is released and maintained rather than under linear build-out,
the completion path is a single always-open path (§4), not a staged sequence.

### Goals

- Pure, modular functional implementation (no OOP, no mutations)
- Domain logic in reusable modules; feature files compose, never own business logic
- Immutable data structures throughout (frozen dataclasses, Result/Maybe types)
- Explicit side-effect isolation (`_effect_*` functions at boundaries)
- Production-ready MCP server, installable via pip and as a Claude Code plugin
- Dogfood AIMFP on itself so tool warts surface here first

### Success Criteria

- Complete directive + modularity system, functional across 4 databases
- Zero OOP violations, complete FP compliance
- Reliable cross-session persistent context (no re-discovery cost)
- Successful PyPI releases via tagged CI (trusted publisher)
- Documentation complete with examples

---

## 2. Technical Blueprint

### Language & Runtime

- **Primary Language**: Python 3.11+ (developed on 3.14.4, supports 3.11–3.14)
- **Runtime/Framework**: Custom JSON-RPC 2.0 over stdio (no MCP SDK dependency)
- **Build Tool**: setuptools (`python3 -m build`)
- **Package Manager**: pip; local installs via `py-trkpac install .`
- **Test Framework**: pytest + hypothesis
- **Main Branch**: `main`

### Architecture Style

- **Paradigm**: Modular Functional Procedural (AIMFP)
- **Pattern**: Pure functions with explicit data flow; effects isolated in `_effect_*`
- **State Management**: Immutable structures with Result/Maybe types, no mutations

### Key Infrastructure

- **Custom JSON-RPC 2.0 over stdio**: Zero-dependency MCP server (pure stdlib)
- **SQLite3**: Four-database architecture (core, project, preferences, user directives)
- **watchdog**: The single external runtime dependency — filesystem monitoring for
  external change detection
- **dataclasses**: Immutable structures (`frozen=True`)
- **pytest + hypothesis**: Testing with property-based testing
- **mypy + ruff**: Static type checking and linting

### Package Structure

**Production Package** (`src/aimfp/` — installable via pip, bundled in the plugin,
and the **tracked source directory**):

- `database/` — Schemas, connection/query helpers, the pre-populated `aimfp_core.db`
- `helpers/` — Helper/MCP tool library organized by domain (see §3 modules)
- `mcp_server/` — Protocol handlers, tool registry, request routing
- `watchdog/` — Filesystem watcher, analyzers, startup reconciliation, reminders
- `wrappers/` — Effect wrappers for filesystem/observer I/O
- `reference/directives/` — 131 directive MD docs shipped with the package
- `reference/guides/` — Supportive-context guides (`get_supportive_context`)
- `templates/` — ProjectBlueprint template, state-DB template

**Development Staging** (everything *above* `src/aimfp/` is dev-only, never shipped):

- `dev/directives-json/` — Directive definitions + flows (source of truth)
- `dev/helpers-json/` — Helper definitions (source of truth)
- `dev/sync-directives.py` — Rebuilds `aimfp_core.db` from the JSON
- `dev/bump-version.py` — Syncs the package version across its four locations
- `docs/` — Blueprints, notes, design docs (gitignored archive; **not** for refactors)
- `tests/` — Test suite
- `sys-prompt/` — The installable AIMFP system prompt text

---

## 3. Project Themes, Flows & Modules

### Themes

1. **Database Operations** — connection/query layer + isolated effects, 4 DBs
2. **Helper / MCP Tools** — the helper library exposed as MCP tools
3. **Directive System** — FP + project + user-pref + automation + git directives
4. **Modularity** — modules + module_files; the AIMFP differentiator
5. **MCP Server Framework** — protocol handlers, registry, routing
6. **Watchdog** — monitoring, reconciliation, three exclusion layers
7. **Changeset & Multi-Worker Interop** — semantic project.db merge, InterComm
8. **Build & Release Pipeline** — dev-staging → production process
9. **Testing Infrastructure** — pytest + hypothesis in `tests/`

### Flows

1. **Session Lifecycle Flow** — `aimfp_run` bundle → checkpoints → `aimfp_status` → `aimfp_end`
2. **Initialization Flow** — `aimfp_init` mechanical setup → Phase 2 → `project_discovery`
3. **Directive Execution Flow** — request → routing → directive lookup → helper → return_statements
4. **Project Tracking Flow** — the reserve → write → finalize → assign file coding loop
5. **Watchdog Flow** — startup reconciliation → live monitoring → reminders.json
6. **Tool & Directive Build Pipeline** — see §5; getting the order wrong breaks tools
7. **Release & Publish Flow** — version sync → build → tagged PyPI publish → local install
8. **Changeset Merge Flow** — export → detect conflicts → apply, keyed on slug + entity_key

### Modules (mapped to real directory seams)

| Module | Path | Owns |
|---|---|---|
| `helpers_project` | `src/aimfp/helpers/project/` | All project.db CRUD (20 files) |
| `helpers_changeset` | `src/aimfp/helpers/changeset/` | Semantic state export/apply (11) |
| `helpers_orchestrators` | `src/aimfp/helpers/orchestrators/` | Entry points + migration (8) |
| `helpers_core` | `src/aimfp/helpers/core/` | Read-only aimfp_core.db access (7) |
| `helpers_user_preferences` | `src/aimfp/helpers/user_preferences/` | user_preferences.db (6) |
| `helpers_user_directives` | `src/aimfp/helpers/user_directives/` | user_directives.db, UC2 (6) |
| `helpers_shared` | `src/aimfp/helpers/shared/` | Cross-cutting utilities (5) |
| `helpers_git` | `src/aimfp/helpers/git/` | Git integration (2) |
| `mcp_server` | `src/aimfp/mcp_server/` | JSON-RPC protocol layer (6) |
| `watchdog` | `src/aimfp/watchdog/` | Filesystem monitoring (8) |
| `wrappers` | `src/aimfp/wrappers/` | `_effect_*` I/O boundary (3) |
| `database` | `src/aimfp/database/` | SQLite connection/query + schemas (3) |
| `catalog` | `src/aimfp/helpers/catalog/` | Adopting an existing FP codebase (5) |
| `state` | `src/aimfp/.state/` | DB-backed variable state (2) |

`helpers_shared` is the highest-reuse module — check it before writing any new
utility. `wrappers` is the only place impure I/O belongs.

**Empty leftovers** (no code, safe to remove or fill): `src/aimfp/core/`,
`src/aimfp/database/queries/`, `src/aimfp/database/effects/`,
`src/aimfp/helpers/mcp/`, `src/aimfp/helpers/preferences/`, `src/aimfp/scripts/`.

---

## 4. Completion Path

**One path: "AIMFP Ongoing Development" (in_progress, order 1).**

A released, maintained project has no terminal completion, so the path stays open
and work routes into one of three milestones:

| # | Milestone | Status | Closes? |
|---|---|---|---|
| 1 | **Self-Tracking Port** | in_progress | Yes — on the §10 criteria |
| 2 | **Features** | pending | **No — perpetual** |
| 3 | **Issues & Bugs** | pending | **No — perpetual** |

Ad-hoc work routes through `project_task_decomposition` into Features or Issues &
Bugs. Neither is ever marked `completed`; they are open-ended containers by design.

**Plus the two standard post-completion paths**, which belong on *every* AIMFP
project regardless of type:

| # | Path | Order | Status |
|---|---|---|---|
| 2 | **Added Features** | 998 | completed (ready to reopen) |
| 3 | **Updates** | 999 | completed (ready to reopen) |

Every project is finite, but every *finished* project still needs updates, fixes,
new features, and security patches. These paths exist so that work has somewhere to
go after completion. When it arrives, **reopen** the appropriate path
(`status='in_progress'`, which reverts project completion status) and create
milestones/tasks inside it — never tack new milestones onto a completed path.

---

## 5. The Build Pipeline (tracked as a flow)

Adding or changing an MCP tool touches these **in order**. Getting the order wrong
produces a tool the AI cannot see or cannot call:

```
dev/helpers-json/*.json          ← edit spec (params, purpose, return_statements)
        ↓
dev/sync-directives.py           ← rebuild (delete aimfp_core.db first for a clean build)
        ↓
src/aimfp/database/aimfp_core.db ← ships in the wheel as package-data
        ↓
src/aimfp/mcp_server/registry.py ← module/function dispatch entry
        ↓
allowlist                        ← tool exposure
```

**Why the JSON is not optional**: `server.py` builds each tool's MCP `inputSchema`
from `helper_functions.parameters` in `aimfp_core.db`. A parameter added in Python
but not in the JSON is invisible to the AI, and every call fails with a `TypeError`.
Code and spec are not independently editable.

**Signature changes**: `system_prompt.txt` (and its identical twin
`sys-prompt/aimfp_system_prompt.txt`) carries no call signatures by design — it
defers to `get_supportive_context(variant)`. Signatures live in
`src/aimfp/reference/guides/supportive_context*.txt`, of which there are **four**
variants (core, coding, case2, init). A tool signature change means grepping all four.

**Release**: `python3 dev/bump-version.py` syncs the package version across
`pyproject.toml`, `src/aimfp/__init__.py`, `src/aimfp/mcp_server/server.py`
(`SERVER_VERSION`), and `manifest.json`. It reports schema versions but deliberately
never rewrites them. Then `rm -rf build dist src/*.egg-info && python3 -m build`,
and publish on a `v*` tag via the trusted-publisher workflow.

---

## 6. Repo-Specific Gotchas

- **Two database families, two migration systems.** User DBs (`project.db`,
  `user_preferences.db`, `user_directives.db`) migrate via
  `helpers/orchestrators/migration.py`, driven by `expected_schema_versions` in the
  core DB. `aimfp_core.db` is read-only at runtime and **ships in the wheel** — it is
  replaced wholesale on upgrade and never migrated in place. Content changes to core
  (helper params, directives) are *not* migrations; they are a JSON edit plus rebuild.
- **Schema versions are not the package version.** They are 2-part (`2.2`, `1.11`)
  and bump on their own cadence. A mismatch has two opposite causes (SQL bumped vs.
  DB not rebuilt) needing opposite fixes — which is why `bump-version.py` reports
  but never rewrites them.
- **Four supportive-context variants.** Nearly shipped an incomplete fix once by
  checking only one. Grep all four.
- **`docs/` and `CLAUDE.md` are gitignored.** Anything that must survive a fresh
  clone belongs in `.aimfp-project/`, `README.md`, or the databases.

---

## 7. User Settings System

Per-directive atomic key-value preferences in `user_preferences.db` let users
customize AI behavior without touching core code.

- **Atomic Preferences**: single key-value pairs (e.g. `always_add_docstrings = true`)
- **Directive-Specific**: target a directive (e.g. `project_file_write.max_function_length = 50`)
- **Opt-In Tracking**: all tracking features disabled by default (cost-conscious)
- **AI Learning**: optionally learn from user corrections (with confirmation)

**Tables**: `directive_preferences`, `user_settings`, `tracking_settings`,
`ai_interaction_log`, `fp_flow_tracking`, `issue_reports`, `custom_return_statements`,
`tracking_notes`.

Watchdog exclusions also read here: `watchdog_excluded_dirs` /
`watchdog_excluded_extensions` (JSON lists), complementing `.watchdogignore`.

---

## 8. User Custom Directives System (Use Case 2)

AIMFP itself is a **Use Case 1** project (regular software development) — no
`user_directives.db` exists here. The UC2 subsystem is a *feature we ship*, not a
mode this project runs in.

UC2: user writes directive definitions (YAML/JSON/TXT); AIMFP generates and manages
the automation codebase in `src/`. Adds `user_directives.db` and file-based logs
(30-day execution / 90-day error retention).

---

## 9. Watchdog & Exclusions

The watchdog monitors the project's `source_directory` (`src/aimfp`) for changes
made outside AIMFP tracking, writing nudges to
`.aimfp-project/watchdog/reminders.json`. On startup it reconciles
DB-registered-but-missing files and on-disk-but-unregistered files.

**Three exclusion layers** (merged before matching):

1. **Built-in** — `EXCLUDED_DIRS` / `EXCLUDED_EXTENSIONS` in `watchdog/config.py`
2. **User settings** — `watchdog_excluded_dirs` / `watchdog_excluded_extensions`
3. **`.watchdogignore`** — gitignore-style, at the project root. Patterns with `/`
   anchor to the project root and prune subtrees; patterns without `/` float.
   `#` comments allowed; negation (`!`) not supported.

Because `source_directory` is `src/aimfp`, `dev/`, `docs/`, `tests/`, and `build/`
fall outside the watched tree by construction.

---

## 10. Self-Tracking (Dogfooding)

### Why it is safe

Installation goes through `py-trkpac install .` → `pip install --target <lib> .`,
which is a **build-and-copy**: no editable mode, no symlinks back into the source
tree. Verified 2026-08-09 (installed package resolves to itself, and differs from
the repo working tree).

**The MCP server serving these tracking tools always runs the last installed build,
never `src/`.** Editing `src/` cannot break the tracking session. This is a property
of the tooling, not a rule anyone has to remember.

Consequences to internalize:

1. `src/` edits are invisible to the tracker until the next install — a feature, and
   a staging gate. A breaking tool change does not affect tracking of the work that
   introduces it.
2. **Never `pip install -e .` this repo.** It would defeat the property above.
3. **Never run `py-trkpac install .` mid-session.** Python caches imported modules,
   so a running MCP server keeps the old code in memory and you cannot tell which
   build is answering. Reinstall *between* sessions, then restart the MCP server.

### Tracking scope

**Tracked**: everything under `src/aimfp/`. Fully cataloged as of 2026-08-09:

| | Count |
|---|---|
| Files | 95 |
| Functions | 837 (0 with null purpose) |
| Types | 224 (0 with null description) |
| Interactions | 1,768 (0 unresolved) |
| Type↔function links | 309 |

**Not tracked, with reasons**:

- **`dev/`** — 3 Python build scripts, non-FP and never shipped. Tracking them would
  fire FP compliance directives against procedural build scripts on every pass: pure
  noise for near-zero benefit. The *pipeline* is captured as a flow (§5) instead —
  the pipeline is what matters, not the scripts' internals.
- **`docs/`** — a gitignored archive (~144 files, mostly completed plans). Not active
  staging.
- **`tests/`** — file internals untracked; the Testing Infrastructure theme exists so
  test work has a home. Also excluded in `.watchdogignore`.
- **`build/`, `dist/`, `*.egg-info/`** — generated.

**Backfill strategy — superseded.** The original plan deferred backfill on the
grounds that the port was "a structure exercise, not a data-entry exercise." That
reasoning assumed cataloging meant hand-entering 837 function records. It did not:
the gap was missing *tooling*, and `project_catalog` described a workflow no helper
implemented. Building `scan_source_tree` / `catalog_*` / `scan_call_graph` made a
complete catalog a few minutes of machine work, and AIMFP's own adoption is the
proof the tooling works. Full catalog beats incremental accrual whenever the
extraction is mechanical.

### Git

`.aimfp-project/` is **committed** — `project.db`, `user_preferences.db`, and this
blueprint. A fresh clone keeps its tracking. Precedent: `aimfp_core.db` is already a
committed binary here. Single maintainer means merge conflicts are rare, and the
changeset export/apply tooling exists for parallel workers (which self-tracking then
dogfoods too).

**Excluded from git**: `.aimfp-project/backups/` and `.aimfp-project/watchdog/` —
churny runtime noise with no value in history.

### State database

`src/aimfp/.state/runtime.db` exists, with its `state_operations.py` companion
(module `state`, both fully tracked). It is **standing infrastructure, present by
default** — there in case it is ever needed, not created only when a need appears.

AIMFP stores nothing in it today; that is expected and not a reason to omit it. It
does not ship: setuptools skips dot-prefixed directories, so `.state/` never enters
the wheel. The file is standalone by contract and must never import from the `aimfp`
package — the package is what generates it, and it has to work as a drop-in for any
project.

---

## 11. Key Decisions & Constraints

### Architectural Decisions

- **Four-Database Architecture**: separation of concerns (core directives, project
  state, user prefs, user directives)
- **Effect Isolation**: pure functions for logic, `_effect_*` functions for I/O
- **Immutable Data**: all dataclasses frozen, no mutations
- **Result Type Monads**: `Result[T, E]` instead of exceptions for fallible ops
- **Modularity First**: domain logic in modules; feature files compose, never own
  business logic
- **Path Separation**: dev staging (`dev/`, `docs/`) vs runtime `.aimfp-project/`

### Constraints

- **Python 3.11+** (type hints, pattern matching); developed and tested through 3.14
- **No OOP** — zero classes except frozen dataclasses
- **FP compliance mandatory** — purity, immutability, isolated side effects
- **Single runtime dependency** — `watchdog` only; server is pure stdlib
- **Linux-only** — no platform-conditional code exists anywhere in the codebase.
  Windows/macOS support is unbuilt, not merely untested; porting would be real work.
- **Version sync** — `pyproject.toml`, `src/aimfp/__init__.py`,
  `src/aimfp/mcp_server/server.py`, and `manifest.json` must all match
  (currently `1.47.0`)

---

## 12. Evolution History

### Self-tracking port — 2026-08-09 (current)

- **Change**: AIMFP became a tracked AIMFP project. `.aimfp-project/` created,
  blueprint moved here as the committed authoritative copy, 9 themes / 8 flows /
  12 modules registered, single ongoing completion path with perpetual Features and
  Issues & Bugs milestones.
- **Rationale**: every wart fixed on 2026-08-09 (`add_items` returning a count in the
  `id` field, `update_items` accepting IDs from any parent, `_batch_update_items`
  reporting `len(ids)` instead of the real row count) surfaced only because a
  *different* project used AIMFP. Self-tracking makes AIMFP its own
  highest-frequency test. Structure discovery was also the dominant per-session cost
  on this repo.

### InterComm interop, changesets & embedding hooks — 2026-06/07

- **Session bundle slimming** (2026-06-05): `aimfp_run(is_new_session=true)` payload
  reduced on larger projects.
- **InterCommAIMFP interop tools** (2026-06-23 → 06-26): worktree-aware
  `source_directory` resolution; semantic changeset export/apply for merging
  parallel workers' `project.db` (schema v1.11, slug + `entity_key`, structured
  UNIQUE-conflict reporting).
- **glama.json** (2026-07-11): MCP directory listing metadata.
- **svamanas embedding hooks** (2026-07-17): hook surface for svamanas integration.
- **Rationale**: InterComm is a *development* tool, not a runtime component of AIMFP.

### Plugin & automation tooling — 2026-05

- Claude Code plugin packaging; `get_system_prompt` and `get_claude_permissions`
  tools for one-call setup; permissions/allowlist automation.
- **Watchdog `.watchdogignore`**: gitignore-style per-project ignore file, because
  built-in exclusions only match directory basenames/extensions and can't target
  nested paths or glob test patterns.

### Rename AIFP → AIMFP & modularity system — 2026-03-29

- Full rebrand plus the modularity layer: `modules` + `module_files` tables, module
  helpers/tools, and `return_statements` steering domain logic into reusable modules.
- **Rationale**: modularity is the differentiator; the name reflects the focus.

### Version 1.3 — 2026-01-07

- Settings system finalized (v3.1); 18 → 12 settings baseline; added
  `project_continue_on_start` and `compliance_checking`; `project_compliance_check`
  repurposed as tracking-only analytics.

### Version 1.2 / 1.1 — 2025-11

- Helper classification (`is_sub_helper`, `is_tool`) and the many-to-many
  `directive_helpers` junction table with execution metadata.

### Version 1 — 2025-10-26

- Initial project setup and foundation for the FP-compliant MCP server.

---

## 13. Notes & References

**FP Compliance Checklist** — every function must satisfy:

- No mutations (data frozen)
- No side effects in logic (effects isolated in `_effect_*`)
- Explicit parameters (no hidden state)
- Deterministic (same inputs → same outputs)
- Type hints on all parameters and returns
- Returns Result types for fallible operations

### External References

- `README.md` — project overview & installation
- `sys-prompt/aimfp_system_prompt.txt` — installable behavioral system prompt
- `src/aimfp/reference/directives/` — 131 directive MD docs
- `src/aimfp/reference/guides/supportive_context*.txt` — four context variants
- `dev/` — directive/helper JSON source of truth + `sync-directives.py`
- `docs/AIMFP_SELF_TRACKING_PORT.md` — full reasoning behind §10 (gitignored)

---

*Blueprint for AIMFP v1.47.0 — last updated 2026-08-09*
