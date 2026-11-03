"""Admin review queue. checked_at is a Korean calendar date, not a badge timestamp."""
from calendar import monthrange


def six_month_cutoff(day):
    month = (day.month - 7) % 12 + 1
    year = day.year - (day.month <= 6)
    return day.replace(year=year, month=month, day=min(day.day, monthrange(year, month)[1]))


# Historical places use an empty string for unknown dates. Normalize on read only.
CHECK_DATE = "NULLIF(p.checked_at, '')"
NEVER = f"{CHECK_DATE} IS NULL"
STALE = f"{CHECK_DATE} <= ?"


def freshness_counts(conn, active, cutoff):
    row = conn.execute(f"""SELECT count(*) FILTER (WHERE {STALE}) AS stale,
                              count(*) FILTER (WHERE {NEVER}) AS never
                       FROM places p WHERE {active}""", (cutoff,)).fetchone()
    return dict(row)
