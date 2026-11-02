"""User-timezone resolution and local-day helpers.

Day boundaries follow a user-configurable IANA timezone (``users.timezone``),
defaulting to the *server's* timezone. Storage stays naive-UTC everywhere
(``health_metrics.recorded_at`` convention, see ``_as_naive_utc`` in
``app/routers/health.py``); this module converts at the edges:

- ``local_day()`` is registered as a SQLite scalar function (see
  ``app/database.py``) so grouped SQL can bucket rows by local calendar day.
- ``local_day_bounds_utc()`` maps a local calendar day to the naive-UTC
  half-open instant range it covers, DST-safe via ``zoneinfo``.

Special case: ``granularity='daily'`` rows are *civil-date markers* (the
calendar date stamped at UTC midnight — Google rollups and compaction both
write them that way). Their day label is ``date(recorded_at)`` directly; do NOT
run them through ``local_day()`` or the label shifts back a day for any user
west of UTC. In SQL use::

    CASE WHEN granularity = 'daily' THEN date(recorded_at)
         ELSE local_day(recorded_at, :tzi) END
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_UTC_OFFSET_RE = re.compile(r"^UTC([+-])(\d{2}):(\d{2})$")
_tz_cache: dict[str, tzinfo] = {"UTC": timezone.utc}


def is_valid_timezone(name: str | None) -> bool:
    """True when name is an IANA zone (or "UTC") the app can resolve."""
    if not name or not isinstance(name, str):
        return False
    if name == "UTC":
        return True
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def tzinfo_for(name: str | None) -> tzinfo:
    """Resolve a timezone name to a tzinfo. Unknown names fall back to UTC."""
    if not name:
        return timezone.utc
    cached = _tz_cache.get(name)
    if cached is not None:
        return cached
    result: tzinfo
    m = _UTC_OFFSET_RE.match(name)
    if name == "UTC":
        result = timezone.utc
    elif m:
        sign = 1 if m.group(1) == "+" else -1
        result = timezone(sign * timedelta(hours=int(m.group(2)),
                                           minutes=int(m.group(3))))
    else:
        try:
            result = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            logger.warning(f"Unknown timezone {name!r}; falling back to UTC")
            result = timezone.utc
    _tz_cache[name] = result
    return result


def server_timezone_name() -> str:
    """IANA name of the server's timezone.

    Resolution order: ``USER_TIMEZONE``/``TZ`` env override -> ``tzlocal`` ->
    ``/etc/localtime`` link (Linux) -> fixed offset of the server clock. The
    last two are returned in ``UTC±HH:MM`` form (accepted by ``tzinfo_for``)
    when no IANA name can be determined (mainly Windows without tzlocal).
    """
    for key in ("USER_TIMEZONE", "TZ"):
        v = (os.environ.get(key) or "").strip()
        if v and is_valid_timezone(v):
            return v
    try:
        from tzlocal import get_localzone_name
        name = get_localzone_name()
        if name and is_valid_timezone(name):
            return name
    except Exception:
        pass
    try:
        target = os.readlink("/etc/localtime")
        # Typical target: /usr/share/zoneinfo/America/New_York
        name = target.split("zoneinfo/", 1)[-1] if "zoneinfo/" in target else target
        if name and is_valid_timezone(name):
            return name
    except OSError:
        pass
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    total = int(offset.total_seconds()) // 60
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 60:02d}:{total % 60:02d}"


def resolve_timezone(user_timezone: str | None) -> str:
    """Effective timezone name for a user: their override, else the server's."""
    if user_timezone and is_valid_timezone(user_timezone):
        return user_timezone
    return server_timezone_name()


async def get_user_timezone(db, user_id: int) -> str:
    """Effective timezone name for a user id (one small query)."""
    from sqlalchemy import select

    from ..models.user import User

    row = (await db.execute(
        select(User.timezone).where(User.id == user_id)
    )).scalar_one_or_none()
    return resolve_timezone(row)


def local_day(ts, tz_name: str | None) -> str | None:
    """Local calendar day ('YYYY-MM-DD') of a naive-UTC timestamp string.

    This is the SQLite scalar function registered as ``local_day``. Stored
    timestamps are naive UTC; tz-aware values are converted via UTC first.
    """
    if ts is None:
        return None
    s = str(ts)
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T", 1))
    except ValueError:
        return s[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo_for(tz_name)).date().isoformat()


def local_day_bounds_utc(day: str, tz_name: str | None) -> tuple[datetime, datetime]:
    """Naive-UTC half-open instant range [start, end) of a local calendar day.

    DST-safe: the local midnight boundaries are built in the target zone and
    converted to UTC, so a 23/25-hour transition day spans the right instants.
    """
    y, m, d = (int(p) for p in day.split("-"))
    tz = tzinfo_for(tz_name)
    start_local = datetime(y, m, d, tzinfo=tz)
    end_local = (datetime(y, m, d) + timedelta(days=1)).replace(tzinfo=tz)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


def utc_to_local_day(ts: datetime, tz_name: str | None) -> str:
    """Local calendar day of a (naive-UTC or aware) datetime."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(tzinfo_for(tz_name)).date().isoformat()


def local_days_spanned(
    start: datetime, end: datetime, tz_name: str | None
) -> list[tuple[str, datetime, datetime]]:
    """Local days touched by [start, end), each with its naive-UTC bounds.

    A point-like span (end <= start) yields the single day containing start.
    """
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    if end.tzinfo is not None:
        end = end.astimezone(timezone.utc).replace(tzinfo=None)

    out: list[tuple[str, datetime, datetime]] = []
    day = utc_to_local_day(start, tz_name)
    while True:
        bounds = local_day_bounds_utc(day, tz_name)
        out.append((day, *bounds))
        if end <= bounds[1]:
            break
        y, m, d = (int(p) for p in day.split("-"))
        day = (datetime(y, m, d) + timedelta(days=1)).date().isoformat()
    return out


def now_local_day(tz_name: str | None) -> str:
    """Today's calendar day in the given timezone (UTC clock)."""
    return utc_to_local_day(datetime.now(timezone.utc), tz_name)
