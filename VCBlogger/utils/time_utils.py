"""Time utilities for VCBlogger."""
import time
from datetime import datetime, timezone, timedelta
from typing import Tuple


def now_ts() -> int:
    """Current UTC unix timestamp in seconds."""
    return int(time.time())


def get_day_boundaries(reference_ts: int | None = None) -> Tuple[int, int]:
    """Get start and end timestamp for the UTC day containing reference_ts."""
    dt = datetime.fromtimestamp(reference_ts or now_ts(), tz=timezone.utc)
    start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def get_week_boundaries(reference_ts: int | None = None) -> Tuple[int, int]:
    """Get start and end timestamp for the current week (Monday start) in UTC."""
    dt = datetime.fromtimestamp(reference_ts or now_ts(), tz=timezone.utc)
    start_dt = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=7)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def get_month_boundaries(reference_ts: int | None = None) -> Tuple[int, int]:
    """Get start and end timestamp for the current UTC month."""
    dt = datetime.fromtimestamp(reference_ts or now_ts(), tz=timezone.utc)
    start_dt = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Next month
    if start_dt.month == 12:
        next_month = start_dt.replace(year=start_dt.year + 1, month=1)
    else:
        next_month = start_dt.replace(month=start_dt.month + 1)
    return int(start_dt.timestamp()), int(next_month.timestamp())
