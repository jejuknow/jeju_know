"""ISSUE category compatibility, calendar windows and public discovery rules."""
from calendar import monthrange
from datetime import date, timedelta
from urllib.parse import urlencode

from community_migrations import today

# Preserve the stored labels and accept both separators in old links and records.
LABELS = {"events": "행사•축제", "exhibitions": "전시•팝업", "activities": "체험•교육",
          "local": "지역소식", "opportunities": "공모•지원"}
PUBLIC_CATEGORIES = {key: label for key, label in LABELS.items() if key != "opportunities"}
ALIASES = {label.replace("•", "·"): label for label in LABELS.values() if "•" in label}
ALIASES.update({"전시": LABELS["exhibitions"], "지원·공모": LABELS["opportunities"],
                "지원사업": LABELS["opportunities"]})
PERIODS = {"all": "전체", "today": "오늘", "week": "이번 주", "weekend": "이번 주말", "month": "이번 달"}


def stored_names(label):
    return [label, *(old for old, current in ALIASES.items() if current == label)]


def period_bounds(period, day):
    monday = day - timedelta(days=day.weekday())
    if period == "today":
        return day, day
    if period == "week":
        return monday, monday + timedelta(days=6)
    if period == "weekend":
        return monday + timedelta(days=5), monday + timedelta(days=6)
    if period == "month":
        return day.replace(day=1), day.replace(day=monthrange(day.year, day.month)[1])
    return None


VALID_DATES = ("date(event_start,'+0 days')=event_start AND "
               "date(event_end,'+0 days')=event_end AND event_end>=event_start")


def public_where(day):
    names = [name for label in PUBLIC_CATEGORIES.values() for name in stored_names(label)]
    placeholders = ",".join("?" for _ in names)
    return (f"published=1 AND deleted_at IS NULL AND {VALID_DATES} AND event_end>=? "
            f"AND category IN ({placeholders})", [day.isoformat(), *names])


def schedule(item, day):
    try:
        start = date.fromisoformat(item["event_start"])
        end = date.fromisoformat(item["event_end"])
        if end < start:
            raise ValueError
    except (ValueError, TypeError):
        return {"event_status": "일정 미등록", "date_badge": "일정 미등록", "date_label": "일정 미등록"}
    fmt = "%Y.%m.%d" if start.year != end.year or start.year != day.year else "%m.%d"
    label = start.strftime(fmt)
    if start != end:
        label += " - " + end.strftime(fmt)
    if day < start:
        status, badge = "예정", f"D-{(start-day).days}"
    elif day > end:
        status = badge = "종료"
    else:
        status = "진행중"
        badge = "오늘까지" if end == day else "오늘 시작" if start == day else status
    return {"event_status": status, "date_badge": badge, "date_label": label}


def browse(conn, query, day):
    period = query.get("period", "all")
    period = period if period in PERIODS else "all"
    category = query.get("category", "").strip()
    category = PUBLIC_CATEGORIES.get(category, ALIASES.get(category, category))
    region, keyword = query.get("region", "").strip()[:100], query.get("q", "").strip()[:200]
    clause, params = public_where(day)
    where = [clause]
    if category:
        names = stored_names(category)
        where.append("category IN (" + ",".join("?" for _ in names) + ")")
        params.extend(names)
    if region:
        where.append("region=?")
        params.append(region)
    if keyword:
        where.append("instr(title||' '||summary||' '||venue||' '||address,?)>0")
        params.append(keyword)
    if bounds := period_bounds(period, day):
        where.append("event_start<=? AND event_end>=?")
        params.extend([bounds[1].isoformat(), bounds[0].isoformat()])
    rows = conn.execute("SELECT * FROM news_posts WHERE " + " AND ".join(where) +
                        " ORDER BY is_featured DESC,(event_start>?) ASC,event_start ASC,id DESC",
                        [*params, day.isoformat()]).fetchall()
    selected = {"period": period, "category": category, "region": region, "q": keyword,
                "view": "map" if query.get("view") == "map" else "list"}

    def link(**changes):
        values = {**selected, **changes}
        cleaned = {k: v for k, v in values.items() if v and not (k == "period" and v == "all")
                   and not (k == "view" and v == "list")}
        return "/news" + ("?" + urlencode(cleaned) if cleaned else "")
    return rows, selected, link
