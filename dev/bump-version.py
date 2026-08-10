#!/usr/bin/env python3
"""
Bump AIMFP version across all version files, then build.

Files updated:
  - pyproject.toml          (version = "X.Y.Z")
  - src/aimfp/__init__.py    (__version__ = "X.Y.Z")
  - src/aimfp/mcp_server/server.py  (SERVER_VERSION: Final[str] = "X.Y.Z")
  - .claude-plugin/plugin.json  ("version": "X.Y.Z")

After bumping, runs: rm -rf build dist src/*.egg-info && python3 -m build --no-isolation
"""

import re
import sys
import glob
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION_FILES = {
    "pyproject.toml": {
        "path": ROOT / "pyproject.toml",
        "pattern": r'^(version\s*=\s*")[^"]+(")',
        "replace": r'\g<1>{version}\2',
    },
    "src/aimfp/__init__.py": {
        "path": ROOT / "src" / "aimfp" / "__init__.py",
        "pattern": r'^(__version__\s*=\s*")[^"]+(")',
        "replace": r'\g<1>{version}\2',
    },
    "src/aimfp/mcp_server/server.py": {
        "path": ROOT / "src" / "aimfp" / "mcp_server" / "server.py",
        "pattern": r'^(SERVER_VERSION:\s*Final\[str\]\s*=\s*")[^"]+(")',
        "replace": r'\g<1>{version}\2',
    },
    ".claude-plugin/plugin.json": {
        "path": ROOT / ".claude-plugin" / "plugin.json",
        "pattern": r'^(\s*"version"\s*:\s*")[^"]+(")',
        "replace": r'\g<1>{version}\2',
    },
}

VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')


# Schema versions are NOT the package version — they live on their own cadence
# and are only bumped when a schema actually changes. They are reported here so
# a drift between the schema SQL and the DB that ships in the wheel is visible
# at release time, when it is cheap to fix.
SCHEMA_SQL_DIR = ROOT / "src" / "aimfp" / "database" / "schemas"
CORE_DB = ROOT / "src" / "aimfp" / "database" / "aimfp_core.db"

# Version seeded by each schema SQL file: INSERT ... VALUES (1, 'X.Y')
SCHEMA_SEED_RE = re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+schema_version\s*\([^)]*\)\s*VALUES\s*\(\s*1\s*,\s*'([^']+)'\s*\)",
    re.IGNORECASE,
)


def _seeded_schema_version(sql_path: Path) -> str | None:
    """Version a fresh DB gets, read from its schema SQL."""
    if not sql_path.exists():
        return None
    m = SCHEMA_SEED_RE.search(sql_path.read_text())
    return m.group(1) if m else None


def check_schema_versions() -> None:
    """
    Report schema versions and flag drift. Reports only — never rewrites.

    Auto-syncing would be wrong here: a mismatch can mean either "the SQL was
    bumped and the DB was not rebuilt" or "a schema changed and nobody bumped
    the SQL". Those need opposite fixes, so this surfaces the mismatch and
    leaves the call to a human.
    """
    print("\nSchema versions (independent of package version):")

    seeded = {
        "aimfp_core": _seeded_schema_version(SCHEMA_SQL_DIR / "aimfp_core.sql"),
        "project": _seeded_schema_version(SCHEMA_SQL_DIR / "project.sql"),
        "user_preferences": _seeded_schema_version(SCHEMA_SQL_DIR / "user_preferences.sql"),
        "user_directives": _seeded_schema_version(SCHEMA_SQL_DIR / "user_directives.sql"),
    }
    for name, version in seeded.items():
        print(f"  {name}.sql seeds: {version or '(not found)'}")

    if not CORE_DB.exists():
        print("  WARNING: aimfp_core.db missing — run dev/sync-directives.py")
        return

    import sqlite3
    conn = sqlite3.connect(CORE_DB)
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        shipped_core = row[0] if row else None

        # What the shipped core DB tells users' DBs to migrate toward
        expected = dict(conn.execute(
            "SELECT db_name, expected_version FROM expected_schema_versions"
        ).fetchall())
    finally:
        conn.close()

    print(f"  aimfp_core.db (ships in wheel) is at: {shipped_core or '(unreadable)'}")

    issues = []
    if shipped_core != seeded["aimfp_core"]:
        issues.append(
            f"aimfp_core.sql seeds {seeded['aimfp_core']} but the shipped DB is at "
            f"{shipped_core} — rebuild with dev/sync-directives.py"
        )

    # expected_schema_versions drives migrate_databases for every user
    for db_name in ("project", "user_preferences", "user_directives"):
        if expected.get(db_name) != seeded[db_name]:
            issues.append(
                f"{db_name}.sql seeds {seeded[db_name]} but core says users should expect "
                f"{expected.get(db_name)} — users will be told to migrate to the wrong version"
            )

    if issues:
        print("\n  WARNING: schema version drift")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  All schema versions consistent.")


