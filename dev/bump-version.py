#!/usr/bin/env python3
"""
Bump AIMFP version across all version files, then build.

Files updated:
  - pyproject.toml          (version = "X.Y.Z")
  - src/aimfp/__init__.py    (__version__ = "X.Y.Z")
  - src/aimfp/mcp_server/server.py  (SERVER_VERSION: Final[str] = "X.Y.Z")
  - manifest.json           ("version": "X.Y.Z")

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
    "manifest.json": {
        "path": ROOT / "manifest.json",
        "pattern": r'^(\s*"version"\s*:\s*")[^"]+(")',
        "replace": r'\g<1>{version}\2',
    },
    ".claude-plugin/plugin.json": {
        "path": ROOT / ".claude-plugin" / "plugin.json",
        "pattern": r'^(\s*"version"\s*:\s*")[^"]+(")',
        "replace": r'\g<1>{version}\2',
    },
}

VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')


def extract_current(name: str, info: dict) -> str | None:
    text = info["path"].read_text()
    m = re.search(info["pattern"], text, re.MULTILINE)
    if m:
        full_match = m.group(0)
        # Extract just the version string between quotes
        v = re.search(r'"([^"]+)"', full_match)
        return v.group(1) if v else None
    return None


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
