"""When a schedule runs: five-field cron, or a few plain forms that become cron.

    */15 * * * *        cron: minute hour day-of-month month day-of-week (0 = Sunday)
    @hourly @daily @weekly
    every 30m           every N minutes (N divides 60) or hours (N divides 24)
    every 2h
    daily 09:00
    weekdays 18:30      Monday to Friday

Times are the server's local time.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

PRESETS = {"@hourly": "0 * * * *", "@daily": "0 9 * * *", "@weekly": "0 9 * * 1"}
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_NAMES = ("minute", "hour", "day of month", "month", "day of week")


class WhenError(ValueError):
    pass


def to_cron(spec: str) -> str:
    """The cron expression for ``spec`` (validated)."""
    text = " ".join((spec or "").strip().lower().split())
    if text in PRESETS:
        text = PRESETS[text]
    elif match := re.fullmatch(r"every (\d+) ?(m|min|mins|minutes?|h|hours?)", text):
        n, unit = int(match.group(1)), match.group(2)[0]
        if unit == "m":
            if n < 1 or 60 % n:
                raise WhenError("`every N minutes` needs N that divides 60 (5, 10, 15, 20, 30…).")
            text = "* * * * *" if n == 1 else f"*/{n} * * * *"
        else:
            if n < 1 or 24 % n:
                raise WhenError("`every N hours` needs N that divides 24 (1, 2, 3, 4, 6, 8, 12).")
            text = "0 * * * *" if n == 1 else f"0 */{n} * * *"
    elif match := re.fullmatch(r"(daily|weekdays) (\d{1,2}):(\d{2})", text):
        hour, minute = int(match.group(2)), int(match.group(3))
        if hour > 23 or minute > 59:
            raise WhenError(f"{match.group(2)}:{match.group(3)} is not a time of day.")
        text = f"{minute} {hour} * * " + ("1-5" if match.group(1) == "weekdays" else "*")
    fields(text)
    return text


def _field(part: str, low: int, high: int, name: str) -> set[int]:
    values: set[int] = set()
    for piece in part.split(","):
        match = re.fullmatch(r"(\*|\d+)(?:-(\d+))?(?:/(\d+))?", piece)
        if not match:
            raise WhenError(f"Cannot read {piece!r} in the {name} field.")
        start_text, end_text, step_text = match.groups()
        if start_text == "*":
            start, end = low, high
        else:
            start = int(start_text)
            end = int(end_text) if end_text else (high if step_text else start)
        step = int(step_text or 1)
        if step < 1 or start < low or end > high or start > end:
            raise WhenError(f"{piece!r} is outside {low}-{high} in the {name} field.")
        values.update(range(start, end + 1, step))
    return values


def fields(expression: str) -> list[set[int]]:
    parts = expression.split()
    if len(parts) != 5:
        raise WhenError("Use five cron fields (minute hour day month weekday), or e.g. `every 2h`, `daily 09:00`, `weekdays 18:30`.")
    parsed = [_field(part, low, high, name) for part, (low, high), name in zip(parts, _RANGES, _NAMES)]
    if parts[4] != "*" and 7 in {int(x) for x in re.findall(r"\d+", parts[4])}:
        raise WhenError("Day of week runs 0-6 (0 is Sunday).")
    return parsed


def next_after(expression: str, after: datetime) -> datetime:
    """The first minute strictly after ``after`` that the expression matches."""
    minutes, hours, days, months, weekdays = fields(expression)
    parts = expression.split()
    day_restricted, weekday_restricted = parts[2] != "*", parts[4] != "*"
    moment = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        weekday = (moment.weekday() + 1) % 7  # cron: 0 = Sunday
        if day_restricted and weekday_restricted:
            day_ok = moment.day in days or weekday in weekdays  # cron's either-or
        else:
            day_ok = moment.day in days and weekday in weekdays
        if moment.month in months and day_ok and moment.hour in hours and moment.minute in minutes:
            return moment
        if moment.month not in months or not day_ok:
            moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
        elif moment.hour not in hours:
            moment = (moment + timedelta(hours=1)).replace(minute=0)
        else:
            moment += timedelta(minutes=1)
    raise WhenError(f"{expression!r} never runs.")
