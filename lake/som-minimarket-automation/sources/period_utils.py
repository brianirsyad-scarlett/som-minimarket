"""
Shared 10-day "period" math for the B2B daily-store reports, per the rule:

    Period 1 = day 1-10
    Period 2 = day 11-20
    Period 3 = day 21-end of month

Each day's run walks backward from *today's* period through the two periods
before it (crossing a month boundary if needed), always fetching the most
recent period first. Only the most recent (current) period is meant to be
pushed to GCS; older periods are archive-only (OneDrive).
"""

import calendar
from datetime import date


def period_index(day: int) -> int:
    if day <= 10:
        return 1
    if day <= 20:
        return 2
    return 3


def period_bounds(year: int, month: int, idx: int):
    if idx == 1:
        start_day, end_day = 1, 10
    elif idx == 2:
        start_day, end_day = 11, 20
    else:
        start_day, end_day = 21, calendar.monthrange(year, month)[1]
    return date(year, month, start_day), date(year, month, end_day)


def prev_period(year: int, month: int, idx: int):
    if idx > 1:
        return year, month, idx - 1
    if month == 1:
        return year - 1, 12, 3
    return year, month - 1, 3


def rolling_periods(today: date, n: int = 3):
    """
    Most-recent-first list of n periods. The first one is today's current
    period, clipped to `today` (in-progress); the rest are fully-completed
    past periods (full period end date).
    """
    y, m, idx = today.year, today.month, period_index(today.day)
    periods = []
    for i in range(n):
        start, end = period_bounds(y, m, idx)
        actual_end = min(end, today) if i == 0 else end
        periods.append({
            "year": y, "month": m, "idx": idx,
            "start": start, "end": actual_end,
            "is_latest": i == 0,
        })
        y, m, idx = prev_period(y, m, idx)
    return periods


def all_periods_since(start_date: date, today: date):
    """Most-recent-first list of every period from start_date through today (backfill)."""
    y, m, idx = today.year, today.month, period_index(today.day)
    periods = []
    while True:
        start, end = period_bounds(y, m, idx)
        actual_end = min(end, today)
        periods.append({
            "year": y, "month": m, "idx": idx,
            "start": start, "end": actual_end,
            "is_latest": len(periods) == 0,
        })
        if start <= start_date:
            break
        y, m, idx = prev_period(y, m, idx)
    return periods


def branch_months(today: date):
    """Two by-branch ranges: current month-to-date, then the full previous month."""
    cur_start = today.replace(day=1)
    cur = {"start": cur_start, "end": today, "label": f"{today.year:04d}{today.month:02d}"}

    if today.month == 1:
        py, pm = today.year - 1, 12
    else:
        py, pm = today.year, today.month - 1
    p_start = date(py, pm, 1)
    p_end = date(py, pm, calendar.monthrange(py, pm)[1])
    prev = {"start": p_start, "end": p_end, "label": f"{py:04d}{pm:02d}"}
    return [cur, prev]


if __name__ == "__main__":
    t = date.today()
    print("today:", t)
    print("rolling_periods(3):")
    for p in rolling_periods(t, 3):
        print(" ", p)
    print("branch_months:")
    for m in branch_months(t):
        print(" ", m)
