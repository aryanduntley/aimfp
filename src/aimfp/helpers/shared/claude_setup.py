"""
AIMFP Helper Functions - Claude Code / Session Bootstrap

Two bootstrap helpers for getting AIMFP active in a client that installed it
without a manual download (e.g. the Claude Code plugin), in the order a user
should run them:

- get_system_prompt:      returns the AIMFP system prompt plus where/how to
                          place it. The system prompt is AIMFP's record-keeping
                          backbone — the AI must load it FIRST, before it can
                          meaningfully run init / discovery / anything else.
- get_claude_permissions: returns the Claude Code allowlist
                          (.claude/settings.local.json) that pre-approves every
                          AIMFP MCP tool, so there's no per-tool approval prompt.

Neither helper writes any file. Each returns content + a target path, and the
AI performs the write with its own tools. AIMFP itself never touches anything
outside the project's .aimfp-project/ folder — honoring that guarantee is why
the AI, not AIMFP, does the write.

All functions are pure FP - immutable data, explicit parameters, Result types.
"""

import json
from pathlib import Path
from typing import Any, Dict

# Import global utilities
from ..utils import get_return_statements, Result


# ============================================================================
# Constants
# ============================================================================

TOOL_PREFIX: str = "mcp__aimfp__"

# Claude Code names a plugin-bundled server's tools
# mcp__plugin_<plugin>_<server>__<tool>; the aimfp plugin's server is "aimfp".
PLUGIN_TOOL_PREFIX: str = "mcp__plugin_aimfp_aimfp__"

# Both install routes (project .mcp.json and the Claude Code plugin) are
# allowlisted, so the file is correct whichever one the user runs.
TOOL_PREFIXES: tuple = (TOOL_PREFIX, PLUGIN_TOOL_PREFIX)

TARGET_PATH: str = ".claude/settings.local.json"

# Bundled system prompt (same file the MCP server serves via the initialize
# handshake and `python3 -m aimfp --system-prompt` prints). shared/ -> helpers
# -> aimfp, then reference/system_prompt.txt.
SYSTEM_PROMPT_PATH = (
    Path(__file__).parent.parent.parent / "reference" / "system_prompt.txt"
)

# Recommended target for Claude Code. Desktop users place it in
# Settings -> Custom Instructions instead (covered in `instructions`).
SYSTEM_PROMPT_TARGET: str = "CLAUDE.md"

# Existing target content longer than this is treated as "extensive" — the AI
# should review and discuss consolidation rather than blindly prepend.
EXTENSIVE_CHARS: int = 600


# ============================================================================
# Pure Helpers
# ============================================================================

def _desired_aimfp_entries() -> list:
    """Pure: the full, sorted set of AIMFP allow entries (one per tool for
    each prefix in TOOL_PREFIXES), derived live from TOOL_REGISTRY so it can
    never drift from the real tool set."""
    # Local import: avoids any import-order coupling with the lazy-loading
    # registry (registry imports helper modules on demand via importlib).
    from ...mcp_server.registry import TOOL_REGISTRY
    return sorted(f"{prefix}{name}" for prefix in TOOL_PREFIXES for name in TOOL_REGISTRY)


def _parse_existing(existing_settings: str):
    """Pure: parse the caller-supplied settings file content.

    Returns (base_dict, warning_or_None). Never raises — malformed input
    degrades to a fresh file plus an explanatory warning.
    """
    if not existing_settings or not existing_settings.strip():
        return {}, None
    try:
        parsed = json.loads(existing_settings)
    except json.JSONDecodeError as e:
        return {}, (
            f"existing_settings was not valid JSON ({e}); ignoring it and "
            f"returning a fresh settings file."
        )
    if not isinstance(parsed, dict):
        return {}, (
            "existing_settings was valid JSON but not a JSON object; ignoring "
            "it and returning a fresh settings file."
        )
    return parsed, None


# ============================================================================
# Public Helper Functions
# ============================================================================

