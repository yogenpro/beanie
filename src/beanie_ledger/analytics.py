"""Build statement and chart data before the site is published."""

from collections import defaultdict
from functools import lru_cache

from beancount.core.inventory import Inventory
from beancount.ops import summarize

from .statements import closing_balances, ending_statements, income_statement, months_between
from .valuation import period_end, valued_positions


def rows(amounts):
    return [(account, currency, str(number)) for (account, currency), number in sorted(amounts.items())]


class StatementReports:
    def __init__(self, ledger):
        self.ledger = ledger
        self.months = list(months_between(min(ledger.monthly_positions), max(ledger.monthly_positions))) \
            if ledger.monthly_positions else []
        self.income_roots = {ledger.income, ledger.expenses}
        self.assets_and_liabilities = set(ledger.balance_roots[:2])
        self.monthly_flows = {}
        self.net_worth = {}

    @lru_cache(maxsize=2)
    def snapshot(self, as_of):
        ledger = self.ledger
        if as_of == ledger.as_of:
            return ledger.accounts, ledger.balances, closing_balances(ledger), as_of
        entries = [entry for entry in ledger.entries if entry.date <= as_of]
        positions, _ = summarize.balance_by_account(entries)
        accounts = {account: opened for account, opened in ledger.accounts.items() if opened <= as_of}
        return accounts, positions, closing_balances(ledger, entries), as_of

    def value(self, positions, mode, period):
        return valued_positions(positions, mode, self.ledger.price_map, period_end(period, self.ledger.as_of))

    def income(self, period, mode):
        months = [month for month in self.months if period == "all" or month.startswith(period)]
        if mode not in self.monthly_flows:
            self.monthly_flows[mode] = {
                month: self.value({
                    account: positions
                    for account, positions in self.ledger.monthly_positions.get(month, {}).items()
                    if account.split(":")[0] in self.income_roots
                }, mode, month)
                for month in self.months
            }
        monthly = self.monthly_flows[mode]
        positions = defaultdict(Inventory)
        for month in months:
            for account, lots in self.ledger.monthly_positions.get(month, {}).items():
                if account.split(":")[0] in self.income_roots:
                    positions[account].add_inventory(lots)
        amounts = self.value(positions, mode, period)
        return income_statement(self.ledger, period, amounts), amounts, [(month, monthly[month]) for month in months]

    def ending(self, period, mode, report):
        snapshot = self.snapshot(period_end(period, self.ledger.as_of))
        balance, trial = ending_statements(self.ledger, mode, snapshot)
        _, positions, closed, _ = snapshot
        if report == "trial":
            amounts = self.value(positions, mode, period)
            return [trial], amounts, []
        if mode not in self.net_worth:
            positions = defaultdict(Inventory)
            history = []
            for month in self.months:
                for account, lots in self.ledger.monthly_positions.get(month, {}).items():
                    if account.split(":")[0] in self.assets_and_liabilities:
                        positions[account].add_inventory(lots)
                history.append((month, self.value(positions, mode, month)))
            self.net_worth[mode] = history
        timeline = [(month, values) for month, values in self.net_worth[mode]
                    if period == "all" or month.startswith(period)]
        return [balance[:1], balance[1:]], self.value(closed, mode, period), timeline
