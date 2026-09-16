"""
AIMFP Hooks - Schedule Arithmetic

When a time trigger fires next. The grammar lives one module over in
triggers.py and deliberately never touches a clock; this is the module that
does, which is why they are separate files rather than one.

WHY AIMFP OWNS THE ARITHMETIC. Before this, AIMFP owned no time semantics at
all: next_scheduled_time was entirely runner-declared and is_due only
compared two strings. That left every UC2 project to hand-roll "what does
17:00 America/New_York mean tomorrow", and two projects that answer that
differently have genuinely disagreed about when the same directive runs.

NAIVE LOCAL TIME IS A CONSTRAINT, NOT A PREFERENCE. _now_iso() produces naive
local time and is_due compares against it. A tz-aware return value would make
`scheduled <= current` raise TypeError against a naive now - uncaught, inside
the loop a runner drives every tick. So occurrences are computed as wall
clock IN the target zone, which is what a user means by "17:00 in New York",
and converted back to naive local before they are handed out or stored.

WHICH MEANS THE RESULT IS NOT THE SCHEDULE THE USER WROTE. "17:00 in New
York" stored on a machine in Los Angeles is 14:00. Nothing is lost - the
declared zone stays in trigger_config and is always recoverable - but the
column is a comparison value, not a rendering. NEVER SHOW next_scheduled_time
OR next_fire_time TO A USER AS THEIR SCHEDULE; render from trigger_config,
which trigger_config_grammar() publishes as data for exactly that. Showing
the converted time tells a user their 17:00 schedule is set for 14:00, and
the obvious response is to "correct" a config that was already right.

THE SEARCH IS A DAY-BY-DAY WALK, capped at 400 days. Calendar arithmetic
invites off-by-one errors around month ends and DST; walking candidate dates
and asking "does this one match" is obviously correct instead, and 400
iterations of that costs nothing. The cap turns an unsatisfiable schedule
into a reported error rather than a hang.

Stdlib only, by package contract - datetime and zoneinfo, no croniter, no
dateutil.
"""

from datetime import datetime, timedelta
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .results import NextFireResult
from .triggers import WEEKDAY_TOKENS, validate_trigger_config

# A weekly schedule needs at most 7 days, a monthly one at most 62 (day 31
# skips the months that lack it). 400 is generous enough that hitting it
# means the schedule is genuinely unsatisfiable, not merely sparse.
_MAX_SEARCH_DAYS = 400


# ============================================================================
# Pure Helpers
# ============================================================================