def get_system_prompt(existing_content: str = "") -> Result:
    """
    Return the AIMFP system prompt and how to place it for the user.

    The system prompt is AIMFP's record-keeping backbone: it is what tells the
    AI to call aimfp_run() first and operate the project through AIMFP. Until
    it is in the client's instructions, the AI has no reason to run init or
    discovery — so placing it is the user's FIRST move (especially for plugin
    installs, where nothing was downloaded manually). The user asks the AI to
    "add the AIMFP system prompt"; the AI calls this and writes the file.

    This helper does not write anything — it returns the prompt text plus a
    target path and explicit placement guidance, and the AI does the write
    with its own tools (AIMFP never writes outside .aimfp-project/).

    Placement policy encoded in `instructions`:
      * No existing instructions file -> create it with the AIMFP prompt.
      * Existing but small            -> PREPEND the AIMFP prompt above the
                                         existing content (never overwrite).
      * Existing and extensive        -> do NOT blindly prepend; first review
                                         it with the user and discuss
                                         consolidation / optimization, then
                                         place the AIMFP content FIRST.
      AIMFP content always goes first — it is the project's record-keeping
      backbone and must be the highest-priority instruction.

    Args:
        existing_content: Current text of the target instructions file
            (e.g. CLAUDE.md) if it already exists — read it and pass it in so
            the guidance can account for size/consolidation. Omit/empty if
            there is no existing file.

    Returns:
        Result with data={
            content: str (the full AIMFP system prompt, verbatim),
            target_path: str ("CLAUDE.md", project-relative; Desktop = Custom
                Instructions, see instructions),
            placement: "prepend" (AIMFP content goes first),
            existing_is_extensive: bool,
            instructions: str (what the AI should do, per the policy above),
            source: str (path the prompt was read from)
        }

    On error:
        Result with error message (only if the bundled prompt is missing).
    """
    try:
        if not SYSTEM_PROMPT_PATH.is_file():
            return Result(
                success=False,
                error=(
                    f"AIMFP system prompt not found at {SYSTEM_PROMPT_PATH}. "
                    "The package/plugin install may be incomplete; reinstall "
                    "AIMFP."
                ),
            )

        content = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        has_existing = bool(existing_content and existing_content.strip())
        extensive = has_existing and len(existing_content.strip()) > EXTENSIVE_CHARS

        if not has_existing:
            placement_note = (
                f"No existing instructions file. Create "
                f"`<project_root>/{SYSTEM_PROMPT_TARGET}` containing `content` "
                f"verbatim."
            )
        elif not extensive:
            placement_note = (
                f"`{SYSTEM_PROMPT_TARGET}` already exists but is short. "
                f"PREPEND `content` above the existing text — do NOT overwrite "
                f"or discard what is there. The AIMFP block must come FIRST."
            )
        else:
            placement_note = (
                f"`{SYSTEM_PROMPT_TARGET}` already exists and is extensive. Do "
                f"NOT blindly prepend. First show the user what is there, then "
                f"discuss consolidating and optimizing it together with the "
                f"AIMFP prompt (remove redundancy/conflicts, tighten it). When "
                f"you write the result, the AIMFP content must still come "
                f"FIRST — it is the project's record-keeping backbone and the "
                f"highest-priority instruction."
            )

        instructions = (
            placement_note
            + " For Claude Desktop, place it in Settings -> Custom "
            "Instructions instead (still AIMFP content first). AIMFP does not "
            "write this file — you do, with the user's normal file-write "
            "confirmation; AIMFP never touches anything outside "
            ".aimfp-project/."
        )

        data: Dict[str, Any] = {
            "content": content,
            "target_path": SYSTEM_PROMPT_TARGET,
            "placement": "prepend",
            "existing_is_extensive": extensive,
            "instructions": instructions,
            "source": str(SYSTEM_PROMPT_PATH),
        }

        return Result(
            success=True,
            data=data,
            return_statements=get_return_statements("get_system_prompt"),
        )

    except Exception as e:
        return Result(
            success=False,
            error=f"Error reading AIMFP system prompt: {str(e)}",
        )


