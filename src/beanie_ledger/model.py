"""Read Beancount directives once and prepare report data."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from beancount import loader
from beancount.core import convert, data, inventory, prices
import beanquery


class BuildError(Exception):
    """A ledger or configuration problem that prevents publication."""


@dataclass(frozen=True)
class CashFlow:
    currency: str
    income: Decimal = Decimal(0)
    expenses: Decimal = Decimal(0)

    @property
    def net(self):
        return self.income - self.expenses


@dataclass(frozen=True)
class FlowReport:
    totals: list[CashFlow]
    categories: list[tuple[str, str, Decimal, str]]
    payees: list[tuple[str, str, Decimal]]


@dataclass(frozen=True)
class QueryReport:
    name: str
    columns: tuple[str, ...]
    rows: list[tuple]


class Ledger:
    def __init__(self, filename):
        self.entries, errors, self.options = loader.load_file(str(filename))
        if errors:
            diagnostics = "\n".join(
                f"{error.source.get('filename')}:{error.source.get('lineno')}: {error.message}"
                for error in errors[:10]
            )
            raise BuildError(f"Beancount found {len(errors)} error(s).\n{diagnostics}")
        self.accounts = {}
        self.closed = {}
        self.transactions = []
        self.registers = defaultdict(list)
        self.balances = defaultdict(inventory.Inventory)
        self.queries = {}
        self.expenses = self.options["name_expenses"]
        self.income = self.options["name_income"]
        self.asset_roots = {self.options["name_assets"], self.options["name_liabilities"]}
        self.balance_roots = tuple(self.options[f"name_{kind}"] for kind in (
            "assets", "liabilities", "equity",
        ))
        self.currencies = tuple(dict.fromkeys(self.options["operating_currency"]))
        self.flows = defaultdict(lambda: defaultdict(Decimal))
        self.cost_flows = defaultdict(lambda: defaultdict(Decimal))
        self.payees = defaultdict(lambda: defaultdict(Decimal))

        for entry in self.entries:
            if isinstance(entry, data.Open):
                self.accounts[entry.account] = entry.date
            elif isinstance(entry, data.Close):
                self.closed[entry.account] = entry.date
            elif isinstance(entry, data.Transaction):
                self.transactions.append(entry)
                self._record(entry)
            elif isinstance(entry, data.Query):
                self.queries[entry.name] = entry.query_string

        self.latest = self.transactions[-1].date if self.transactions else None
        self.as_of = max((entry.date for entry in self.entries), default=None)
        self.price_map = prices.build_price_map(self.entries)
        self.accounts = dict(sorted(self.accounts.items()))
        self.periods = sorted(
            {str(entry.date.year) for entry in self.transactions}
            | {entry.date.isoformat()[:7] for entry in self.transactions},
            reverse=True,
        )

    def _record(self, entry):
        changes = defaultdict(lambda: defaultdict(Decimal))
        periods = ("all", str(entry.date.year), entry.date.isoformat()[:7])
        for posting in entry.postings:
            self.balances[posting.account].add_position(posting)
            units = posting.units
            changes[posting.account][units.currency] += units.number
            root = posting.account.split(":")[0]
            if root in (self.expenses, self.income):
                amount = units.number if root == self.expenses else -units.number
                cost = convert.get_cost(posting)
                for period in periods:
                    self.flows[period][posting.account, units.currency] += amount
                    self.cost_flows[period][posting.account, cost.currency] += cost.number
                    if root == self.expenses:
                        payee = entry.payee or "(No payee)"
                        self.payees[period][payee, units.currency] += amount
        for account, units in changes.items():
            self.registers[account].append((entry, sorted(units.items())))

    def flow_report(self, period):
        income = defaultdict(Decimal)
        expenses = defaultdict(Decimal)
        categories = []
        for (account, currency), amount in sorted(self.flows.get(period, {}).items()):
            if account.split(":")[0] == self.income:
                income[currency] += amount
                kind = "Income"
            else:
                expenses[currency] += amount
                kind = "Spending"
            categories.append((account, currency, amount, kind))
        totals = [
            CashFlow(currency, income[currency], expenses[currency])
            for currency in sorted(income.keys() | expenses.keys())
        ]
        payees = sorted(
            ((payee, currency, amount)
             for (payee, currency), amount in self.payees.get(period, {}).items()),
            key=lambda row: (row[1], -row[2], row[0].casefold(), row[0]),
        )
        return FlowReport(totals, categories, payees)

    def register(self, account):
        return reversed(self.registers.get(account, ()))

    def positions(self, account):
        return sorted(self.balances[account], key=str)

    def valuation(self, position):
        """Use the first declared operating currency and dated direct prices."""
        if not self.currencies:
            return None
        units = position.units
        currency = self.currencies[0]
        if units.currency == currency:
            return units.number, currency, None
        price_date, price = prices.get_price(self.price_map, (units.currency, currency), self.as_of)
        if price is None:
            return None
        return units.number * price, currency, price_date

    def query_reports(self, selected):
        names = list(self.queries) if selected is None else selected
        connection = beanquery.connect("beancount:", entries=self.entries, options=self.options, errors=[])
        for name in names:
            if name not in self.queries:
                raise BuildError(f"Unknown saved query: {name}")
            try:
                cursor = connection.execute(self.queries[name])
                yield QueryReport(name, tuple(col.name for col in cursor.description), cursor.fetchall())
            except Exception as exc:
                raise BuildError(f"Saved query {name!r} failed: {exc}") from exc
