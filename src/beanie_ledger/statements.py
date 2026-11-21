"""Hierarchical statements based on booked amounts at cost."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from beancount.core import convert
from beancount.ops import summarize


@dataclass(frozen=True)
class StatementRow:
    label: str
    account: str | None
    depth: int
    amounts: dict[str, Decimal]


@dataclass
class StatementNode:
    row: StatementRow
    children: list["StatementNode"]


@dataclass(frozen=True)
class StatementSection:
    name: str
    rows: list[StatementRow]

    @property
    def tree(self):
        roots = []
        parents = []
        for row in self.rows:
            while parents and parents[-1].row.depth >= row.depth:
                parents.pop()
            node = StatementNode(row, [])
            siblings = parents[-1].children if parents else roots
            siblings.append(node)
            parents.append(node)
        return roots


def rollup(accounts, amounts, roots):
    """Build each account subtree with signed, currency-specific subtotals."""
    subtotals = {}
    for account in accounts:
        segments = account.split(":")
        if segments[0] not in roots:
            continue
        for size in range(1, len(segments) + 1):
            subtotals.setdefault(":".join(segments[:size]), defaultdict(Decimal))

    for (account, currency), amount in amounts.items():
        segments = account.split(":")
        if segments[0] in roots:
            for size in range(1, len(segments) + 1):
                subtotals[":".join(segments[:size])][currency] += amount

    grouped = {root: [] for root in roots}
    for account, balances in sorted(subtotals.items(), key=lambda item: item[0].split(":")):
        segments = account.split(":")
        grouped[segments[0]].append(StatementRow(
            segments[-1], account if account in accounts else None,
            len(segments) - 1, {currency: amount for currency, amount in balances.items() if amount},
        ))
    return [StatementSection(root, grouped[root]) for root in roots]


def ending_statements(ledger):
    amounts = defaultdict(Decimal)
    for account in ledger.accounts:
        for position in ledger.positions(account):
            value = convert.get_cost(position)
            amounts[account, value.currency] += value.number
    trial = rollup(ledger.accounts, amounts, (*ledger.balance_roots, ledger.income, ledger.expenses))

    # Use Beancount's report-time closing. The original directives and journal
    # stay intact. In particular, closing accounts never become source postings.
    capped = summarize.cap_opt(ledger.entries, ledger.options) if ledger.transactions else ledger.entries
    balances, _ = summarize.balance_by_account(capped)
    sheet_amounts = {
        (account, position.units.currency): position.units.number
        for account, positions in balances.items()
        for position in positions.reduce(convert.get_cost)
    }
    sheet = rollup(ledger.accounts.keys() | balances.keys(), sheet_amounts, ledger.balance_roots)
    return sheet, trial


def income_statement(ledger, period):
    amounts = ledger.cost_flows.get(period, {})
    accounts = {account for account, _currency in amounts}
    sections = rollup(accounts, amounts, (ledger.income, ledger.expenses))
    total = defaultdict(Decimal)
    for (_account, currency), amount in amounts.items():
        total[currency] += amount
    net = StatementSection("Net Profit", [StatementRow("Net Profit", None, 0, dict(total))])
    return [sections[0], net], [sections[1]]


@dataclass(frozen=True)
class MonthlyBar:
    month: str
    income: Decimal
    expenses: Decimal
    income_height: Decimal
    expense_height: Decimal
    has_report: bool


@dataclass(frozen=True)
class MonthlyActivity:
    currency: str
    ticks: tuple[Decimal, ...]
    bars: list[MonthlyBar]
    years: list[tuple[str, int]]


def months_between(first, last):
    def index(month):
        return int(month[:4]) * 12 + int(month[5:]) - 1

    for number in range(index(first), index(last) + 1):
        year, month = divmod(number, 12)
        yield f"{year:04d}-{month + 1:02d}"


def monthly_activity(ledger, period="all"):
    report_months = {
        month for month in ledger.periods
        if len(month) == 7 and (period == "all" or month == period or month.startswith(period + "-"))
    }
    if not report_months:
        return None
    candidates = {
        currency for month in report_months for _, currency in ledger.cost_flows.get(month, {})
    }
    if not candidates:
        return None
    currency = next((code for code in ledger.currencies if code in candidates), min(candidates))
    totals = []
    for month in months_between(min(report_months), max(report_months)):
        income = Decimal(0)
        expenses = Decimal(0)
        for (account, code), amount in ledger.cost_flows.get(month, {}).items():
            if code != currency:
                continue
            if account.split(":")[0] == ledger.income:
                income -= amount
            else:
                expenses += amount
        totals.append((month, income, expenses))

    # Income is drawn as a credit below zero. Returns or reversals can put
    # both series on the same side, so the scale must fit their combined stack.
    maximum = max(max(max(expenses, 0) + max(-income, 0), max(-expenses, 0) + max(income, 0))
                  for _, income, expenses in totals)
    if not maximum:
        return None

    half = maximum / 2
    unit = Decimal(1).scaleb(half.adjusted())
    step = next(unit * multiplier for multiplier in (1, 2, Decimal("2.5"), 5, 10)
                if unit * multiplier >= half)
    limit = step * 2

    def height(number):
        return round(100 * abs(number) / limit, 2)

    bars = [MonthlyBar(month, income, expenses, height(income), height(expenses), month in report_months)
            for month, income, expenses in totals]
    years = list(Counter(bar.month[:4] for bar in bars).items())
    return MonthlyActivity(currency, (limit, step, Decimal(0), -step, -limit), bars, years)