def get_claude_permissions(existing_settings: str = "") -> Result:
    """
    Build the Claude Code allowlist that pre-approves every AIMFP tool.

    Claude Code prompts for approval the first time each MCP tool is called.
    Dropping a `.claude/settings.local.json` allowlist into the project skips
    those prompts. This helper returns that file's contents (derived live from
    the tool registry, so it is always complete and current).

    It does not write anything — the AI writes/merges the returned JSON into
    the project's `.claude/settings.local.json`. AIMFP never writes outside
    `.aimfp-project/`; honoring that guarantee is why the AI does the write.

    Args:
        existing_settings: The current text of `.claude/settings.local.json`
            if the file already exists (read it and pass it in). When given,
            the result is a MERGE: every non-AIMFP `permissions.allow` entry
            and every other top-level key is preserved untouched; only the
            AIMFP entries (mcp__aimfp__* and the plugin's
            mcp__plugin_aimfp_aimfp__*) are refreshed and the two MCP-autostart
            keys are ensured. Omit/empty to get a fresh file.

    Returns:
        Result with data={
            target_path: str (".claude/settings.local.json", project-relative),
            settings: dict (the final merged settings object),
            settings_json: str (pretty-printed, ready to write verbatim),
            aimfp_tool_count: int (registered tools; the allowlist holds
                one entry per tool for each of the two prefixes),
            added: list (AIMFP entries newly added vs existing),
            removed_stale: list (AIMFP entries dropped — no longer
                registered tools),
            preserved_other_permissions: list (non-AIMFP allow entries kept),
            merged: bool (True if merged into supplied existing settings),
            instructions: str (what the AI should do next),
            warning: str (only present if existing_settings was unusable)
        }

    On error:
        Result with error message (only on unexpected failures).
    """
    try:
        desired_aimfp = _desired_aimfp_entries()
        base, warning = _parse_existing(existing_settings)
        merged = bool(base)

        # Deep copy so we never mutate caller-derived structures.
        settings: Dict[str, Any] = json.loads(json.dumps(base)) if base else {}

        # --- permissions.allow: refresh aimfp entries, preserve everything else
        permissions = settings.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
        allow = permissions.get("allow")
        if not isinstance(allow, list):
            allow = []

        existing_aimfp = [
            a for a in allow
            if isinstance(a, str) and a.startswith(TOOL_PREFIXES)
        ]
        other_perms = [
            a for a in allow
            if not (isinstance(a, str) and a.startswith(TOOL_PREFIXES))
        ]

        added = sorted(set(desired_aimfp) - set(existing_aimfp))
        removed_stale = sorted(set(existing_aimfp) - set(desired_aimfp))

        # Non-AIMFP entries kept in their original order; aimfp entries sorted.
        permissions["allow"] = other_perms + desired_aimfp
        settings["permissions"] = permissions

        # --- MCP autostart keys: ensure aimfp without dropping other servers
        settings["enableAllProjectMcpServers"] = True
        servers = settings.get("enabledMcpjsonServers")
        if not isinstance(servers, list):
            servers = []
        if "aimfp" not in servers:
            servers = servers + ["aimfp"]
        settings["enabledMcpjsonServers"] = servers

        settings_json = json.dumps(settings, indent=2) + "\n"

        instructions = (
            f"Write `settings_json` verbatim to `<project_root>/{TARGET_PATH}` "
            f"(create the `.claude/` folder if missing). "
            + (
                "This MERGES with the existing file: non-AIMFP permissions and "
                "all other settings were preserved. "
                if merged else
                "No existing file was supplied, so this is a fresh allowlist. "
            )
            + "Claude Code reads it automatically — after writing, AIMFP tools "
            "no longer prompt for approval. AIMFP never reads or writes outside "
            "the project; this file lives in the project's own .claude/ folder."
        )

        data: Dict[str, Any] = {
            "target_path": TARGET_PATH,
            "settings": settings,
            "settings_json": settings_json,
            "aimfp_tool_count": len(desired_aimfp) // len(TOOL_PREFIXES),
            "added": added,
            "removed_stale": removed_stale,
            "preserved_other_permissions": other_perms,
            "merged": merged,
            "instructions": instructions,
        }
        if warning:
            data["warning"] = warning

        return Result(
            success=True,
            data=data,
            return_statements=get_return_statements("get_claude_permissions"),
        )

    except Exception as e:
        return Result(
            success=False,
            error=f"Error building Claude Code permissions: {str(e)}",
        )
