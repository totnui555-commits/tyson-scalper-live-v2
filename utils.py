from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import yaml


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def in_session(cfg: dict, now_utc: datetime | None = None) -> bool:
    f = cfg["filters"]
    tz = ZoneInfo(f.get("session_timezone", "UTC"))
    now_utc = now_utc or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(tz).time()
    for start_s, end_s in f["session_windows"]:
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
        start = time(sh, sm)
        end = time(eh, em)
        if start <= end:
            if start <= local_now <= end:
                return True
        else:
            if local_now >= start or local_now <= end:
                return True
    return False


def local_day_bounds_utc(tz_name: str, now_utc: datetime | None = None):
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = ZoneInfo(tz_name)
    local = now_utc.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc), now_utc, start_local.date().isoformat()


def make_signal_id(symbol: str, bar_time: str, side: str) -> str:
    raw = f"{symbol}|{bar_time}|{side}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def file_exists(path: str) -> bool:
    return Path(path).exists()