def extract_current(name: str, info: dict) -> str | None:
    text = info["path"].read_text()
    m = re.search(info["pattern"], text, re.MULTILINE)
    if not m:
        return None
    # Every pattern is structured as (prefix")<version>("): group 1 is the
    # prefix ending at the opening quote, group 2 is the closing quote, and
    # the version is whatever sits between them. (Searching for the first
    # "..." in the match instead would return the JSON `"version"` KEY, not
    # its value — that was the false "out of sync" bug.)
    full_match = m.group(0)
    prefix, suffix = m.group(1), m.group(2)
    return full_match[len(prefix):len(full_match) - len(suffix)]


def update_file(info: dict, new_version: str) -> bool:
    text = info["path"].read_text()
    new_text, count = re.subn(
        info["pattern"],
        info["replace"].format(version=new_version),
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count == 0:
        return False
    info["path"].write_text(new_text)
    return True


def run_build() -> int:
    # Clean
    for d in ["build", "dist"]:
        p = ROOT / d
        if p.exists():
            subprocess.run(["rm", "-rf", str(p)])
    for egg in glob.glob(str(ROOT / "src" / "*.egg-info")):
        subprocess.run(["rm", "-rf", egg])

    # Build (--no-isolation: pip.conf sets user=true which breaks isolated venvs)
    print("\n--- Building ---")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation"],
        cwd=str(ROOT),
    )
    return result.returncode


def main():
    print("AIMFP Version Bumper")
    print("=" * 40)

    # Show current versions
    print("\nCurrent versions:")
    versions_found = {}
    for name, info in VERSION_FILES.items():
        v = extract_current(name, info)
        versions_found[name] = v
        print(f"  {name}: {v or '(not found)'}")

    # Check sync
    unique = set(v for v in versions_found.values() if v)
    if len(unique) > 1:
        print(f"\n  WARNING: versions are out of sync!")
    elif len(unique) == 1:
        print(f"\n  All files at: {unique.pop()}")

    # Schema versions ride a separate cadence — report, never rewrite
    check_schema_versions()

    # Get new version
    print()
    new_version = input("New version (or 'q' to quit): ").strip()
    if new_version.lower() == 'q' or not new_version:
        print("Aborted.")
        return

    if not VERSION_RE.match(new_version):
        print(f"Invalid version format: '{new_version}' (expected X.Y.Z)")
        return

    # Confirm
    print(f"\nWill update all files to: {new_version}")
    confirm = input("Proceed? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        return

    # Update files
    print()
    for name, info in VERSION_FILES.items():
        ok = update_file(info, new_version)
        status = "updated" if ok else "FAILED (pattern not found)"
        print(f"  {name}: {status}")

    # Build
    print()
    build = input("Run build? [Y/n]: ").strip().lower()
    if build in ('', 'y', 'yes'):
        rc = run_build()
        if rc == 0:
            print("\nBuild succeeded.")
        else:
            print(f"\nBuild failed (exit code {rc}).")
    else:
        print("Skipped build.")

    print("\nDone.")


if __name__ == "__main__":
    main()
