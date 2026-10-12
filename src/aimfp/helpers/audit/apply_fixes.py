"""
AIMFP Helper Functions - Apply Audit Fixes

apply_audit_fixes turns audit findings into database changes in ONE call and
ONE transaction, and records what it removed or changed so every change can be
undone.

Two ways to say what to change, freely combined:
- report + apply: a report written by an audit tool and the finding
  categories to apply from it — nothing large passes through the conversation;
  exclude holds back named findings of those categories (reviewed false
  positives) without listing the rest
- explicit lists: interaction adds/repoints/deletes, indirect marks, function
  and type deletes, signature refreshes, JSON normalization, hash baselines,
  type links

Category -> change:
    missing_interactions     add the edge
    stale_interactions       delete the edge
    repoint_interactions     move the edge's source or target
    stale_functions          delete the function (its edges and type links go with it;
                             one marked moved_to hands them to its successor first)
    stale_types              delete the type (its type links go with it)
    param_mismatch           refresh parameters from the source, keeping prose
    malformed_json           re-encode the column exactly once
    missing_type_links       add the types_functions link
    unbaselined_descriptions record the current code hash (after reviewing prose)
    unbaselined_needs_review record the current code hash: the triaged prose was
                             reviewed and is right (exclude the ones being rewritten)
    stale_descriptions       record the current code hash: the prose still
                             describes the changed code (exclude the ones that don't)

Explicit only: exempt / unexempt_ids write and remove audit_exemptions (schema
1.16), reviewed findings audits list under 'exempted' instead of reporting.

Safety:
- Every report finding is re-checked against the row before it is applied:
  an edge whose endpoints changed since the audit, or an entity already gone,
  is skipped with a reason rather than changed blindly.
- One entry_deletion note (the restore manifest) lists every row deleted —
  cascaded edges and type links included — and the old values of every row
  changed. It is written in the same transaction, so a change without its
  manifest cannot happen.
- dry_run=True does everything and rolls back; the result is what a real run
  does.
"""

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..utils import (
    decode_json_layers, encode_json_field, get_cached_project_root, get_return_statements,
    _open_project_connection,
)
from ..shared.return_gates import gate_return_statements
from ..project.functions_2 import plan_edge_repoints
from ..catalog.extract import ExtractedFunction, extract_entities
from ..catalog.scan import _effect_read_source
from ...watchdog.config import detect_language
from .exemptions import EXEMPTABLE
from .reports import _effect_delete_report, _effect_load_report


VALID_INTERACTION_TYPES = frozenset({'call', 'chain', 'borrow', 'compose', 'pipe'})
VALID_ROLES = frozenset({'factory', 'transformer', 'operator', 'pattern_matcher',
                         'accessor', 'validator', 'combinator'})
SKIPPED_SAMPLE = 20

# Report category -> plan field it feeds.
CATEGORY_OPERATIONS: Dict[str, str] = {
    'missing_interactions': 'add_interactions',
    'stale_interactions': 'delete_interactions',
    'repoint_interactions': 'repoint_interactions',
    'stale_functions': 'delete_functions',
    'stale_types': 'delete_types',
    'param_mismatch': 'refresh_signatures',
    'malformed_json': 'normalize_json',
    'missing_type_links': 'add_type_links',
    'unbaselined_descriptions': 'baseline_hashes',
    'unbaselined_needs_review': 'baseline_hashes',
    'stale_descriptions': 'baseline_hashes',
}

