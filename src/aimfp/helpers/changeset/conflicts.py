"""
AIMFP Helper Functions - Detect Cross-Branch State Conflicts

`detect_state_conflicts(branches)` — OPTIONAL pre-apply, all-at-once overlap report across
several pending worker branches, so InterCommAIMFP's master can sequence or re-partition
BEFORE applying any changeset. Pure read (built entirely from export_state_changeset).

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

This is a heuristic planning aid, not a verdict: an "overlap" means two branches touch the
same semantic entity/edge — which the master may want to avoid (re-partition) or order
deliberately. The authoritative 3-way resolution still happens in apply_state_changeset.
"""

from typing import Dict, List, Any

from ..utils import Result, get_return_statements
from ._common import serialize_key
from .export import export_state_changeset


def _severity(ops: set) -> str:
    """Heuristic severity for a set of ops touching one entity across branches."""
    if "delete" in ops and ops != {"delete"}:
        return "delete_vs_change"      # one branch deletes, another changes/keeps — highest risk
    if ops == {"add"}:
        return "duplicate_add"         # both create the same entity independently
    if ops == {"modify"}:
        return "concurrent_modify"     # both edit the same entity
    if ops == {"delete"}:
        return "concurrent_delete"     # both delete — usually benign (idempotent)
    return "mixed"                     # e.g. add + modify


def detect_state_conflicts(branches: List[Dict[str, Any]]) -> Result:
    """
    Report where pending branches would collide on the same semantic keys.

    Args:
        branches: list of {branch, base_commit, worker_id?} dicts. Each is exported and the
                  touched semantic keys/edges are cross-referenced.

    Returns:
        Result with data={
          clean: bool,                       # True if no entity/edge is touched by >1 branch
          branches_analyzed: [branch names],
          entity_overlaps: [{kind, semantic_key, severity, touched_by:[{branch,op}]}],
          reference_overlaps: [{kind, identity, severity, touched_by:[{branch,op}]}],
          export_errors: [{branch, error}],
        }
    """
    if not isinstance(branches, list) or not branches:
        return Result(success=False, error="branches must be a non-empty list of {branch, base_commit}")

    # serialized-key -> {"kind":..., "key":..., "touched": [(branch, op)]}
    entity_touch: Dict[str, Dict[str, Any]] = {}
    reference_touch: Dict[str, Dict[str, Any]] = {}
    analyzed: List[str] = []
    export_errors: List[dict] = []

    for b in branches:
        branch = b.get("branch")
        base = b.get("base_commit")
        if not branch or not base:
            export_errors.append({"branch": branch, "error": "missing branch or base_commit"})
            continue

        cs = export_state_changeset(base, branch, b.get("worker_id"))
        if not cs.success:
            export_errors.append({"branch": branch, "error": cs.error})
            continue
        analyzed.append(branch)

        for e in cs.data["entities"]:
            sk = f'{e["kind"]}|{serialize_key(e["semantic_key"])}'
            slot = entity_touch.setdefault(sk, {"kind": e["kind"], "key": e["semantic_key"], "touched": []})
            slot["touched"].append({"branch": branch, "op": e["op"]})

        for r in cs.data["references"]:
            identity = {k: v for k, v in r.items() if k not in ("op", "attributes")}
            sk = serialize_key(identity)
            slot = reference_touch.setdefault(sk, {"kind": r.get("kind"), "identity": identity, "touched": []})
            slot["touched"].append({"branch": branch, "op": r["op"]})

    def _overlaps(touch_map, key_field):
        out = []
        for slot in touch_map.values():
            branches_hit = {t["branch"] for t in slot["touched"]}
            if len(branches_hit) > 1:
                ops = {t["op"] for t in slot["touched"]}
                out.append({
                    "kind": slot["kind"],
                    key_field: slot[key_field if key_field != "semantic_key" else "key"],
                    "severity": _severity(ops),
                    "touched_by": slot["touched"],
                })
        return out

    entity_overlaps = _overlaps(entity_touch, "semantic_key")
    reference_overlaps = _overlaps(reference_touch, "identity")

    return Result(
        success=True,
        data={
            "clean": not entity_overlaps and not reference_overlaps,
            "branches_analyzed": analyzed,
            "entity_overlaps": entity_overlaps,
            "reference_overlaps": reference_overlaps,
            "export_errors": export_errors,
        },
        return_statements=get_return_statements("detect_state_conflicts"),
    )
