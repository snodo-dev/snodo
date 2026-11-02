"""Human-readable duration and timestamp formatting for plan run output."""

import time
from typing import Optional


def _format_duration(seconds: float) -> str:
    """Format duration in seconds as a human-readable string with s, m, h buckets."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        minutes = int(seconds // 60)
        rem_s = seconds % 60
        return f"{minutes}m {rem_s:.1f}s"
    hours = int(seconds // 3600)
    rem_minutes = int((seconds % 3600) // 60)
    rem_s = seconds % 60
    return f"{hours}h {rem_minutes}m {rem_s:.1f}s"


def _format_timestamp(ts: Optional[float]) -> str:
    """Format a timestamp as YYYY-MM-DD HH:MM:SS."""
    if not ts:
        return "N/A"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "N/A"