def _parse_anchor(after: Optional[str]) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Pure: Parse the anchor timestamp, dropping any timezone it carries.

    An aware anchor is normalised to local wall clock rather than rejected:
    a runner that previously wrote an aware timestamp through
    set_next_scheduled_time should still get a usable answer.
    """
    if after is None:
        return None, None
    try:
        parsed = datetime.fromisoformat(after)
    except (TypeError, ValueError):
        return None, f'after: is not an ISO timestamp ({after!r})'
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed, None


def _resolve_zone(name: Optional[str]) -> Tuple[Optional[ZoneInfo], Optional[str]]:
    """
    Effect: Resolve an IANA name against this machine's tz database.

    This is where the resolution deferred by validate_trigger_config
    happens - see the note there on why a pure validator must not do it. A
    missing tz database surfaces here, at the moment the zone actually
    matters, instead of making a config invalid on one machine and valid on
    another.
    """
    if name is None:
        return None, None
    try:
        return ZoneInfo(name), None
    except (ZoneInfoNotFoundError, ValueError) as exc:
        return None, (f"timezone: {name!r} could not be resolved on this "
                      f"machine ({exc}); the name is well-formed, so this is "
                      f"a missing or incomplete tz database, not a bad config")


def _day_matches(candidate: datetime, kind: str, config: dict) -> bool:
    """
    Pure: Decide whether one candidate date satisfies the schedule's day rule.

    A monthly day the month does not have simply fails to match, so that
    month is skipped. That follows cron, and it is the honest reading: "the
    31st" means the 31st, where clamping to the 28th would fire on a date
    nobody asked for.
    """
    if kind == 'daily':
        return True
    if kind == 'weekly':
        return WEEKDAY_TOKENS[candidate.weekday()] in config['weekdays']
    if kind == 'monthly':
        return candidate.day in config['days']
    return False


def _walk_to_next_occurrence(
    anchor: datetime,
    kind: str,
    config: dict,
    zone: Optional[ZoneInfo],
) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Compute the first matching occurrence strictly after the anchor.

    Both sides of the comparison are expressed in the same zone, so a
    schedule stays at its wall-clock time across a DST boundary: 17:00 stays
    17:00, and the instant it represents moves by an hour, which is what
    "every day at 17:00" means.

    A local time that a DST transition skips is resolved by the platform's
    fold rules rather than rejected - the alternative is a directive that
    silently misses one day a year.
    """
    hours, _, minutes = config['at'].partition(':')
    hour, minute = int(hours), int(minutes)

    local_anchor = anchor if zone is None else anchor.astimezone(zone)

    for offset in range(_MAX_SEARCH_DAYS):
        date = (local_anchor + timedelta(days=offset)).date()
        candidate = datetime(
            date.year, date.month, date.day, hour, minute, tzinfo=zone)
        if not _day_matches(candidate, kind, config):
            continue
        if candidate > local_anchor:
            return candidate, None

    return None, (f"no occurrence of this schedule falls within "
                  f"{_MAX_SEARCH_DAYS} days of the anchor")


def _to_naive_local(value: datetime) -> datetime:
    """Pure: Express an occurrence as naive local time, as is_due expects."""
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


# ============================================================================
# Public Hooks - Schedule Arithmetic
# ============================================================================

def next_fire_time(
    trigger_config: Any,
    after: Optional[str] = None,
) -> NextFireResult:
    """
    HOOK (library API, not an MCP tool).

    Compute the first moment a time trigger fires strictly after an anchor.

    Deterministic given `after`; only the default anchor reads the clock, so
    schedules are testable without moving the system time.

    Strictly after, never equal: an occurrence exactly at the anchor would
    make a directive that just ran immediately due again.

    The config is validated first, so a malformed schedule fails here with
    the validator's own field-level errors instead of producing a plausible
    but wrong timestamp.

    Args:
        trigger_config: A time trigger's config, as a mapping or JSON text
        after: ISO anchor to schedule from, defaulting to now

    Returns:
        NextFireResult. next_fire_time is naive local ISO, matching _now_iso
        and is_due - so it is a comparison value, not the wall clock the
        config declared, and is not what to display to a user (see the module
        docstring). An unresolvable timezone or an unsatisfiable schedule is
        success=False with an explanation, never an exception.
    """
    validation = validate_trigger_config('time', trigger_config)
    if not validation.valid:
        return NextFireResult(
            success=False,
            kind=validation.kind,
            error='; '.join(validation.errors),
        )

    config = validation.config
    kind = validation.kind

    anchor, anchor_error = _parse_anchor(after)
    if anchor_error is not None:
        return NextFireResult(success=False, kind=kind, error=anchor_error)
    if anchor is None:
        anchor = datetime.now().replace(microsecond=0)

    if kind == 'interval':
        fires_at = anchor + timedelta(seconds=config['seconds'])
        return NextFireResult(
            success=True,
            next_fire_time=fires_at.isoformat(timespec='seconds'),
            kind=kind,
        )

    zone, zone_error = _resolve_zone(config.get('timezone'))
    if zone_error is not None:
        return NextFireResult(success=False, kind=kind, error=zone_error)

    occurrence, walk_error = _walk_to_next_occurrence(anchor, kind, config, zone)
    if occurrence is None:
        return NextFireResult(success=False, kind=kind, error=walk_error)

    return NextFireResult(
        success=True,
        next_fire_time=_to_naive_local(occurrence).isoformat(timespec='seconds'),
        kind=kind,
    )
