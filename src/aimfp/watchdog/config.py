"""
AIMFP Watchdog - Configuration Constants

Exclusion patterns, function detection patterns, paths, and timing constants.
All values are immutable (Final + frozenset).
"""

import fnmatch
import os
import re
from typing import Final, Optional, Pattern

from ..database.connection import (
    AIMFP_PROJECT_DIR,
    get_project_db_path as _foundation_get_project_db_path,
    get_user_preferences_db_path as _foundation_get_preferences_db_path,
)


# ============================================================================
# Timing
# ============================================================================

DEBOUNCE_SECONDS: Final[float] = 2.0


# ============================================================================
# Directory / File Names (watchdog-specific)
# ============================================================================

WATCHDOG_DIR_NAME: Final[str] = "watchdog"
REMINDERS_FILE: Final[str] = "reminders.json"
PID_FILE: Final[str] = "watchdog.pid"

# Gitignore-style exclusion file. Lives at the PROJECT ROOT (not inside
# .aimfp-project/) so it is discoverable and editable like .gitignore.
WATCHDOGIGNORE_FILE: Final[str] = ".watchdogignore"


# ============================================================================
# Exclusion Patterns (Hardcoded Defaults)
# ============================================================================

EXCLUDED_DIRS: Final[frozenset[str]] = frozenset([
    'node_modules',
    'venv',
    '.venv',
    'env',
    '.env',
    '__pycache__',
    '.git',
    '.svn',
    '.hg',
    'target',
    'build',
    'dist',
    '.tox',
    '.mypy_cache',
    '.pytest_cache',
    'vendor',
    '.next',
    '.nuxt',
    'coverage',
    '.coverage',
    'htmlcov',
    '.aimfp-project',
])

EXCLUDED_EXTENSIONS: Final[frozenset[str]] = frozenset([
    '.pyc', '.pyo', '.so', '.dll', '.dylib',
    '.class', '.o', '.obj', '.exe',
    '.lock', '.log',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
    '.woff', '.woff2', '.ttf', '.eot',
    '.zip', '.tar', '.gz', '.bz2',
    '.tmp', '.swp', '.swo', '.bak',
])


# Default contents written to a project's .watchdogignore at init time.
# Gitignore-style: one pattern per line, '#' comments, blank lines ignored.
DEFAULT_WATCHDOGIGNORE_CONTENT: Final[str] = """\
# .watchdogignore — paths the AIMFP watchdog should NOT track.
#
# Gitignore-style syntax (note: negation '!' is NOT supported):
#   tests/                 a directory named 'tests' at any depth
#   packages/host/extension/  an exact nested subtree (relative to project root)
#   *_test.py              glob matched against the file name
#   test_*.py
#   *.test.ts
#
# Patterns containing '/' are matched against the project-relative path
# (and everything beneath them). Patterns without '/' match a file name
# or any single directory component anywhere in the tree.
#
# These supplement the built-in exclusions (node_modules, __pycache__,
# .git, build/dist, .aimfp-project, etc.) — you don't need to list those.
# Uncomment or add the lines that fit your project:

# tests/
# *_test.py
# test_*.py
# *.test.js
# *.test.ts
# *.spec.ts
"""


# ============================================================================
# Function Detection Patterns (per language)
# ============================================================================

FUNCTION_PATTERNS: Final[dict[str, str]] = {
    'python': r'^\s*def\s+(\w+)\s*\(',
    'javascript': r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>))',
    'typescript': r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*(?::\s*\w+)?\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>))',
    'rust': r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)',
    'go': r'func\s+(?:\([^)]+\)\s+)?(\w+)',
    'java': r'(?:public|private|protected)?\s*(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(',
}


# ============================================================================
# Reminder Type Constants
# ============================================================================

REMINDER_TIMESTAMP_SYNCED: Final[str] = "timestamp_synced"
REMINDER_NEW_FILE: Final[str] = "new_file_detected"
REMINDER_MISSING_FUNCTION: Final[str] = "missing_function"
REMINDER_MISSING_DB_FUNCTION: Final[str] = "missing_db_function"
REMINDER_FILE_DELETED: Final[str] = "file_deleted"

SEVERITY_INFO: Final[str] = "info"
SEVERITY_WARNING: Final[str] = "warning"


# ============================================================================
# Pure Functions
# ============================================================================

def get_watchdog_dir(project_root: str) -> str:
    """Pure: Get path to .aimfp-project/watchdog/ directory."""
    return os.path.join(project_root, AIMFP_PROJECT_DIR, WATCHDOG_DIR_NAME)


def get_reminders_path(project_root: str) -> str:
    """Pure: Get path to reminders.json."""
    return os.path.join(get_watchdog_dir(project_root), REMINDERS_FILE)


