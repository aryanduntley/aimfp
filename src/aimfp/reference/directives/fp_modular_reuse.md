# Directive: fp_modular_reuse

**Type**: FP Core
**Level**: 1 (Critical)
**Parent Directive**: None
**Priority**: CRITICAL - Required for all FP compliance

---

## Purpose

The `fp_modular_reuse` directive enforces **functional modularity** — the principle that functions are self-contained, reusable puzzle pieces organized by domain, not by the feature or page that first needs them. Code should be a puzzle: each function is a piece with a defined shape (signature), and pieces snap together at the call site to form the whole picture. The same piece can appear in multiple pictures without modification.

This directive addresses a critical gap in functional codebases: even when every function is pure, immutable, and OOP-free, the codebase can still become unmaintainable if domain logic is embedded in feature-specific locations. A payment calculation buried inside a checkout page is just as problematic as a method buried inside a class — it's entangled, unreachable from elsewhere, and impossible to reuse without extraction.

**The Puzzle Piece Principle**:
- Every function is a **puzzle piece** — self-contained, well-shaped, reusable
- Pieces are stored in **domain boxes** (domain modules), not scattered across the table (feature files)
- The **assembled picture** (page, route, handler, orchestrator) is built by snapping pieces together
- The same piece can be used in **multiple pictures** — no duplication needed
- If a piece doesn't fit anywhere else, it's probably too specialized — break it down

