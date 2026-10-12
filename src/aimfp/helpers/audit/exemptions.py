"""
AIMFP Helper Functions - Audit Exemptions

An audit is mechanical; some code is not. A function defined in a way the
audit cannot see (generated, defined inside an ``if`` at module level), a
signature it cannot read, prose that names another language's built-ins: once
a person has reviewed such a finding and found it is not drift, it must stop
being reported, or it blocks every task that touches the file.

audit_exemptions (project schema 1.16) records one reviewed finding per row:
the category, the function or type, and the reason. Audits move an exempted
finding into the info category 'exempted' (with its reason), so it stays
visible but never blocks or warns.

A function exemption records the function's code hash when it is made and
lapses when the code changes: the finding comes back, marked
exemption_lapsed, for a fresh look. Interactions have is_indirect instead.

apply_audit_fixes(exempt=[...], unexempt_ids=[...]) writes and removes them.
"""

from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .code_index import CodeIndex


# Category -> the entity kinds an exemption of it can name.
EXEMPTABLE: Dict[str, FrozenSet[str]] = {
    'stale_functions': frozenset({'function'}),
    'param_mismatch': frozenset({'function'}),
    'incomplete_functions': frozenset({'function'}),
    'malformed_json': frozenset({'function', 'type'}),
    'dangling_mentions': frozenset({'function', 'type'}),
    'stale_types': frozenset({'type'}),
    'incomplete_types': frozenset({'type'}),
}

EntityRef = Tuple[str, int]


# ============================================================================
# Pure Helpers
# ============================================================================

def finding_entity(category: str, item: Dict[str, Any]) -> Optional[EntityRef]:
    """
    Pure: ('function' | 'type', id) a finding is about, or None when the
    category cannot be exempted.
    """
    kinds = EXEMPTABLE.get(category, frozenset())
    if category == 'malformed_json':
        kind = 'function' if item.get('table') == 'functions' else 'type'
        return (kind, item['id']) if kind in kinds and item.get('id') is not None else None
    if 'function' in kinds and item.get('function_id') is not None:
        return ('function', item['function_id'])
    if 'type' in kinds and item.get('type_id') is not None:
        return ('type', item['type_id'])
    return None


def exemption_key(row: Dict[str, Any]) -> Tuple[str, str, int]:
    """Pure: (category, kind, id) of an audit_exemptions row."""
    if row.get('function_id') is not None:
        return (row['category'], 'function', row['function_id'])
    return (row['category'], 'type', row['type_id'])


def is_lapsed(row: Dict[str, Any], current_hash: Optional[str]) -> bool:
    """
    Pure: Whether a function exemption no longer covers the code: it recorded
    a code hash and the code now hashes differently (or is gone).
    """
    return row.get('code_hash') is not None and current_hash != row['code_hash']


def apply_exemptions(
    findings: Dict[str, Tuple[Any, ...]],
    exemptions: Tuple[Dict[str, Any], ...],
    current_hashes: Dict[int, Optional[str]],
) -> Dict[str, Tuple[Any, ...]]:
    """
    Pure: Move exempted findings into 'exempted' (appended to any already
    there); mark findings whose exemption lapsed with exemption_lapsed.

    Args:
        findings: category -> findings, as an audit produced them
        exemptions: audit_exemptions rows
        current_hashes: function id -> its code hash now (None when gone)
    """
    if not exemptions:
        return findings
    by_key = {exemption_key(row): row for row in exemptions}
    out: Dict[str, Tuple[Any, ...]] = {}
    exempted: List[Dict[str, Any]] = list(findings.get('exempted', ()))
    for category, items in findings.items():
        if category not in EXEMPTABLE:
            out.setdefault(category, items)
            continue
        kept: List[Any] = []
        for item in items:
            entity = finding_entity(category, item)
            row = by_key.get((category,) + entity) if entity else None
            if row is None:
                kept.append(item)
            elif entity[0] == 'function' and is_lapsed(row, current_hashes.get(entity[1])):
                kept.append({**item, 'exemption_lapsed': row['id']})
            else:
                exempted.append({'category': category, f'{entity[0]}_id': entity[1],
                                 'name': item.get('name'), 'path': item.get('path'),
                                 'exemption_id': row['id'], 'reason': row['reason']})
        out[category] = tuple(kept)
    out['exempted'] = tuple(exempted)
    return out


def current_function_hashes(index: CodeIndex) -> Dict[int, Optional[str]]:
    """
    Pure: function id -> the code hash of its definition now, for tracked
    functions in in-scope full-fidelity files (None when the file no longer
    defines it).
    """
    defined = {
        f.file_id: {fn.name: fn.body_hash for fn in f.entities.functions}
        for f in index.files if f.fidelity == 'full' and f.file_id is not None
    }
    return {
        row['id']: defined[row['file_id']].get(row['name'])
        for row in index.snapshot.functions if row['file_id'] in defined
    }


def exempt_index_findings(index: CodeIndex, findings: Dict[str, Tuple[Any, ...]]) -> Dict[str, Tuple[Any, ...]]:
    """Pure: apply_exemptions with the index's exemption rows and current code hashes."""
    if not index.snapshot.exemptions:
        return findings
    return apply_exemptions(findings, index.snapshot.exemptions, current_function_hashes(index))
