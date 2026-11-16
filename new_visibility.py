"""One calendar-month policy shared by public NEW and admin queries."""
from community_migrations import today


def new_clause(now=None):
    now = now or today()  # Service date in Asia/Seoul, not the client's clock.
    month = now.year * 12 + now.month
    return ("c.slug IN ('cafe','food') AND c.is_active=1 AND p.new_override!='hide' AND "
            "((p.opened_year IS NOT NULL AND p.opened_month BETWEEN 1 AND 12 "
            "AND p.opened_year*12+p.opened_month BETWEEN ? AND ?) "
            "OR (p.new_override='show' AND (p.opened_year IS NULL OR p.opened_month IS NULL)))",
            [month - 6, month])
