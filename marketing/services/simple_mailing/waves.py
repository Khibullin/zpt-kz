from __future__ import annotations

from datetime import datetime, timedelta


def compute_wave_schedule(
    *,
    total_count: int,
    t0: datetime,
    wave_size: int,
    interval_minutes: int,
) -> list[tuple[int, int, datetime]]:
    """Return list of (position_number, wave_number, scheduled_at)."""
    if total_count <= 0:
        return []
    rows: list[tuple[int, int, datetime]] = []
    interval = timedelta(minutes=interval_minutes)
    for position in range(1, total_count + 1):
        wave_number = ((position - 1) // wave_size) + 1
        scheduled_at = t0 + interval * (wave_number - 1)
        rows.append((position, wave_number, scheduled_at))
    return rows
