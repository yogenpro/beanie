"""Read-only valuations using historical quotes from the ledger."""

from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal

from beancount.core import convert


def valuation_modes(currencies):
    return {
        "cost": "At cost",
        "market": "At market value",
        "units": "Units",
        **{f"to-{currency}": f"Converted to {currency}" for currency in currencies},
    }


def period_end(period, as_of):
    if period == "all":
        return as_of
    if len(period) == 4:
        end = date(int(period), 12, 31)
    else:
        year, month = (int(part) for part in period.split("-"))
        end = date(year, month, monthrange(year, month)[1])
    return min(end, as_of) if as_of else end


def position_amount(position, mode, price_map, as_of):
    if mode == "cost":
        return convert.get_cost(position)
    if mode == "units":
        return position.units
    if mode == "market":
        value = convert.get_value(position, price_map, as_of)
        return convert.get_cost(position) if value is position.units else value
    return convert.convert_position(position, mode.removeprefix("to-"), price_map, as_of)


def valued_positions(positions, mode, price_map, as_of):
    amounts = defaultdict(Decimal)
    for account, lots in positions.items():
        for position in lots:
            amount = position_amount(position, mode, price_map, as_of)
            amounts[account, amount.currency] += amount.number
    return {key: number for key, number in amounts.items() if number}
