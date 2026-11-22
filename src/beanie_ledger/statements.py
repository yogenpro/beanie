"""Hierarchical statements based on booked amounts at cost."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from beancount.ops import summarize

from .valuation import valued_positions


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
            segments[-1], account,
            len(segments) - 1, {currency: amount for currency, amount in balances.items() if amount},
        ))
    return [StatementSection(root, grouped[root]) for root in roots]


def ending_statements(ledger, mode="cost", snapshot=None):
    accounts, positions, balances, as_of = snapshot or (
        ledger.accounts, ledger.balances, closing_balances(ledger), ledger.as_of,
    )
    amounts = valued_positions(positions, mode, ledger.price_map, as_of)
    trial = rollup(accounts, amounts, (*ledger.balance_roots, ledger.income, ledger.expenses))

    # Use Beancount's report-time closing. The original directives and journal
    # stay intact. In particular, closing accounts never become source postings.
    sheet_amounts = valued_positions(balances, mode, ledger.price_map, as_of)
    sheet = rollup(accounts.keys() | balances.keys(), sheet_amounts, ledger.balance_roots)
    return sheet, trial


def closing_balances(ledger, entries=None):
    if entries is None:
        entries = ledger.entries
    capped = summarize.cap_opt(entries, ledger.options) if entries else entries
    balances, _ = summarize.balance_by_account(capped)
    return balances


def income_statement(ledger, period, amounts=None):
    if amounts is None:
        amounts = ledger.cost_flows.get(period, {})
    accounts = {account for account, _currency in amounts}
    sections = rollup(accounts, amounts, (ledger.income, ledger.expenses))
    total = defaultdict(Decimal)
    for (_account, currency), amount in amounts.items():
        total[currency] -= amount
    net = StatementSection("Net Profit", [StatementRow("Net Profit", None, 0, dict(total))])
    return [sections[0], net], [sections[1]]


def months_between(first, last):
    def index(month):
        return int(month[:4]) * 12 + int(month[5:]) - 1

    for number in range(index(first), index(last) + 1):
        year, month = divmod(number, 12)
        yield f"{year:04d}-{month + 1:02d}"
