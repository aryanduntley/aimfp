"""
dev/helpers-json/*.json specs must mirror the real Python signatures.

server.py builds each tool's inputSchema from helper_functions.parameters, so a
spec that has drifted from its function produces a tool the AI calls wrongly —
and the JSON is the dev source of truth, so drift here ships.

Note 23 records the contract; this enforces it. Written after a manual sweep
found `add_subtask` declaring its parameters in a different order than the
function takes them, which no existing test caught.

Deliberately NOT in the spec (and asserted as such below):
  - `project_root` on the ~145 tracking helpers — the Hook A embedding kwarg,
    for svamanas, not for the MCP AI. Helpers whose caller is outside code (the
    UC2 runtime hooks) DO declare it, because their caller must pass it.
  - `aimfp_run.start_watchdog` and `aimfp_init.init_git` — embedding-host
    parameters.
"""
import glob
import importlib
import inspect
import json
import os

import pytest

HELPERS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "dev", "helpers-json")

# Embedding-host parameters, hidden from the AI by design (note 23).
HIDDEN_NAMED = {
    ("aimfp_run", "start_watchdog"),
    ("aimfp_init", "init_git"),
}


def _specs():
    """(helper_name, spec, module_path) for every helper JSON entry."""
    out = []
    for path in sorted(glob.glob(os.path.join(HELPERS_DIR, "helpers-*.json"))):
        for h in json.load(open(path)).get("helpers", []):
            out.append((h["name"], h, os.path.basename(path)))
    return out


def _resolve(spec):
    """The live function for a spec, or None when it cannot be imported."""
    file_path = spec.get("file_path")
    if not file_path or not file_path.endswith(".py"):
        return None
    module_path = "aimfp." + file_path[:-3].replace("/", ".")
    try:
        module = importlib.import_module(module_path)
    except Exception:
        return None
    return getattr(module, spec["name"], None)


ALL_SPECS = _specs()


def test_specs_were_found():
    """Guard against the glob silently matching nothing."""
    assert len(ALL_SPECS) > 250


@pytest.mark.parametrize("name,spec,source", ALL_SPECS,
                         ids=[s[0] for s in ALL_SPECS])
def test_declared_file_path_contains_the_function(name, spec, source):
    """A spec pointing at code that does not exist is undetectable at runtime."""
    assert spec.get("file_path"), f"{name} ({source}) has no file_path"
    full = os.path.join(
        os.path.dirname(__file__), "..", "..", "src", "aimfp", spec["file_path"])
    assert os.path.isfile(full), f"{name}: {spec['file_path']} does not exist"
    assert _resolve(spec) is not None, \
        f"{name} is not defined in {spec['file_path']}"


@pytest.mark.parametrize("name,spec,source", ALL_SPECS,
                         ids=[s[0] for s in ALL_SPECS])
def test_parameters_mirror_the_signature(name, spec, source):
    """
    Same parameters, same ORDER. Order matters because the spec is the
    documented signature, and a reader who trusts it writes the wrong call.
    """
    fn = _resolve(spec)
    if fn is None:
        pytest.skip(f"{name} not importable")

    declared = [p["name"] for p in (spec.get("parameters") or [])]
    real = [
        p.name for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        and (name, p.name) not in HIDDEN_NAMED
    ]

    # project_root is hidden unless the spec deliberately exposes it.
    if "project_root" not in declared:
        real = [p for p in real if p != "project_root"]

    assert declared == real, (
        f"{name} ({source}) spec/signature drift:\n"
        f"  spec: {declared}\n  code: {real}"
    )


@pytest.mark.parametrize("name,spec,source", ALL_SPECS,
                         ids=[s[0] for s in ALL_SPECS])
def test_required_and_defaults_match(name, spec, source):
    """required iff the code has no default; declared default == code default."""
    fn = _resolve(spec)
    if fn is None:
        pytest.skip(f"{name} not importable")

    params = inspect.signature(fn).parameters
    for entry in (spec.get("parameters") or []):
        p = params.get(entry["name"])
        if p is None:
            continue
        has_default = p.default is not inspect.Parameter.empty
        assert bool(entry.get("required")) != has_default, (
            f"{name}.{entry['name']}: spec required={entry.get('required')} "
            f"but code {'has' if has_default else 'has no'} default"
        )
        if has_default and entry.get("default") is not None:
            assert str(entry["default"]) == str(p.default), (
                f"{name}.{entry['name']}: spec default={entry['default']!r} "
                f"code default={p.default!r}"
            )
