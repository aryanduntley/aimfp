"""
AIMFP Watchdog - Background File System Monitor

Monitors project source files for changes and writes actionable
reminders to a JSON file. AI reads this via aimfp_run to stay
on track during long directive chains.

The watchdog acts as external memory — it never forgets, has no
context window, and catches things AI might miss: unregistered
functions, stale timestamps, files changed without DB updates.

Embedding hosts can run the watcher in-process via start_watcher /
stop_watcher (see embed.py). These are exported lazily so that the
MCP server's imports of config/reconciliation/reminders submodules
do not pull in the external observer dependency.
"""

_EMBED_EXPORTS = ("start_watcher", "stop_watcher", "WatcherHandle")


def __getattr__(name):
    """Lazy re-export of the in-process embedding API (PEP 562)."""
    if name in _EMBED_EXPORTS:
        from . import embed
        return getattr(embed, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
