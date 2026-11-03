"""Per-key token quotas: daily and monthly, in the machine's local time zone (WIB here).

Totals live in memory per key for the current local day and month. They are seeded from
usage_logs the first time a key is checked in a period, then increased by every finished request
(Finalizer -> add). Single process by design. The quota is soft: requests already running may
go past the limit, since their token count is only known when they end.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .errors import APIError

if TYPE_CHECKING:
    from .auth import ApiKey
    from .db import Database


def local_now() -> datetime:
    return datetime.now().astimezone()


def iso_utc(dt: datetime) -> str:
    """Same format as db.utcnow(), so it compares correctly with usage_logs.ts."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_expiry(value: str) -> str:
    """'2026-12-31' (or '20261231') -> end of that local day; a datetime without zone is local time.

    Returns UTC ISO. Raises ValueError on anything else, including years outside 2000-2100 (the
    Windows time functions behind astimezone() fail on far dates).
    """
    try:
        try:
            d = date.fromisoformat(value)
        except ValueError:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not 2000 <= moment.year <= 2100:
                raise ValueError("year out of range") from None
            if moment.tzinfo is None:
                moment = moment.astimezone()
        else:
            if not 2000 <= d.year <= 2100:
                raise ValueError("year out of range")
            moment = datetime(d.year, d.month, d.day).astimezone() + timedelta(days=1)
        return iso_utc(moment)
    except (OverflowError, OSError) as exc:
        raise ValueError(str(exc)) from None


def local_periods(now: datetime) -> tuple[datetime, datetime, datetime, datetime]:
    """(day_start, next_day, month_start, next_month) in the local time zone of `now`."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    next_month = month.replace(year=month.year + 1, month=1) if month.month == 12 else month.replace(month=month.month + 1)
    return day, day + timedelta(days=1), month, next_month


@dataclass(frozen=True)
class QuotaStatus:
    day_used: int
    month_used: int
    day_limit: int | None
    month_limit: int | None
    now: datetime
    day_reset: datetime
    month_reset: datetime

    def check(self) -> None:
        """Raise 429 insufficient_quota when the daily or monthly quota is used up."""
        if self.day_limit is not None and self.day_used >= self.day_limit:
            raise quota_exceeded("daily", self.day_limit, self.day_reset, self.now)
        if self.month_limit is not None and self.month_used >= self.month_limit:
            raise quota_exceeded("monthly", self.month_limit, self.month_reset, self.now)

    def headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for period, used, limit in (("day", self.day_used, self.day_limit), ("month", self.month_used, self.month_limit)):
            if limit is not None:
                out[f"x-quota-tokens-{period}-limit"] = str(limit)
                out[f"x-quota-tokens-{period}-remaining"] = str(max(0, limit - used))
        return out


def quota_exceeded(period: str, limit: int, reset_at: datetime, now: datetime) -> APIError:
    seconds = max(1, int((reset_at - now).total_seconds()))
    return APIError(
        429, f"The {period} token quota of {limit} tokens for this API key is used up. "
             f"It resets at {reset_at.isoformat(timespec='minutes')}.",
        type_="insufficient_quota", code="insufficient_quota",
        # x-should-retry: the OpenAI SDKs would otherwise retry a 429 a few times for nothing.
        headers={"Retry-After": str(seconds), "x-should-retry": "false"},
    )


class QuotaTracker:
    def __init__(self, db: "Database", now: Callable[[], datetime] = local_now):
        self._db = db
        self._now = now
        # key_id -> [day_start_iso, month_start_iso, day_tokens, month_tokens]
        self._totals: dict[int, list[Any]] = {}

    async def status(self, key: "ApiKey") -> QuotaStatus:
        now = self._now()
        day, next_day, month, next_month = local_periods(now)
        day_iso, month_iso = iso_utc(day), iso_utc(month)
        entry = self._totals.get(key.id)
        if entry is None or entry[0] != day_iso or entry[1] != month_iso:
            day_used = await self._db.token_usage_since(key.id, day_iso)
            month_used = await self._db.token_usage_since(key.id, month_iso)
            entry = self._totals[key.id] = [day_iso, month_iso, day_used, month_used]
        return QuotaStatus(entry[2], entry[3], key.daily_token_limit, key.monthly_token_limit,
                           now, next_day, next_month)

    def add(self, key_id: int | None, tokens: int | None, ts: str) -> None:
        """Count a finished request (ts = its usage_logs timestamp) for keys already being tracked."""
        entry = self._totals.get(key_id) if key_id is not None else None
        if entry is None or not tokens:
            return
        if ts >= entry[1]:
            entry[3] += tokens
            if ts >= entry[0]:
                entry[2] += tokens
