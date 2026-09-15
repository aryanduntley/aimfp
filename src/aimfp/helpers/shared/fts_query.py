"""
AIMFP Helper Functions - FTS5 Query Building

Pure utilities that turn free text typed by an AI into safe SQLite FTS5 MATCH
expressions and equivalent per-term LIKE clauses.

Why this exists: passing raw search text straight to ``MATCH ?`` fails in two ways.
FTS5 treats bare space-separated words as an implicit AND, so a single word that
does not appear in a row ("disk space free check") drops every result. And FTS5
query syntax rejects ordinary punctuation ("disk-space", "what's"), raising an
OperationalError that callers caught and answered with ``LIKE '%whole phrase%'`` —
which almost never matches, so the failure looked like "no results".

The builders here tokenize the text into word terms, quote each term (so no user
input is ever parsed as FTS syntax), and OR-join them as prefix queries. FTS5's
bm25 ranking already scores rows matching more terms higher, so OR-joining widens
recall without losing precision at the top of the list.

All functions are pure.
"""

import re
from typing import Final, Sequence, Tuple

# Word runs of letters/digits. Underscore is excluded so snake_case identifiers
# split the same way the FTS5 unicode61 tokenizer splits them.
_TERM_PATTERN: Final = re.compile(r"[^\W_]+", re.UNICODE)

# Guards against pathological inputs producing huge MATCH expressions.
_MAX_TERMS: Final[int] = 16


def tokenize_search_terms(search_string: str, max_terms: int = _MAX_TERMS) -> Tuple[str, ...]:
    """
    Pure: Split free text into distinct lowercase search terms.

    Uppercase OR/AND/NOT typed by the AI are dropped as connectors rather than
    searched for, since every term is already OR-joined. Single-character
    fragments (the "s" of "what's") are dropped because as prefix terms they
    match nearly every row — unless the input has nothing longer.

    Args:
        search_string: Raw search text (may contain punctuation or FTS operators)
        max_terms: Upper bound on returned terms

    Returns:
        Tuple of unique terms in first-seen order (empty if no word characters)
    """
    raw = _TERM_PATTERN.findall(search_string or "")
    words = tuple(t.lower() for t in raw if t not in ("OR", "AND", "NOT"))
    meaningful = tuple(t for t in words if len(t) > 1) or words
    return tuple(dict.fromkeys(meaningful))[:max_terms]


def build_fts_match_expression(terms: Sequence[str]) -> str:
    """
    Pure: Build an FTS5 MATCH expression that OR-joins quoted prefix terms.

    Args:
        terms: Search terms, as returned by tokenize_search_terms

    Returns:
        Expression like '"disk"* OR "space"*' (empty string for no terms)
    """
    return " OR ".join('"{}"*'.format(t.replace('"', '""')) for t in terms)


def build_like_clause(
    columns: Sequence[str],
    terms: Sequence[str],
) -> Tuple[str, Tuple[str, ...]]:
    """
    Pure: Build a per-term LIKE clause for databases without an FTS5 table.

    A row matches when any column contains any term — the LIKE equivalent of the
    OR-joined MATCH expression.

    Args:
        columns: Fully qualified column names to search (e.g. ('f.name', 'f.purpose'))
        terms: Search terms, as returned by tokenize_search_terms

    Returns:
        (sql_fragment, params) — fragment is parenthesized; '0' when no terms
    """
    if not terms or not columns:
        return "(0)", ()
    per_term = " OR ".join(f"{col} LIKE ?" for col in columns)
    sql = "(" + " OR ".join(f"({per_term})" for _ in terms) + ")"
    params = tuple(f"%{t}%" for t in terms for _ in columns)
    return sql, params
