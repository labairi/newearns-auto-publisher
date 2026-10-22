"""Time-zone aware scheduling.

A scheduled time is stored the way a person thinks about it: a local wall-clock time plus an IANA
zone name ("2026-10-25T10:30", "Africa/Casablanca"). The UTC instant is always derived from those two
at the moment it is needed, so daylight-saving rules in the zone database are applied correctly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimeError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError as e:
        raise TimeError(f"not an ISO time: {s!r}") from e
    if d.tzinfo is None:
        raise TimeError(f"time has no zone: {s!r}")
    return d.astimezone(timezone.utc)


def to_utc(local: str, tz: str) -> datetime:
    """Local wall time + IANA zone -> UTC instant.

    A wall time that does not exist (spring-forward gap) or exists twice (autumn overlap) is rejected
    instead of guessed, so a post never goes out an hour off.
    """
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as e:
        raise TimeError(f"unknown time zone: {tz!r}") from e
    try:
        naive = datetime.fromisoformat(local)
    except ValueError as e:
        raise TimeError(f"not a local time: {local!r} (use YYYY-MM-DDTHH:MM)") from e
    if naive.tzinfo is not None:
        raise TimeError("give the local time without an offset; the zone is a separate field")
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        # Either a gap (no such time) or an overlap (twice). Tell them apart.
        back = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if back != naive:
            raise TimeError(f"{local} does not exist in {tz} (clocks skip it); choose another time")
        raise TimeError(f"{local} happens twice in {tz} (clocks go back); choose another time")
    return first.astimezone(timezone.utc)


def add_minutes(dt: datetime, minutes: int) -> datetime:
    return dt + timedelta(minutes=minutes)