# Report category -> the finding fields that name one finding in `exclude`
# (one field: an id; two fields: an [a, b] pair).
FINDING_KEYS: Dict[str, Tuple[str, ...]] = {
    'missing_interactions': ('source_function_id', 'target_function_id'),
    'stale_interactions': ('interaction_id',),
    'repoint_interactions': ('interaction_id',),
    'stale_functions': ('function_id',),
    'stale_types': ('type_id',),
    'param_mismatch': ('function_id',),
    'malformed_json': ('id',),
    'missing_type_links': ('type_id', 'function_id'),
    'unbaselined_descriptions': ('function_id',),
    'unbaselined_needs_review': ('function_id',),
    'stale_descriptions': ('function_id',),
}


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class FixPlan:
    """Every change requested, as plain dicts (one per row to touch)."""
    add_interactions: Tuple[Dict[str, Any], ...] = ()
    repoint_interactions: Tuple[Dict[str, Any], ...] = ()
    delete_interactions: Tuple[Dict[str, Any], ...] = ()
    set_indirect: Tuple[Dict[str, Any], ...] = ()
    delete_functions: Tuple[Dict[str, Any], ...] = ()
    delete_types: Tuple[Dict[str, Any], ...] = ()
    refresh_signatures: Tuple[Dict[str, Any], ...] = ()
    normalize_json: Tuple[Dict[str, Any], ...] = ()
    baseline_hashes: Tuple[Dict[str, Any], ...] = ()
    add_type_links: Tuple[Dict[str, Any], ...] = ()
    add_exemptions: Tuple[Dict[str, Any], ...] = ()
    delete_exemptions: Tuple[Dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ApplyResult:
    """What apply_audit_fixes did (or, with dry_run, would do)."""
    success: bool
    dry_run: bool = False
    applied: Dict[str, int] = field(default_factory=dict)
    skipped_count: int = 0
    skipped: Tuple[Dict[str, Any], ...] = ()
    excluded_count: int = 0
    manifest_note_id: Optional[int] = None
    report_deleted: bool = False
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helpers
# ============================================================================

PLAN_FIELDS: Tuple[str, ...] = tuple(FixPlan.__dataclass_fields__)


def merge_plans(*plans: FixPlan) -> FixPlan:
    """Pure: One plan holding every change of the given plans, in order."""
    return FixPlan(**{
        name: tuple(item for plan in plans for item in getattr(plan, name))
        for name in PLAN_FIELDS
    })


def plan_from_report(report: Dict[str, Any], categories: Tuple[str, ...]) -> Tuple[FixPlan, Tuple[str, ...]]:
    """
    Pure: The changes a report's findings ask for, for the chosen categories.

    Returns:
        (plan, refused) — refused names categories that are unknown or not
        appliable (they need judgement: rewrite prose, mark indirect, catalog)
    """
    findings = report.get('findings') or {}
    refused = tuple(c for c in categories if c not in CATEGORY_OPERATIONS)
    fields: Dict[str, List[Dict[str, Any]]] = {}
    for category in categories:
        operation = CATEGORY_OPERATIONS.get(category)
        if operation:
            fields.setdefault(operation, []).extend(
                {**item, '_from': category} for item in findings.get(category) or ()
            )
    return (FixPlan(**{name: tuple(items) for name, items in fields.items()}), refused)


def exemption_problem(item: Dict[str, Any]) -> Optional[str]:
    """Pure: Why an exempt entry is unusable (category, entity, reason), or None."""
    kinds = EXEMPTABLE.get(item.get('category'))
    if kinds is None:
        return f"category '{item.get('category')}' cannot be exempted; exemptable: {', '.join(sorted(EXEMPTABLE))}"
    named = tuple(k for k in ('function', 'type') if item.get(f'{k}_id') is not None)
    if len(named) != 1:
        return 'name exactly one of function_id / type_id'
    if named[0] not in kinds:
        return f"{item['category']} findings are about {' or '.join(sorted(kinds))}s, not {named[0]}s"
    if not item.get('reason'):
        return 'reason is required: it is shown with every exempted finding'
    return None


def exclusion_problem(exclude: Dict[str, Any], categories: Tuple[str, ...]) -> Optional[str]:
    """Pure: Why an exclude map is unusable (unknown category, not applied, bad key shape), or None."""
    for category, entries in exclude.items():
        if category not in categories:
            return f"exclude names '{category}', which is not in apply"
        width = len(FINDING_KEYS[category])
        for entry in entries if isinstance(entries, list) else [entries]:
            ok = (isinstance(entry, int) and width == 1) or (
                isinstance(entry, list) and len(entry) == width and all(isinstance(i, int) for i in entry))
            if not ok:
                shape = 'an id' if width == 1 else f"a [{', '.join(FINDING_KEYS[category])}] pair"
                return f"exclude['{category}'] entries must each be {shape}; got {entry!r}"
    return None


def exclude_findings(
    report: Dict[str, Any],
    exclude: Dict[str, List[Any]],
) -> Tuple[Dict[str, Any], int]:
    """
    Pure: The report without the findings `exclude` names, and how many were removed.

    Args:
        report: An audit report
        exclude: {category: [id, ...]} or, for missing_interactions and
            missing_type_links, {category: [[a, b], ...]} (FINDING_KEYS order)
    """
    findings = report.get('findings') or {}
    kept: Dict[str, Any] = {}
    removed = 0
    for category, items in findings.items():
        if category not in exclude:
            kept[category] = items
            continue
        keys = FINDING_KEYS[category]
        named = {tuple(e) if isinstance(e, list) else (e,) for e in exclude[category]}
        survivors = [item for item in items or () if tuple(item.get(k) for k in keys) not in named]
        removed += len(items or ()) - len(survivors)
        kept[category] = survivors
    return ({**report, 'findings': kept}, removed)


def plan_from_arguments(
    add_interactions: Optional[List[Dict[str, Any]]] = None,
    repoint_interactions: Optional[List[Dict[str, Any]]] = None,
    delete_interaction_ids: Optional[List[int]] = None,
    mark_indirect_ids: Optional[List[int]] = None,
    unmark_indirect_ids: Optional[List[int]] = None,
    delete_function_ids: Optional[List[int]] = None,
    delete_type_ids: Optional[List[int]] = None,
    refresh_signature_function_ids: Optional[List[int]] = None,
    baseline_hash_function_ids: Optional[List[int]] = None,
    add_type_links: Optional[List[Dict[str, Any]]] = None,
    exempt: Optional[List[Dict[str, Any]]] = None,
    unexempt_ids: Optional[List[int]] = None,
    reason: str = '',
) -> FixPlan:
    """Pure: The changes named explicitly by tool arguments (an exemption
    without its own reason takes the call's)."""
    return FixPlan(
        add_interactions=tuple(add_interactions or ()),
        repoint_interactions=tuple(repoint_interactions or ()),
        delete_interactions=tuple({'interaction_id': i} for i in delete_interaction_ids or ()),
        set_indirect=tuple({'interaction_id': i, 'is_indirect': 1} for i in mark_indirect_ids or ())
        + tuple({'interaction_id': i, 'is_indirect': 0} for i in unmark_indirect_ids or ()),
        delete_functions=tuple({'function_id': i} for i in delete_function_ids or ()),
        delete_types=tuple({'type_id': i} for i in delete_type_ids or ()),
        refresh_signatures=tuple({'function_id': i} for i in refresh_signature_function_ids or ()),
        baseline_hashes=tuple({'function_id': i} for i in baseline_hash_function_ids or ()),
        add_type_links=tuple(add_type_links or ()),
        add_exemptions=tuple({**e, 'reason': (e.get('reason') or reason).strip()} for e in exempt or ()),
        delete_exemptions=tuple({'exemption_id': i} for i in unexempt_ids or ()),
    )


def plan_size(plan: FixPlan) -> int:
    """Pure: Number of requested row changes."""
    return sum(len(getattr(plan, name)) for name in PLAN_FIELDS)


def parameters_from_source(
    extracted: ExtractedFunction,
    stored: Any,
) -> List[Dict[str, Any]]:
    """
    Pure: Parameter records from the code, keeping the stored description of
    every parameter whose name survives.

    Args:
        extracted: The function as the source defines it
        stored: The decoded stored parameters (list of dicts or strings, or junk)
    """
    def bare(name: str) -> str:
        return name.lstrip('*')

    descriptions = {
        bare(str(p.get('name', ''))): p.get('description')
        for p in (stored if isinstance(stored, list) else ())
        if isinstance(p, dict) and p.get('description')
    }
    return [
        {
            'name': p['name'],
            'type': p.get('type'),
            'required': p.get('default') is None and p.get('kind') not in ('var_positional', 'var_keyword'),
            **({'default': p['default']} if p.get('default') is not None else {}),
            **({'kind': p['kind']} if p.get('kind') not in (None, 'positional') else {}),
            **({'description': descriptions[bare(p['name'])]} if bare(p['name']) in descriptions else {}),
        }
        for p in extracted.parameters
    ]


def manifest_content(reason: str, manifest: Dict[str, List[Dict[str, Any]]], dry_run: bool) -> str:
    """Pure: The restore-manifest note text: a one-line summary, then JSON."""
    counts = ', '.join(f"{k}={len(v)}" for k, v in manifest.items() if v)
    return (
        f"AUDIT FIX MANIFEST (apply_audit_fixes{' dry run' if dry_run else ''}). Reason: {reason}. "
        f"Changed: {counts}. Old rows below restore every change (re-insert deleted rows "
        f"with their ids; write old_values back for updated rows).\n"
        + json.dumps(manifest, default=str, separators=(',', ':'))
    )


# ============================================================================
# Effects (all through the caller's connection; nothing commits here)
# ============================================================================

def _row(conn: sqlite3.Connection, sql: str, params: Tuple) -> Optional[Dict[str, Any]]:
    """Effect: One row as a dict, or None."""
    found = conn.execute(sql, params).fetchone()
    return dict(found) if found else None


def _rows(conn: sqlite3.Connection, sql: str, params: Tuple) -> List[Dict[str, Any]]:
    """Effect: Rows as dicts."""
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Effect: Whether a column exists (pre-1.15 databases lack the new ones)."""
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _source_function(project_root: str, path: str, name: str,
                     cache: Dict[str, Any]) -> Optional[ExtractedFunction]:
    """Effect: A top-level function as the file defines it (files read once per call)."""
    if path not in cache:
        source = _effect_read_source(os.path.join(project_root, path))
        cache[path] = None if source is None else extract_entities(source, detect_language(path))
    entities = cache[path]
    if entities is None or entities.fidelity != 'full':
        return None
    return next((f for f in entities.functions if f.name == name and not f.is_nested), None)


def _function_with_path(conn: sqlite3.Connection, function_id: int) -> Optional[Dict[str, Any]]:
    """Effect: A function row plus its file path."""
    return _row(conn, "SELECT f.*, fi.path AS path FROM functions f "
                      "LEFT JOIN files fi ON fi.id = f.file_id WHERE f.id = ?", (function_id,))


def _hand_over(
    conn: sqlite3.Connection,
    function_id: int,
    successor: int,
    manifest: Dict[str, List[Dict[str, Any]]],
) -> None:
    """
    Effect: Move a moved function's edges and type links to its successor
    (edges that would become self-edges or duplicates, and duplicate links,
    stay behind and cascade with the delete). Old values go into the manifest.
    """
    edges = tuple(_rows(conn, "SELECT * FROM interactions WHERE source_function_id = ? OR target_function_id = ? "
                              "ORDER BY id", (function_id, function_id)))
    existing = frozenset(
        (r['source_function_id'], r['target_function_id'], r['interaction_type'])
        for r in _rows(conn, "SELECT source_function_id, target_function_id, interaction_type FROM interactions "
                             "WHERE source_function_id = ? OR target_function_id = ?", (successor, successor)))
    moves, _ = plan_edge_repoints(edges, function_id, successor, existing)
    for move in moves:
        conn.execute("UPDATE interactions SET source_function_id = ?, target_function_id = ? WHERE id = ?",
                     (move['source_function_id'], move['target_function_id'], move['id']))
        manifest['changed_interactions'].append({'id': move['id'], 'old_values': move['old']})
    for link in _rows(conn, "SELECT * FROM types_functions WHERE function_id = ?", (function_id,)):
        if _row(conn, "SELECT id FROM types_functions WHERE type_id = ? AND function_id = ? AND role IS ?",
                (link['type_id'], successor, link['role'])) is None:
            conn.execute("UPDATE types_functions SET function_id = ? WHERE id = ?", (successor, link['id']))
            manifest['changed_type_links'].append({'id': link['id'], 'old_values': {'function_id': function_id}})


def _apply_plan(
    conn: sqlite3.Connection,
    project_root: str,
    plan: FixPlan,
) -> Tuple[Dict[str, int], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """
    Effect: Apply every change of a plan through one connection (no commit).

    Order matters: repoints and adds run before deletes so a repoint's target
    function still exists; function deletes run last so their cascades are
    captured from the final state.

    Returns:
        (applied counts per operation, skipped [{operation, ..., reason}],
         manifest {deleted_*/changed_*: rows})
    """
    applied: Dict[str, int] = {}
    skipped: List[Dict[str, Any]] = []
    manifest: Dict[str, List[Dict[str, Any]]] = {
        'deleted_interactions': [], 'changed_interactions': [], 'deleted_functions': [],
        'deleted_types': [], 'deleted_type_links': [], 'changed_functions': [], 'changed_types': [],
        'deleted_exemptions': [], 'changed_type_links': [],
    }
    has_indirect = _has_column(conn, 'interactions', 'is_indirect')
    has_exemptions = _has_column(conn, 'audit_exemptions', 'id')
    has_hash = _has_column(conn, 'functions', 'source_hash')
    sources: Dict[str, Any] = {}

    def done(operation: str) -> None:
        applied[operation] = applied.get(operation, 0) + 1

    def skip(operation: str, item: Dict[str, Any], reason: str) -> None:
        key = {k: v for k, v in item.items() if k.endswith('_id') or k in ('table', 'column')}
        skipped.append({'operation': operation, **key, 'reason': reason})

    def function_exists(function_id: Any) -> bool:
        return _row(conn, "SELECT id FROM functions WHERE id = ?", (function_id,)) is not None

    def edge_matches(edge: Dict[str, Any], item: Dict[str, Any], prefix: str = '') -> bool:
        expect_source = item.get(f'{prefix}source_function_id')
        expect_target = item.get(f'{prefix}target_function_id')
        expect_type = item.get('interaction_type') if item.get('_from') else None
        return ((expect_source is None or edge['source_function_id'] == expect_source)
                and (expect_target is None or edge['target_function_id'] == expect_target)
                and (expect_type is None or edge['interaction_type'] == expect_type))

    # -- interactions: repoint --------------------------------------------------
    for item in plan.repoint_interactions:
        edge = _row(conn, "SELECT * FROM interactions WHERE id = ?", (item.get('interaction_id'),))
        if edge is None:
            skip('repoint_interactions', item, 'interaction not found')
            continue
        if not edge_matches(edge, item, 'expect_'):
            skip('repoint_interactions', item, 'edge changed since the audit')
            continue
        new_source = item.get('source_function_id', edge['source_function_id'])
        new_target = item.get('target_function_id', edge['target_function_id'])
        if not (function_exists(new_source) and function_exists(new_target)):
            skip('repoint_interactions', item, 'new endpoint function not found')
            continue
        if new_source == new_target:
            skip('repoint_interactions', item, 'repoint would make a self-edge')
            continue
        duplicate = _row(conn, "SELECT id FROM interactions WHERE source_function_id = ? AND "
                               "target_function_id = ? AND interaction_type = ? AND id != ?",
                         (new_source, new_target, edge['interaction_type'], edge['id']))
        if duplicate:
            conn.execute("DELETE FROM interactions WHERE id = ?", (edge['id'],))
            manifest['deleted_interactions'].append({**edge, 'note': f"duplicate of {duplicate['id']} after repoint"})
        else:
            conn.execute("UPDATE interactions SET source_function_id = ?, target_function_id = ?, "
                         "description = COALESCE(?, description) WHERE id = ?",
                         (new_source, new_target, item.get('description'), edge['id']))
            manifest['changed_interactions'].append({'id': edge['id'], 'old_values': {
                'source_function_id': edge['source_function_id'],
                'target_function_id': edge['target_function_id'],
                'description': edge['description']}})
        done('repoint_interactions')

    # -- interactions: add ------------------------------------------------------
    for item in plan.add_interactions:
        source, target = item.get('source_function_id'), item.get('target_function_id')
        kind = item.get('interaction_type') or 'call'
        if kind not in VALID_INTERACTION_TYPES:
            skip('add_interactions', item, f"invalid interaction_type '{kind}'")
            continue
        if not (function_exists(source) and function_exists(target)):
            skip('add_interactions', item, 'endpoint function not found')
            continue
        if source == target:
            skip('add_interactions', item, 'self-edge')
            continue
        if _row(conn, "SELECT id FROM interactions WHERE source_function_id = ? AND target_function_id = ? "
                      "AND interaction_type = ?", (source, target, kind)):
            skip('add_interactions', item, 'already tracked')
            continue
        indirect = 1 if item.get('is_indirect') else 0
        if indirect and has_indirect:
            conn.execute("INSERT INTO interactions (source_function_id, target_function_id, interaction_type, "
                         "description, is_indirect) VALUES (?, ?, ?, ?, 1)",
                         (source, target, kind, item.get('description')))
        else:
            conn.execute("INSERT INTO interactions (source_function_id, target_function_id, interaction_type, "
                         "description) VALUES (?, ?, ?, ?)", (source, target, kind, item.get('description')))
        done('add_interactions')

    # -- interactions: indirect flag -------------------------------------------
    for item in plan.set_indirect:
        if not has_indirect:
            skip('set_indirect', item, 'database predates schema 1.15; run migrate_databases')
            continue
        edge = _row(conn, "SELECT id, is_indirect FROM interactions WHERE id = ?", (item.get('interaction_id'),))
        if edge is None:
            skip('set_indirect', item, 'interaction not found')
            continue
        conn.execute("UPDATE interactions SET is_indirect = ? WHERE id = ?", (item['is_indirect'], edge['id']))
        manifest['changed_interactions'].append({'id': edge['id'], 'old_values': {'is_indirect': edge['is_indirect']}})
        done('set_indirect')

    # -- interactions: delete ---------------------------------------------------
    for item in plan.delete_interactions:
        edge = _row(conn, "SELECT * FROM interactions WHERE id = ?", (item.get('interaction_id'),))
        if edge is None:
            skip('delete_interactions', item, 'interaction not found')
            continue
        if not edge_matches(edge, item):
            skip('delete_interactions', item, 'edge changed since the audit')
            continue
        if item.get('_from') and edge.get('is_indirect'):
            skip('delete_interactions', item, 'marked indirect: deliberate, kept')
            continue
        conn.execute("DELETE FROM interactions WHERE id = ?", (edge['id'],))
        manifest['deleted_interactions'].append(edge)
        done('delete_interactions')

    # -- type links -------------------------------------------------------------
    for item in plan.add_type_links:
        type_id, function_id, role = item.get('type_id'), item.get('function_id'), item.get('role')
        if role is not None and role not in VALID_ROLES:
            skip('add_type_links', item, f"invalid role '{role}'")
            continue
        if _row(conn, "SELECT id FROM types WHERE id = ?", (type_id,)) is None or not function_exists(function_id):
            skip('add_type_links', item, 'type or function not found')
            continue
        if _row(conn, "SELECT id FROM types_functions WHERE type_id = ? AND function_id = ? AND role IS ?",
                (type_id, function_id, role)):
            skip('add_type_links', item, 'already linked')
            continue
        conn.execute("INSERT INTO types_functions (type_id, function_id, role) VALUES (?, ?, ?)",
                     (type_id, function_id, role))
        done('add_type_links')

    # -- functions: signature refresh, JSON normalization, hash baseline -------
    for item in plan.refresh_signatures:
        fn = _function_with_path(conn, item.get('function_id'))
        if fn is None:
            skip('refresh_signatures', item, 'function not found')
            continue
        extracted = _source_function(project_root, fn['path'] or '', fn['name'], sources)
        if extracted is None:
            skip('refresh_signatures', item, 'not defined in the source at full fidelity')
            continue
        params = encode_json_field(parameters_from_source(extracted, decode_json_layers(fn['parameters'])))
        conn.execute("UPDATE functions SET parameters = ? WHERE id = ?", (params, fn['id']))
        manifest['changed_functions'].append({'id': fn['id'], 'old_values': {'parameters': fn['parameters']}})
        done('refresh_signatures')

    for item in plan.normalize_json:
        table, column, row_id = item.get('table'), item.get('column'), item.get('id')
        allowed = {'functions': ('parameters', 'returns'), 'types': ('definition_json', 'links')}
        if column not in allowed.get(table, ()):
            skip('normalize_json', item, 'not a JSON column audits normalize')
            continue
        if item.get('fixable') is False:
            skip('normalize_json', item, f"{item.get('problem', 'malformed')}: rewrite it (update_function / update_type)")
            continue
        current = _row(conn, f"SELECT {column} AS value FROM {table} WHERE id = ?", (row_id,))
        if current is None:
            skip('normalize_json', item, f'{table} row not found')
            continue
        fixed = encode_json_field(current['value'])
        if fixed == current['value']:
            skip('normalize_json', item, 'already well-formed')
            continue
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (fixed, row_id))
        manifest[f'changed_{table}'].append({'id': row_id, 'old_values': {column: current['value']}})
        done('normalize_json')

    for item in plan.baseline_hashes:
        if not has_hash:
            skip('baseline_hashes', item, 'database predates schema 1.15; run migrate_databases')
            continue
        fn = _function_with_path(conn, item.get('function_id'))
        if fn is None:
            skip('baseline_hashes', item, 'function not found')
            continue
        extracted = _source_function(project_root, fn['path'] or '', fn['name'], sources)
        if extracted is None or not extracted.body_hash:
            skip('baseline_hashes', item, 'not defined in the source at full fidelity')
            continue
        conn.execute("UPDATE functions SET source_hash = ? WHERE id = ?", (extracted.body_hash, fn['id']))
        manifest['changed_functions'].append({'id': fn['id'], 'old_values': {'source_hash': fn['source_hash']}})
        done('baseline_hashes')

    # -- audit exemptions ---------------------------------------------------------
    for item in plan.add_exemptions:
        if not has_exemptions:
            skip('add_exemptions', item, 'database predates schema 1.16; run migrate_databases')
            continue
        problem = exemption_problem(item)
        if problem:
            skip('add_exemptions', item, problem)
            continue
        kind = 'function' if item.get('function_id') is not None else 'type'
        entity_id = item[f'{kind}_id']
        entity = (_function_with_path(conn, entity_id) if kind == 'function'
                  else _row(conn, "SELECT id FROM types WHERE id = ?", (entity_id,)))
        if entity is None:
            skip('add_exemptions', item, f'{kind} not found')
            continue
        if _row(conn, f"SELECT id FROM audit_exemptions WHERE category = ? AND {kind}_id = ?",
                (item['category'], entity_id)):
            skip('add_exemptions', item, 'already exempted (unexempt_ids first to change the reason)')
            continue
        extracted = (_source_function(project_root, entity['path'] or '', entity['name'], sources)
                     if kind == 'function' else None)
        conn.execute(f"INSERT INTO audit_exemptions (category, {kind}_id, reason, code_hash) VALUES (?, ?, ?, ?)",
                     (item['category'], entity_id, item['reason'], extracted.body_hash if extracted else None))
        done('add_exemptions')

    for item in plan.delete_exemptions:
        row = _row(conn, "SELECT * FROM audit_exemptions WHERE id = ?", (item.get('exemption_id'),)) if has_exemptions else None
        if row is None:
            skip('delete_exemptions', item, 'exemption not found')
            continue
        conn.execute("DELETE FROM audit_exemptions WHERE id = ?", (row['id'],))
        manifest['deleted_exemptions'].append(row)
        done('delete_exemptions')

    # -- deletes with cascades (last) ------------------------------------------
    for item in plan.delete_types:
        ty = _row(conn, "SELECT * FROM types WHERE id = ?", (item.get('type_id'),))
        if ty is None:
            skip('delete_types', item, 'type not found')
            continue
        if item.get('name') and item['name'] != ty['name']:
            skip('delete_types', item, 'type renamed since the audit')
            continue
        manifest['deleted_type_links'].extend(_rows(conn, "SELECT * FROM types_functions WHERE type_id = ?", (ty['id'],)))
        if has_exemptions:
            manifest['deleted_exemptions'].extend(_rows(conn, "SELECT * FROM audit_exemptions WHERE type_id = ?", (ty['id'],)))
        conn.execute("DELETE FROM types WHERE id = ?", (ty['id'],))
        manifest['deleted_types'].append(ty)
        done('delete_types')

    for item in plan.delete_functions:
        fn = _row(conn, "SELECT * FROM functions WHERE id = ?", (item.get('function_id'),))
        if fn is None:
            skip('delete_functions', item, 'function not found')
            continue
        if item.get('name') and item['name'] != fn['name']:
            skip('delete_functions', item, 'function renamed since the audit')
            continue
        successor = (item.get('moved_to') or {}).get('function_id')
        if successor is not None and successor != fn['id'] and function_exists(successor):
            _hand_over(conn, fn['id'], successor, manifest)
            done('repoint_to_successor')
        manifest['deleted_interactions'].extend(_rows(
            conn, "SELECT * FROM interactions WHERE source_function_id = ? OR target_function_id = ?",
            (fn['id'], fn['id'])))
        manifest['deleted_type_links'].extend(_rows(conn, "SELECT * FROM types_functions WHERE function_id = ?", (fn['id'],)))
        if has_exemptions:
            manifest['deleted_exemptions'].extend(_rows(conn, "SELECT * FROM audit_exemptions WHERE function_id = ?", (fn['id'],)))
        conn.execute("DELETE FROM functions WHERE id = ?", (fn['id'],))
        manifest['deleted_functions'].append(fn)
        done('delete_functions')

    return (applied, skipped, manifest)


# ============================================================================
# Public Tool
# ============================================================================

def apply_audit_fixes(
    reason: str,
    report: Optional[str] = None,
    apply: Optional[List[str]] = None,
    add_interactions: Optional[List[Dict[str, Any]]] = None,
    repoint_interactions: Optional[List[Dict[str, Any]]] = None,
    delete_interaction_ids: Optional[List[int]] = None,
    mark_indirect_ids: Optional[List[int]] = None,
    unmark_indirect_ids: Optional[List[int]] = None,
    delete_function_ids: Optional[List[int]] = None,
    delete_type_ids: Optional[List[int]] = None,
    refresh_signature_function_ids: Optional[List[int]] = None,
    baseline_hash_function_ids: Optional[List[int]] = None,
    add_type_links: Optional[List[Dict[str, Any]]] = None,
    exclude: Optional[Dict[str, List[Any]]] = None,
    exempt: Optional[List[Dict[str, Any]]] = None,
    unexempt_ids: Optional[List[int]] = None,
    dry_run: bool = False,
    cleanup: bool = False,
    project_root: Optional[str] = None,
) -> ApplyResult:
    """
    Apply audit findings in one transaction, with a restore manifest.

    Args:
        reason: Why these changes are made (recorded in the manifest note)
        report: report_path returned by an audit tool
        apply: Finding categories of that report to apply (see module docs)
        add_interactions: [{source_function_id, target_function_id, interaction_type?, description?, is_indirect?}]
        repoint_interactions: [{interaction_id, source_function_id?, target_function_id?, description?}]
        delete_interaction_ids: Interactions to delete
        mark_indirect_ids: Interactions to mark is_indirect (deliberate indirect edges)
        unmark_indirect_ids: Interactions to clear is_indirect on
        delete_function_ids: Functions to delete (edges and type links cascade, all in the manifest)
        delete_type_ids: Types to delete (type links cascade, in the manifest)
        refresh_signature_function_ids: Functions whose parameters are re-read from source
        baseline_hash_function_ids: Functions whose current code hash is recorded as reviewed
        add_type_links: [{type_id, function_id, role?}]
        exclude: Findings of applied categories to hold back, {category: [id, ...]};
            missing_interactions takes [source_function_id, target_function_id]
            pairs and missing_type_links [type_id, function_id] pairs (FINDING_KEYS)
        exempt: Reviewed findings that are not drift, [{category, function_id | type_id,
            reason?}] (EXEMPTABLE categories; reason defaults to the call's). Audits list
            them under 'exempted' from then on; a function exemption lapses when its code changes
        unexempt_ids: audit_exemptions ids to remove (the finding is reported again)
        dry_run: Do everything, then roll back
        cleanup: Delete the report file after a successful real run
        project_root: Explicit root for embedding hosts (defaults to the session root)

    Returns:
        ApplyResult: counts per operation, skipped rows with reasons, manifest note id
    """
    root = project_root or get_cached_project_root()
    if not (reason or '').strip():
        return ApplyResult(success=False, dry_run=dry_run, error="reason is required: it goes into the restore manifest")

    report_plan, refused, excluded = FixPlan(), (), 0
    if exclude and not apply:
        return ApplyResult(success=False, dry_run=dry_run, error="exclude holds back findings of applied categories; pass report and apply too")
    if apply and not report:
        return ApplyResult(success=False, dry_run=dry_run, error="apply names report categories; pass report=<report_path> too")
    if report:
        loaded, error = _effect_load_report(root, report)
        if loaded is None:
            return ApplyResult(success=False, dry_run=dry_run, error=error)
        categories = tuple(apply or ())
        refused = tuple(c for c in categories if c not in CATEGORY_OPERATIONS)
        if refused:
            return ApplyResult(success=False, dry_run=dry_run, error=(
                f"Not appliable from a report: {', '.join(refused)}. Appliable categories: "
                f"{', '.join(sorted(CATEGORY_OPERATIONS))}. The others need judgement — "
                "rewrite prose, mark_indirect_ids, or reconcile_paths."))
        problem = exclusion_problem(exclude or {}, categories)
        if problem:
            return ApplyResult(success=False, dry_run=dry_run, error=problem)
        loaded, excluded = exclude_findings(loaded, exclude or {})
        report_plan, _ = plan_from_report(loaded, categories)

    plan = merge_plans(report_plan, plan_from_arguments(
        add_interactions, repoint_interactions, delete_interaction_ids, mark_indirect_ids,
        unmark_indirect_ids, delete_function_ids, delete_type_ids, refresh_signature_function_ids,
        baseline_hash_function_ids, add_type_links, exempt, unexempt_ids, reason,
    ))
    if plan_size(plan) == 0:
        return ApplyResult(success=False, dry_run=dry_run, error="Nothing to apply: name report categories or pass explicit changes.")

    conn = _open_project_connection(root)
    try:
        applied, skipped, manifest = _apply_plan(conn, root, plan)
        note_id = None
        if any(manifest.values()):
            cursor = conn.execute(
                "INSERT INTO notes (content, source, severity, note_type) "
                "VALUES (?, 'ai', 'info', 'entry_deletion')",
                (manifest_content(reason.strip(), manifest, dry_run),))
            note_id = cursor.lastrowid
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception as exc:
        conn.rollback()
        return ApplyResult(success=False, dry_run=dry_run, error=f"Apply failed, nothing changed: {exc}")
    finally:
        conn.close()

    report_deleted = bool(cleanup and report and not dry_run and _effect_delete_report(root, report))
    payload = {'applied': applied, 'skipped_count': len(skipped), 'excluded_count': excluded, 'dry_run': dry_run}
    return ApplyResult(
        success=True,
        dry_run=dry_run,
        applied=applied,
        skipped_count=len(skipped),
        skipped=tuple(skipped[:SKIPPED_SAMPLE]),
        excluded_count=excluded,
        manifest_note_id=None if dry_run else note_id,
        report_deleted=report_deleted,
        return_statements=gate_return_statements(get_return_statements('apply_audit_fixes'), payload),
    )
