"""Parking fee calculation. Pure functions, easy to unit test."""
import math
from datetime import datetime


def calculate_fee(vehicle_type, entry, exit_, rates, lost_ticket=False, lost_penalty=0):
    """Return the fee for a stay.

    Rules:
      * Anything up to 60 minutes costs the first-hour rate.
      * Each additional started hour costs `per_hour`.
      * Each full 24h block is capped at `daily_cap`; the leftover part is
        charged normally but also capped at `daily_cap`.
      * A grace period of 10 minutes applies to stays under 10 minutes (free).
    """
    if vehicle_type not in rates:
        raise ValueError(f"Unknown vehicle type: {vehicle_type}")
    if exit_ < entry:
        raise ValueError("Exit time is before entry time")

    r = rates[vehicle_type]
    minutes = (exit_ - entry).total_seconds() / 60

    if minutes < 10 and not lost_ticket:
        return 0.0

    full_days, rem_minutes = divmod(minutes, 24 * 60)
    fee = int(full_days) * r["daily_cap"]

    if rem_minutes > 0:
        hours = math.ceil(rem_minutes / 60)
        part = r["first_hour"] + max(0, hours - 1) * r["per_hour"]
        fee += min(part, r["daily_cap"])

    if lost_ticket:
        fee += lost_penalty
    return float(fee)


def duration_text(entry: datetime, exit_: datetime) -> str:
    total = int((exit_ - entry).total_seconds() // 60)
    h, m = divmod(total, 60)
    return f"{h}h {m}m"