def get_pid_path(project_root: str) -> str:
    """Pure: Get path to watchdog.pid."""
    return os.path.join(get_watchdog_dir(project_root), PID_FILE)


def get_watchdogignore_path(project_root: str) -> str:
    """Pure: Get path to the project's .watchdogignore (at project root)."""
    return os.path.join(project_root, WATCHDOGIGNORE_FILE)


def get_project_db_path(project_root: str) -> str:
    """Pure: Get path to project.db (delegates to foundation layer)."""
    return _foundation_get_project_db_path(project_root)


def get_preferences_db_path(project_root: str) -> str:
    """Pure: Get path to user_preferences.db (delegates to foundation layer)."""
    return _foundation_get_preferences_db_path(project_root)


def build_exclusion_sets(
    user_excluded_dirs: tuple[str, ...] = (),
    user_excluded_extensions: tuple[str, ...] = (),
) -> tuple[frozenset[str], frozenset[str]]:
    """
    Pure: Merge hardcoded exclusions with user-provided ones.

    Returns:
        (excluded_dirs, excluded_extensions) as frozensets
    """
    merged_dirs = EXCLUDED_DIRS | frozenset(user_excluded_dirs)
    merged_exts = EXCLUDED_EXTENSIONS | frozenset(user_excluded_extensions)
    return (merged_dirs, merged_exts)


def parse_watchdogignore(content: str) -> tuple[str, ...]:
    """
    Pure: Parse .watchdogignore file contents into a tuple of glob patterns.

    Gitignore-lite: blank lines and '#' comments are dropped, backslashes are
    normalized to forward slashes, and a single trailing '/' (directory marker)
    is stripped. Negation ('!') is not supported — such lines are kept verbatim
    and simply won't match anything meaningful.
    """
    patterns: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        line = line.replace('\\', '/')
        if line.endswith('/'):
            line = line[:-1]
        if line:
            patterns.append(line)
    return tuple(patterns)


def matches_ignore_patterns(
    relative_path: str,
    ignore_patterns: tuple[str, ...],
) -> bool:
    """
    Pure: Check a project-relative path against .watchdogignore glob patterns.

    Matching rules (case-sensitive, deterministic across platforms):
    - A pattern containing '/' is anchored to the project root: it matches the
      path itself, anything beneath it (subtree), or via glob (e.g. 'src/*.gen.py').
    - A pattern without '/' floats: it matches the file's basename OR any single
      directory component anywhere in the path (e.g. 'tests' or '*_test.py').
    """
    if not ignore_patterns:
        return False

    norm = relative_path.replace('\\', '/').strip('/')
    if not norm:
        return False

    parts = norm.split('/')
    basename = parts[-1]

    for pattern in ignore_patterns:
        if '/' in pattern:
            if (
                norm == pattern
                or norm.startswith(pattern + '/')
                or fnmatch.fnmatchcase(norm, pattern)
                or fnmatch.fnmatchcase(norm, pattern + '/*')
            ):
                return True
        else:
            if fnmatch.fnmatchcase(basename, pattern):
                return True
            if any(fnmatch.fnmatchcase(part, pattern) for part in parts):
                return True

    return False


def get_function_pattern(language: str) -> Optional[Pattern[str]]:
    """
    Pure: Get compiled regex pattern for detecting functions in given language.

    Returns None if language not supported.
    """
    raw = FUNCTION_PATTERNS.get(language.lower())
    if raw is None:
        return None
    return re.compile(raw, re.MULTILINE)


def should_exclude(
    file_path: str,
    excluded_dirs: frozenset[str],
    excluded_extensions: frozenset[str],
    ignore_patterns: tuple[str, ...] = (),
) -> bool:
    """
    Pure: Determine if a file path should be excluded from watching.

    Checks directory components, file extension, ephemeral file patterns
    (tilde-suffix backups, dot-prefixed editor temps), and user-defined
    .watchdogignore glob patterns. For ignore_patterns to anchor correctly,
    callers should pass a project-relative path.
    """
    parts = file_path.replace('\\', '/').split('/')
    for part in parts:
        if part in excluded_dirs:
            return True

    basename = parts[-1] if parts else ''

    # Tilde-suffix backup files (e.g. file.py~) and dot-hash editor locks (e.g. .#file.py)
    if basename.endswith('~') or basename.startswith('.#'):
        return True

    # Atomic write temp files (e.g. file.ts.tmp.322162.1774839388035)
    if '.tmp.' in basename:
        return True

    _, ext = os.path.splitext(file_path)
    if ext.lower() in excluded_extensions:
        return True

    if matches_ignore_patterns(file_path, ignore_patterns):
        return True

    return False


def get_relative_path(file_path: str, source_dir: str) -> str:
    """Pure: Get path relative to source directory."""
    return os.path.relpath(file_path, source_dir)
