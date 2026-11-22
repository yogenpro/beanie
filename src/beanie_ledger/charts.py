"""Coordinates for small, server-rendered trend charts."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Trend:
    points: list[tuple[Decimal, Decimal, str, Decimal]]
    high: Decimal
    low: Decimal


def trend(values):
    if not values:
        return None
    low = min(number for _, number in values)
    high = max(number for _, number in values)
    span = high - low
    dates = [date.fromisoformat(label if len(label) == 10 else f"{label}-01") for label, _ in values]
    days = max(1, (dates[-1] - dates[0]).days)
    points = [
        (
            round(Decimal(8) + Decimal(784) * (day - dates[0]).days / days, 2),
            round(Decimal(112) - (Decimal(104) * (number - low) / span if span else Decimal(52)), 2),
            label, number,
        )
        for day, (label, number) in zip(dates, values)
    ]
    return Trend(points, high, low)
