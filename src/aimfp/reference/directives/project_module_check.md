# Directive: project_module_check

**Type**: Project
**Level**: 3 (Operational Execution)
**Parent Directive**: project_file_write
**Priority**: HIGH - Module boundary enforcement and reusable code organization

---

## Purpose

The `project_module_check` directive evaluates whether code being written should be organized as a module (reusable code boundary). Modules are folder-based: a module IS a directory containing related files. All files under that directory belong to the module.

Key responsibilities:
- **Detect module candidates** using concrete signals (external dependencies, cross-file reuse, domain vocabulary, change impact)
- **Search existing modules** before creating new ones
- **Create module entries** in project.db when new modules are needed
- **Enforce external dependency wrapping** — libraries/services must be abstracted behind modules
- **Prevent domain logic leaking** into orchestrator files (pages, handlers, commands)

This is called by `project_file_write` as a gate BEFORE writing code.

---

## When to Apply

This directive applies when:
- **Writing any new file** — evaluate if the code belongs in a module
- **Adding external library imports** — must be wrapped in a module
- **Writing domain logic** — logic with its own vocabulary/concepts should be modular
- **Called by**: `project_file_write` (module gate step)

---

## Workflow

### Trunk: evaluate_module_candidacy

Determine if the code being written is a module candidate.

**Steps**:
1. **Run signal detection** — apply the four module candidate signals (see below)
2. **If any signal is YES** → proceed to branch 1 (module candidate)
3. **If all signals are NO** → proceed to branch 3 (no module needed)

### Branches

**Branch 1: If module_candidate**
- **Then**: `search_and_assign_module`
- **Details**:
  1. Call `search_modules(search_string)` with domain keywords
  2. Call `get_all_modules()` for full overview
  3. If matching module exists → file goes under that module's path
  4. If no match → proceed to branch 2 (create new module)

**Branch 2: If new_module_needed**
- **Then**: `create_module`
- **Details**:
  1. Determine module name (domain concept, not implementation detail)
  2. Determine module path (directory under source root)
  3. Call `add_module(name, path, description, purpose, external_dependencies)`
  4. Create directory if it doesn't exist
  5. File being written goes under the new module's path

**Branch 3: If not_module_candidate**
- **Then**: `proceed_with_write`
- **Details**: File is an orchestrator (page, handler, command, config) or truly one-off code. Proceed with file_write. Orchestrators should ONLY import from modules — never contain domain logic.

### Fallback: unclear_candidacy
- When candidacy is ambiguous, apply the **change impact test**: "If the implementation internals changed, how many files would need updating?" If >1 → it's a module.

---

## Module Candidate Signals

Any YES answer means the code should be a module:

### Signal 1: External Dependency
**Question**: Is this code importing an external library or service?
**Examples**: walletconnect, stripe, phue, boto3, requests, any npm package
**Rule**: NEVER import external libraries directly from orchestrator code. Always wrap in a module. The module abstracts the dependency — when you swap libraries, only the module internals change.

### Signal 2: Cross-File Reuse
**Question**: Does this logic exist or could it exist in multiple files?
**Rule**: If two or more files need the same functionality, it must be a module. "Similar but not identical" is NOT a reason to duplicate — parameterize the shared logic.

### Signal 3: Domain Vocabulary
**Question**: Does this code have its own vocabulary and concepts?
**Examples**: "wallets", "transactions", "cart items", "shipping rates", "pipeline stages"
**Rule**: Distinct domain concepts get their own modules. If you can describe the code's concern in a domain-specific phrase, it's a module.

### Signal 4: Change Impact
**Question**: If the implementation internals changed, how many files would break?
**Rule**: If >1 file would need updating, it MUST be a module. This is the sharpest test and overrides all ambiguity.

---

## Module Organization Rules

### Folder Structure
Modules are directories. Files within a module are granular by use case:

