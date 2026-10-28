"""Mobile controls serialized into the existing hours/closed-days text fields."""
import re
from business_hours import business_hours_range

WEEKDAYS = tuple("월화수목금토일")
TIME_OPTIONS = tuple(f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30))
CONTROL_FIELDS = {"hours_present", "hours_same", "hours_start", "hours_end", "hours_unknown",
                  "closed_present", "closed_mode", "closed_weekdays"}


def normalize_controls(form):
    # MultiDict remains intact, including photo uploads and multi-select values.
    from starlette.datastructures import FormData
    overrides = {}
    if form.get("hours_present") == "1":
        if form.get("hours_unknown") == "1":
            overrides["business_hours"] = ""
        elif form.get("hours_same") == "1":
            start, end = (str(form.get(k, "")) for k in ("hours_start", "hours_end"))
            overrides["business_hours"] = business_hours_range(start, end)
    if form.get("closed_present") == "1":
        mode = form.get("closed_mode", "unknown")
        if mode == "unknown":
            overrides["closed_days"] = ""
        elif mode == "everyday":
            overrides["closed_days"] = "연중무휴"
        elif mode == "days":
            days = form.getlist("closed_weekdays")
            if not days or any(d not in WEEKDAYS for d in days):
                raise ValueError("휴무 요일을 선택하거나 휴무일 미확인을 선택해주세요.")
            overrides["closed_days"] = " · ".join(d + "요일" for d in WEEKDAYS if d in days)
        elif mode != "custom":
            raise ValueError("휴무일 선택값을 확인해주세요.")
    return FormData([(k,v) for k,v in form.multi_items() if k not in overrides] + list(overrides.items()))


def controls(data):
    hours, closed = data.get("business_hours") or "", data.get("closed_days") or ""
    match = re.fullmatch(r"([0-2][0-9]:[0-5][0-9])\s*~\s*([0-2][0-9]:[0-5][0-9])", hours)
    days = [d for d in WEEKDAYS if d + "요일" in closed]
    known_closed = " · ".join(d + "요일" for d in days)
    result = {"hours_same": "1" if not hours or match else "", "hours_unknown": "1" if not hours else "",
              "hours_start": match[1] if match else "", "hours_end": match[2] if match else "",
              "closed_mode": "unknown" if not closed else "everyday" if closed == "연중무휴" else "days" if closed == known_closed else "custom",
              "closed_weekdays": days}
    for key in CONTROL_FIELDS:
        if key in data:
            result[key] = data[key]
    return result