When applied, this directive analyzes code for:
1. **Domain logic embedded in feature files** (business logic in pages, routes, handlers)
2. **Functions that mix concerns** (validation + persistence + formatting in one function)
3. **Unreusable functions** (tightly coupled to a specific caller's context)
4. **Missing domain modules** (no dedicated location for a domain's logic)
5. **Thin orchestration violation** (feature files containing logic instead of composing calls)

**Important**: This directive is reference documentation for modular reuse patterns.
AI consults this when making decisions about where to place functions and how to structure modules.

**Modular reuse is baseline behavior**:
- AI organizes code by domain and composes at call sites (enforced by system prompt during code writing)
- This directive provides detailed guidance for complex structuring decisions
- **Pre-write search is MANDATORY**: Before writing any function, search existing domain modules for overlapping logic. If reusable code exists, import it — do not rewrite it
- If a function could serve more than one caller, it MUST go in a domain module. This is a hard gate, not a suggestion

**Database-Enforced Modularity (AIMFP modules system)**:
- Modules are tracked in `project.db` via the `modules` table
- A module IS a directory — all files under it belong to the module
- Use `search_modules()`, `get_all_modules()` to find existing modules before writing code
- Use `add_module()` to create new modules when domain logic needs its own boundary
- **External dependency rule**: Libraries/services MUST be wrapped in modules. Record wrapped libraries in `external_dependencies` field. Never import a library directly from orchestrator code (pages, handlers, commands)
- **Change impact test**: If changing implementation internals would break >1 file, it MUST be a module
- See `project_module_check` directive for the full evaluation workflow

---

## When to Apply

**When AI Consults This Directive**:
- Deciding where to place a new function or module
- Noticing domain logic embedded in a feature-specific file
- Structuring a new feature that spans multiple domains
- Refactoring entangled code into reusable pieces
- Uncertainty about module boundaries or function granularity

**Context**:
- AI writes modular, domain-organized code as baseline behavior (system prompt enforcement)
- This directive is consulted DURING code writing when structuring decisions arise
- Related directives (`fp_no_oop`, `fp_io_isolation`, `fp_api_design`) complement this with specific patterns

**NOT Applied**:
- NOT called automatically after every file write
- NOT used for post-write validation
- NO validation loop

---

## Core Principles

### Principle 1: Domain-Based Organization

Functions belong in **domain modules**, not in the file that first uses them.

**Anti-pattern**: Payment logic lives in `checkout_page.py` because checkout was the first feature that needed it.

**Pattern**: Payment logic lives in `payments/` (or `payments.py`). Checkout page imports and calls it. Refund page imports the same functions. Admin dashboard imports the same functions. One source of truth, many consumers.

**Rule**: If a function implements domain logic (business rules, calculations, validations, transformations), it belongs in a domain module — never in a page, route, handler, or controller.

### Principle 2: Single-Responsibility Functions

Each function does **one thing** and does it completely. It handles its own concern without leaking into adjacent concerns.

**Anti-pattern**: `process_order(order)` validates the order, calculates tax, charges payment, sends email, and updates inventory.

**Pattern**: Separate functions for each concern:
- `validate_order(order) -> Result[Order, ValidationError]`
- `calculate_tax(order, tax_rules) -> TaxResult`
- `charge_payment(amount, payment_method) -> Result[PaymentReceipt, PaymentError]`
- `send_confirmation(order, receipt) -> EmailPayload`
- `update_inventory(items, quantities) -> InventoryUpdate`

The orchestrator composes them. Each function is independently testable and reusable.

### Principle 3: Compose at the Call Site

Feature files (pages, routes, handlers, orchestrators) should be **thin** — they compose domain functions, they don't contain domain logic. They are the assembled puzzle, not the puzzle pieces.

**Anti-pattern**: A route handler contains 200 lines of business logic.

**Pattern**: A route handler is 10-20 lines that call domain functions in sequence:
```python
def handle_checkout(request: CheckoutRequest) -> CheckoutResponse:
    """Thin orchestrator — composes domain functions."""
    # Validate (from validation domain)
    validated = validate_checkout(request)
    if not validated.ok:
        return error_response(validated.error)

    # Calculate (from pricing domain)
    total = calculate_total(validated.value.items, get_tax_rules())

    # Charge (from payments domain)
    receipt = charge_payment(total, request.payment_method)
    if not receipt.ok:
        return error_response(receipt.error)

    # Confirm (from notifications domain)
    send_order_confirmation(request.email, receipt.value)

    return success_response(receipt.value)
```

### Principle 4: Reusability by Default (HARD GATE)

When writing a function, answer: **"Could another part of the codebase use this?"** If yes (even hypothetically), it **MUST** go in a domain module — this is not optional, not a suggestion, not a "nice to have." Place it in the domain module, import it where needed:
- Accept general inputs, not caller-specific structures
- Return general outputs, not caller-specific formats
- Don't assume context about who's calling

If similar logic already exists elsewhere, do NOT duplicate it. Either reuse the existing function directly, parameterize it to cover both cases, or extract a shared core that both call sites compose.

**Anti-pattern**: `format_price_for_checkout_page(price)` — hard-coded to one consumer.

**Pattern**: `format_currency(amount, currency, locale) -> str` — usable anywhere.

### Principle 5: No Logic Entanglement

Two distinct concerns should never be woven together in the same function. If you can't describe what a function does without using the word "and", it's doing too much.

**Test**: Can you describe the function's purpose in one sentence without "and"? If not, split it.

---

## Workflow

### Trunk: analyze_module_structure

Analyzes code organization for modular reuse compliance.

**Steps**:
1. **Identify domain logic** - Find business rules, calculations, validations, transformations
2. **Check placement** - Is domain logic in a domain module or a feature file?
3. **Check granularity** - Does each function have a single responsibility?
4. **Check reusability** - Are functions designed for general use or tied to one caller?
5. **Check orchestration** - Are feature files thin composers or thick logic containers?

### Branches

**Branch 1: If domain_logic_in_feature_file**
- **Then**: `extract_to_domain_module`
- **Details**: Move domain logic to appropriate domain module
  - Identify the domain (payments, users, inventory, etc.)
  - Create or find the domain module
  - Extract functions with clean signatures
  - Replace inline logic with import + call
- **Result**: Domain logic in domain module, feature file imports and calls

**Branch 2: If function_mixes_concerns**
- **Then**: `decompose_function`
- **Details**: Split into single-responsibility functions
  - Identify distinct concerns within the function
  - Extract each concern as a separate function
  - Create orchestrator if needed to compose them
  - Each extracted function should be independently testable
- **Result**: Multiple focused functions, each reusable

**Branch 3: If unreusable_function**
- **Then**: `generalize_interface`
- **Details**: Make function signature more general
  - Replace caller-specific parameter types with general types
  - Remove assumptions about calling context
  - Return general data, not caller-specific formats
- **Result**: Function usable from multiple call sites

**Branch 4: If missing_domain_module**
- **Then**: `create_domain_module`
- **Details**: Create module for the identified domain
  - Name module after the domain (payments.py, users.py, inventory/)
  - Move all related functions to the module
  - Export clean public API
- **Result**: New domain module with organized functions

**Fallback**: `mark_as_compliant`
- **Details**: Code is well-organized with reusable domain modules
  - Feature files are thin orchestrators
  - Domain logic is in domain modules
  - Functions are single-responsibility and reusable
- **Result**: Code passes modular reuse check

---

## Examples

### Example 1: Payment Logic Extraction

**Anti-pattern (domain logic in feature file):**
```python
# checkout_page.py
# Payment logic is EMBEDDED in the checkout page

def checkout(cart: Cart, card_number: str, expiry: str) -> dict:
    """Checkout handler with embedded payment logic."""
    # Validation (embedded)
    if not card_number or len(card_number) != 16:
        return {"error": "Invalid card number"}
    if not expiry or "/" not in expiry:
        return {"error": "Invalid expiry"}

    # Tax calculation (embedded)
    subtotal = sum(item.price * item.qty for item in cart.items)
    tax = subtotal * 0.08
    total = subtotal + tax

    # Payment processing (embedded)
    # Now if refund_page needs payment logic, it must duplicate or import from checkout_page!
    payment_result = process_card(card_number, expiry, total)
    if not payment_result.success:
        return {"error": payment_result.message}

    return {"receipt_id": payment_result.receipt_id, "total": total}
```

**Compliant (puzzle pieces in domain modules):**
```python
# payments/validation.py — reusable payment validation
def validate_card(card_number: str, expiry: str) -> Result[CardInfo, str]:
    """Validate payment card details."""
    if not card_number or len(card_number) != 16:
        return Err("Invalid card number")
    if not expiry or "/" not in expiry:
        return Err("Invalid expiry")
    return Ok(CardInfo(number=card_number, expiry=expiry))


# pricing/calculation.py — reusable pricing logic
def calculate_order_total(
    items: tuple[LineItem, ...], tax_rate: float
) -> OrderTotal:
    """Calculate order total with tax."""
    subtotal = sum(item.price * item.qty for item in items)
    tax = subtotal * tax_rate
    return OrderTotal(subtotal=subtotal, tax=tax, total=subtotal + tax)


# payments/processing.py — reusable payment processing
def charge_card(
    card: CardInfo, amount: float
) -> Result[PaymentReceipt, str]:
    """Process a card payment."""
    payment_result = process_card(card.number, card.expiry, amount)
    if not payment_result.success:
        return Err(payment_result.message)
    return Ok(PaymentReceipt(receipt_id=payment_result.receipt_id))


# checkout_page.py — THIN orchestrator, no domain logic
def checkout(cart: Cart, card_number: str, expiry: str) -> dict:
    """Thin orchestrator — composes domain functions."""
    card = validate_card(card_number, expiry)
    if not card.ok:
        return {"error": card.error}

    totals = calculate_order_total(cart.items, TAX_RATE)

    receipt = charge_card(card.value, totals.total)
    if not receipt.ok:
        return {"error": receipt.error}

    return {"receipt_id": receipt.value.receipt_id, "total": totals.total}


# refund_page.py — REUSES the same puzzle pieces!
def refund(receipt_id: str, amount: float, card_number: str, expiry: str) -> dict:
    """Thin orchestrator — reuses payment domain functions."""
    card = validate_card(card_number, expiry)
    if not card.ok:
        return {"error": card.error}

    result = refund_to_card(card.value, amount, receipt_id)
    return {"status": result}
```

**Why Compliant**:
- Payment validation is reusable (checkout, refund, admin all use it)
- Pricing calculation is reusable (checkout, invoice, reporting all use it)
- Payment processing is reusable (checkout, subscription, retry all use it)
- Feature files are thin — they compose, they don't contain logic

---

### Example 2: User Formatting Reuse

**Anti-pattern (formatting logic in one feature):**
```python
# admin_dashboard.py
def render_user_row(user_data: dict) -> str:
    """Admin-specific user formatting — not reusable."""
    full_name = f"{user_data['first']} {user_data['last']}"
    initials = f"{user_data['first'][0]}{user_data['last'][0]}"
    display = f"{full_name} ({initials})"
    return f"<tr><td>{display}</td><td>{user_data['email']}</td></tr>"
```

**Compliant (separate domain logic from presentation):**
```python
# users/formatting.py — reusable user formatting
def full_name(first: str, last: str) -> str:
    """Format full name from parts."""
    return f"{first} {last}"

def initials(first: str, last: str) -> str:
    """Extract initials from name parts."""
    return f"{first[0]}{last[0]}"

def display_name(first: str, last: str) -> str:
    """Format display name with initials."""
    return f"{full_name(first, last)} ({initials(first, last)})"


# admin_dashboard.py — uses domain functions
def render_user_row(user_data: dict) -> str:
    """Thin: composes domain formatting with HTML."""
    name = display_name(user_data['first'], user_data['last'])
    return f"<tr><td>{name}</td><td>{user_data['email']}</td></tr>"


# user_profile.py — REUSES the same formatting
def render_profile_header(user_data: dict) -> str:
    """Thin: composes domain formatting with profile template."""
    name = display_name(user_data['first'], user_data['last'])
    return f"<h1>{name}</h1>"
```

---

### Example 3: Multi-Domain Feature

When a feature spans multiple domains, the feature file is a **thin orchestrator** that composes calls across domain modules:

```python
# order_completion.py — orchestrator spanning 4 domains
def complete_order(order_id: str, user_id: str) -> Result[OrderConfirmation, str]:
    """Orchestrate order completion across domains.

    This function contains NO domain logic — it composes puzzle pieces.
    """
    # Inventory domain
    stock_check = check_stock_availability(order_id)
    if not stock_check.ok:
        return Err(f"Stock unavailable: {stock_check.error}")

    # Pricing domain
    totals = calculate_final_total(order_id, apply_discounts=True)

    # Payment domain
    charge_result = charge_stored_payment(user_id, totals.total)
    if not charge_result.ok:
        return Err(f"Payment failed: {charge_result.error}")

    # Inventory domain (update after successful payment)
    reserve_inventory(order_id)

    # Notification domain
    confirmation = build_confirmation(order_id, charge_result.value, totals)
    queue_confirmation_email(user_id, confirmation)

    return Ok(confirmation)
```

---

## Edge Cases

### Edge Case 1: Truly Feature-Specific Logic

**Issue**: Some logic genuinely belongs to one feature and will never be reused.

**Handling**:
- If a function is genuinely specific to one feature (e.g., a page-specific layout transform), it can live in the feature file
- But the **domain logic it uses** must still come from domain modules
- Ask: "Is this specific to HOW this feature presents data, or WHAT the data means?" Presentation-specific is OK in the feature file. Domain meaning belongs in a domain module.

### Edge Case 2: Small Projects

**Issue**: Creating domain modules for a 3-file project feels like over-engineering.

**Handling**:
- For very small projects, a single `domain.py` or `core.py` is acceptable
- The principle still applies: domain logic in domain file, feature files compose
- As the project grows, split `domain.py` into domain-specific modules
- The threshold: when a domain file exceeds ~300 lines or covers 3+ distinct domains, split it

### Edge Case 3: Shared Utilities vs Domain Logic

**Issue**: Where do generic utilities (string formatting, date math) go vs domain logic?

**Handling**:
- **Generic utilities** (no domain knowledge): `utils/` or `_common.py`
- **Domain logic** (knows about business rules): domain module (`payments/`, `users/`)
- Test: "Does this function know about my business domain?" If yes, domain module. If it's pure data transformation with no domain knowledge, utilities.

### Edge Case 4: Circular Domain Dependencies

**Issue**: Domain A needs Domain B, and Domain B needs Domain A.

**Handling**:
- Extract the shared concept into a third module
- Or pass the dependency as a function parameter (dependency injection via higher-order functions)
- Circular imports signal entangled domains — the shared logic needs its own home

---

## Related Directives

- **Depends On**: None (foundational directive)
- **Works With**:
  - `fp_no_oop` - Modular reuse is the FP answer to OOP's "organize by class" — we organize by domain module instead
  - `fp_purity` - Pure functions are inherently more reusable (no hidden dependencies)
  - `fp_io_isolation` - I/O at boundaries, domain logic pure and reusable in the middle
  - `fp_api_design` - Public API design follows the same composability principles
  - `fp_chaining` - Composed domain functions chain naturally
- **Complements**:
  - DRY principle (supportive context section 9) - DRY says extract identical code; modular reuse says organize it by domain
  - `fp_dependency_tracking` - Tracks which functions depend on which, revealing reuse patterns

---

## Helper Functions

Query `get_helpers_for_directive('fp_modular_reuse')` to discover this directive's
helpers, and `get_helper_by_name` for a current signature.

The module toolset is this directive's subject matter, so the names are worth
knowing by heart — but look the signatures up rather than trusting a copy:

- `add_module` — create a module entry
- `search_modules` — find existing modules by name, purpose, or description
- `get_all_modules` — list every module in the project
- `get_module_for_file` — reverse lookup: which module owns this file?
- `get_module_files` / `get_module_functions` — what a module contains
- `get_module_dependencies` — cross-module dependency graph
- `add_file_to_module` / `add_files_to_module` — assign after `finalize_file`

Signatures are deliberately omitted. This file previously carried them, and they
had already gone stale: it documented `search_modules(search_string)` after the
function had gained `limit`. A signature in prose is a copy that nothing keeps in
sync, and a stale one is worse than none because it is believed.

---

## Database Operations

**Project Database** (project.db):
- **`modules`**: Module definitions — name, path, purpose, external_dependencies. File membership derived from path prefix
- **`files`**: File metadata shows domain module vs feature file organization
- **`functions`**: Function metadata reveals reuse patterns across files
- **`interactions`**: Cross-function dependencies show composition patterns (also used for cross-module dependency analysis)
- **`file_flows`**: Flow assignments reflect domain organization

**Tracking** (Optional - Disabled by Default):

If tracking is enabled:
- **`tracking_notes`** (user_preferences.db): Logs modular reuse analysis with `note_type='fp_analysis'`

Only occurs when `fp_flow_tracking` is enabled via `tracking_toggle`.

---

## Testing

How to verify this directive is working:

1. **Domain logic in feature file** -> Directive extracts to domain module
   ```python
   # Before: tax calculation in checkout_page.py
   def checkout(cart):
       tax = sum(i.price for i in cart.items) * 0.08  # domain logic embedded

   # After: tax calculation in pricing/calculation.py, checkout imports it
   from pricing.calculation import calculate_tax
   def checkout(cart):
       tax = calculate_tax(cart.items, TAX_RATE)  # composed
   ```

2. **Multi-concern function** -> Directive decomposes into single-responsibility pieces
   ```python
   # Before: validate_and_save_and_notify(user)
   # After: validate_user(user), save_user(user), notify_user(user) + orchestrator
   ```

3. **Feature file is thin** -> Directive confirms compliance
   ```python
   # Feature file only imports and composes domain functions
   # No business logic, calculations, or validations inline
   ```

---

## Common Mistakes

- **Organizing by feature first** - "All checkout code in checkout/" puts domain logic in the wrong place. Organize by domain, compose in features.
- **"I'll extract it later"** - Logic placed in a feature file "for now" rarely gets extracted. Place it in the domain module from the start.
- **Over-specialized signatures** - `format_price_for_checkout(price)` should be `format_currency(amount, currency, locale)`.
- **Fat orchestrators** - If your orchestrator has `if/else` business logic, you haven't extracted enough. Orchestrators compose; they don't decide.
- **Confusing DRY with modularity** - DRY says "don't repeat code." Modularity says "put code where it belongs." Even unique code should be in its domain module, not embedded in a feature file.

---

## Roadblocks and Resolutions

### Roadblock 1: domain_logic_in_feature_file
**Issue**: Business logic embedded in a page, route, or handler
**Resolution**: Extract to domain module, replace with import + function call

### Roadblock 2: mixed_concerns
**Issue**: Function handles multiple unrelated responsibilities
**Resolution**: Decompose into single-responsibility functions, compose via orchestrator

### Roadblock 3: unreusable_interface
**Issue**: Function signature tied to one specific caller
**Resolution**: Generalize parameters and return types for broader applicability

### Roadblock 4: missing_domain_module
**Issue**: No dedicated module for a domain's logic
**Resolution**: Create domain module, migrate related functions from feature files

---

## References

None

---

*Part of AIMFP v1.0 - Critical FP Core directive for functional modularity and reuse*
