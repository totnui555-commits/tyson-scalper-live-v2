from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv


def _parse_iso_utc(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_manual_events(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    events = []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        rows = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    if not rows:
        return events
    reader = csv.DictReader(rows)
    if not reader.fieldnames or "event_time_utc" not in reader.fieldnames:
        raise ValueError("news CSV must contain event_time_utc,event_name")
    for row in reader:
        if not row.get("event_time_utc", "").strip():
            continue
        events.append((_parse_iso_utc(row["event_time_utc"]), row.get("event_name", "event").strip()))
    return events


def check_news_blackout(cfg: dict, now_utc: datetime | None = None):
    ncfg = cfg["filters"]["news_filter"]
    if not ncfg.get("enabled", False):
        return False, "news_filter_disabled"

    now_utc = now_utc or datetime.now(timezone.utc)
    provider = ncfg.get("provider", "manual_csv")
    if provider != "manual_csv":
        if ncfg.get("fail_closed_if_unavailable", True):
            return True, f"unsupported_news_provider:{provider}"
        return False, f"unsupported_news_provider_bypassed:{provider}"

    try:
        events = load_manual_events(ncfg["csv_path"])
    except Exception as e:
        if ncfg.get("fail_closed_if_unavailable", True):
            return True, f"news_calendar_unavailable:{e}"
        return False, f"news_calendar_unavailable_bypassed:{e}"

    before = timedelta(minutes=float(ncfg.get("minutes_before", 20)))
    after = timedelta(minutes=float(ncfg.get("minutes_after", 20)))
    for event_time, name in events:
        if event_time - before <= now_utc <= event_time + after:
            return True, f"news_blackout:{name}@{event_time.isoformat()}"
    return False, "news_clear"