```
validation/                  <- MODULE: validation
  payments.py                <- payment-specific validation
  users.py                   <- user-specific validation
  addresses.py               <- address validation
  __init__.py                <- clean public API

wallet_connection/           <- MODULE: wallet_connection
  connect.py                 <- connection logic
  transactions.py            <- send/receive
  types.py                   <- WalletState, TransactionResult
  __init__.py                <- public API (abstracts walletconnect/wagmi internals)
```

### Naming
- Module names reflect the **domain concept**, not the implementation
- `wallet_connection` not `walletconnect_wrapper`
- `payment_processing` not `stripe_integration`
- This enables swapping implementations without renaming the module

### External Dependency Wrapping
When creating a module that wraps an external library:
1. Record the library in `external_dependencies` field
2. The module's public API should be implementation-agnostic
3. All callers import from the module, never from the library directly

---

## Examples Across Software Types

### Web Application
```
src/
  validation/         <- MODULE (used by checkout, registration, profile)
  cart_management/    <- MODULE (used by checkout, wishlist, admin)
  payment_processing/ <- MODULE wraps stripe (used by checkout, subscriptions, refunds)
  auth/               <- MODULE wraps auth0 (used by all protected pages)
  pages/              <- ORCHESTRATORS (thin, import from modules)
    checkout.py       <- imports from validation, cart_management, payment_processing
    profile.py        <- imports from validation, auth
```

### CLI Tool
```
src/
  file_operations/    <- MODULE (read, write, transform files)
  output_formatting/  <- MODULE (tables, colors, progress bars)
  config_parsing/     <- MODULE wraps toml/yaml (load, validate, merge configs)
  commands/           <- ORCHESTRATORS (thin command handlers)
    build.py          <- imports from file_operations, config_parsing
    report.py         <- imports from file_operations, output_formatting
```

### Data Pipeline
```
src/
  data_loading/       <- MODULE wraps pandas/polars (CSV, JSON, DB sources)
  transformers/       <- MODULE (clean, normalize, aggregate)
  validators/         <- MODULE (schema validation, range checks, null detection)
  exporters/          <- MODULE wraps S3/GCS (output to various destinations)
  pipelines/          <- ORCHESTRATORS (compose loading -> transform -> validate -> export)
    daily_report.py   <- imports from all modules above
    etl_users.py      <- imports from data_loading, transformers, exporters
```

### API Server
```
src/
  business_rules/     <- MODULE (pricing, eligibility, limits)
  data_access/        <- MODULE wraps SQLAlchemy/prisma (queries, transactions)
  serialization/      <- MODULE (request/response formatting)
  auth/               <- MODULE wraps JWT/OAuth (token verification, permissions)
  endpoints/          <- ORCHESTRATORS (thin route handlers)
    users.py          <- imports from auth, data_access, serialization
    orders.py         <- imports from business_rules, data_access, auth
```

---

## When NOT to Modularize

- **Configuration files** — pyproject.toml, setup.py, .env
- **Orchestrator-specific layout** — page HTML structure, CLI argument parsing (the glue, not the logic)
- **One-time scripts** — migration scripts, data fixups
- **Tests** — test files reference modules but are not modules themselves

The test: "Would extracting this create a module with only one caller and no domain vocabulary?" If yes, keep it inline.

---

## Database Integration

### Helpers Used

Look signatures up with `get_helper_by_name`; the names alone are listed here
because they are this directive's subject matter.

- `search_modules` — search existing modules by name/purpose/description
- `get_all_modules` — list all modules for overview
- `add_module` — create a new module
- `get_module_files` — inspect what is already in a module
- `get_module_for_file` — reverse lookup: which module owns this file?

Signatures are deliberately omitted: this list previously carried
`search_modules(search_string)` after the function had gained `limit`. A
signature copied into prose is a copy nothing keeps in sync, and a stale one is
worse than none because it is believed.

### Tracking Flow
1. Module check triggers during `project_file_write`
2. If new module → `add_module()` creates the DB entry
3. File is written under module path → automatically associated via path prefix
4. File's functions/types are tracked as normal via reserve/finalize
5. Cross-module interactions tracked via `add_interaction()` as normal
6. `get_module_dependencies()` can later show the module dependency graph
