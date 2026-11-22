from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from urllib.parse import urlsplit

from beanie_ledger.analytics import StatementReports
from beanie_ledger.model import BuildError, Ledger
from beanie_ledger.site import build_site, slug
from beanie_ledger.statements import ending_statements, income_statement, rollup


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "main.beancount"


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        self.urls.extend(value for name, value in attrs if name in ("href", "src"))


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.site = self.root / "site"

    def test_refunds_transfers_and_currencies(self):
        ledger = Ledger(EXAMPLE)
        report = ledger.flow_report("2025-11")
        self.assertEqual(
            {row.currency: (str(row.income), str(row.expenses)) for row in report.totals},
            {"EUR": ("0", "3.75"), "USD": ("3600.00", "1879.66")},
        )
        positions = ledger.positions("Assets:Investments")
        self.assertEqual(str(positions[0].cost.number), "40.00")
        value, currency, price_date = ledger.valuation(positions[0])
        self.assertEqual((str(value), currency, price_date.isoformat()), ("228.3750", "USD", "2025-11-17"))
        self.assertIsNone(ledger.valuation(ledger.positions("Assets:Travel-Cash")[0]))

    def test_example_has_three_full_years_of_income_and_expenses(self):
        ledger = Ledger(EXAMPLE)
        months = [f"{year}-{month:02d}" for year in range(2023, 2026) for month in range(1, 13)]
        self.assertEqual(sorted(period for period in ledger.periods if len(period) == 7), months)
        for month in months:
            with self.subTest(month=month):
                usd = next(row for row in ledger.flow_report(month).totals if row.currency == "USD")
                self.assertGreater(usd.income, 0)
                self.assertGreater(usd.expenses, 0)

    def test_chart_uses_the_selected_period(self):
        ledger = Ledger(EXAMPLE)
        reports = StatementReports(ledger)
        for period, first, last, count in (
            ("all", "2023-01", "2025-12", 36),
            ("2023", "2023-01", "2023-12", 12),
            ("2025", "2025-01", "2025-12", 12),
            ("2024-06", "2024-06", "2024-06", 1),
        ):
            with self.subTest(period=period):
                _, amounts, timeline = reports.income(period, "cost")
                self.assertEqual(len(timeline), count)
                self.assertEqual((timeline[0][0], timeline[-1][0]), (first, last))
                for key, amount in amounts.items():
                    self.assertEqual(amount, sum(month.get(key, 0) for _, month in timeline))

    def test_market_units_and_conversion_use_dated_quotes(self):
        reports = StatementReports(Ledger(EXAMPLE))
        account = "Assets:Investments"
        for mode, currency, amount in (
            ("cost", "USD", "210.00"),
            ("market", "USD", "228.3750"),
            ("units", "INDEX", "5.25"),
            ("to-USD", "USD", "228.3750"),
        ):
            with self.subTest(mode=mode):
                _, values, _ = reports.ending("2025-11", mode, "trial")
                self.assertEqual(values[account, currency], Decimal(amount))
        _, older, _ = reports.ending("2025-10", "market", "trial")
        self.assertNotIn((account, "USD"), older)
        _, converted, _ = reports.ending("all", "to-USD", "trial")
        self.assertIn(("Assets:Travel-Cash", "EUR"), converted)

    def test_render_all_pages_and_references(self):
        settings = self.root / "site.toml"
        settings.write_text("page_size = 10\nquery_csv = true\n", encoding="utf-8")
        build_site(EXAMPLE, self.site, settings)
        landing = (self.site / "index.html").read_text()
        self.assertIn("Income Statement", landing)
        self.assertIn("Stacked bars", landing)
        self.assertIn("At market value", landing)
        self.assertIn("Converted to USD", landing)
        self.assertIn("Treemap", landing)
        for report in ("balance-sheet", "balance-sheet/2025", "trial-balance", "journal", "commodities",
                       "statistics", "holdings/by-account", "holdings/by-currency", "documents", "events",
                       "options"):
            self.assertTrue((self.site / report / "index.html").is_file())
        account = self.site / "accounts" / slug("Assets:Checking")
        self.assertIn("Account Journal", (account / "index.html").read_text())
        self.assertTrue((account / "balances/index.html").is_file())
        self.assertTrue((account / "changes/index.html").is_file())
        empty_account = self.site / "accounts" / slug("Assets:Reserves") / "index.html"
        self.assertIn("No transactions on this account.", empty_account.read_text())
        self.assertEqual(len(list(self.site.glob("reports/*/results.csv"))), 2)
        for payload_path in (self.site / "assets").glob("statement-*.json"):
            payload = json.loads(payload_path.read_text())
            self.assertIn("accounts", payload)
            if payload["report"] == "income":
                self.assertTrue(all(month[:4].isdigit() for month, _, _ in payload["timeline"]))
        for html in self.site.rglob("*.html"):
            parser = References()
            parser.feed(html.read_text())
            for url in parser.urls:
                parsed = urlsplit(url)
                with self.subTest(html=html.relative_to(self.site), url=url):
                    self.assertFalse(parsed.scheme or parsed.netloc or parsed.path.startswith("/"))
                    if parsed.path:
                        self.assertTrue((html.parent / parsed.path).is_file())

    def test_statement_rollups_and_report_only_closing(self):
        ledger = Ledger(EXAMPLE)
        (income, net), (expenses,) = income_statement(ledger, "2025-11")
        self.assertEqual(str(income.rows[0].amounts["USD"]), "-3600.00")
        self.assertEqual(str(expenses.rows[0].amounts["EUR"]), "3.75")
        utilities = next(row for row in expenses.rows if row.label == "Utilities")
        self.assertEqual(str(utilities.amounts["USD"]), "134.45")
        self.assertEqual(str(net.rows[0].amounts["USD"]), "1720.34")

        before = tuple(ledger.entries)
        sheet, trial = ending_statements(ledger)
        self.assertEqual(tuple(ledger.entries), before)
        self.assertEqual([section.name for section in trial],
                         ["Assets", "Liabilities", "Equity", "Income", "Expenses"])
        for currency in ("USD", "EUR"):
            self.assertEqual(sum(section.rows[0].amounts.get(currency, 0) for section in sheet), 0)
        self.assertTrue(any("Earnings" in row.label for row in sheet[-1].rows))

    def test_statement_children_stay_under_their_own_parent(self):
        amounts = {
            ("Expenses:Food:Groceries", "USD"): Decimal("14.00"),
            ("Expenses:Food-Fast", "USD"): Decimal("9.00"),
        }
        section, = rollup({account for account, _ in amounts}, amounts, ("Expenses",))
        expenses, = section.tree
        food, fast = expenses.children
        self.assertEqual(food.row.label, "Food")
        self.assertEqual([node.row.label for node in food.children], ["Groceries"])
        self.assertEqual(food.row.amounts["USD"], Decimal("14.00"))
        self.assertEqual(fast.row.label, "Food-Fast")
        self.assertEqual(fast.children, [])

    def test_failed_build_retains_previous_output(self):
        build_site(EXAMPLE, self.site)
        index = self.site / "index.html"
        before = index.read_bytes()
        settings = self.root / "other.toml"
        settings.write_text('queries = ["Unrecognized"]', encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "Unknown saved query"):
            build_site(EXAMPLE, self.site, settings)
        self.assertEqual(index.read_bytes(), before)

    def test_rejects_output_that_contains_ledger(self):
        source = self.root / "input.beancount"
        source.write_text('option "title" "Example"\n', encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "contains a ledger source"):
            build_site(source, self.root)

    def test_escaping_pagination_and_csv(self):
        ledger = self.root / "input.beancount"
        rows = ['2025-01-01 open Assets:Cash USD', '2025-01-01 open Expenses:Meals USD']
        for index in range(20):
            rows.extend([
                '2025-01-02 * "=HYPERLINK(1)" "<img src=x onerror=alert(1)>"',
                '  Expenses:Meals  1 USD',
                '  Assets:Cash    -1 USD',
            ])
        rows.append('2025-01-01 query "Payees" "SELECT payee"')
        ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")
        settings = self.root / "site.toml"
        settings.write_text("page_size = 10\nquery_csv = true\n", encoding="utf-8")
        build_site(ledger, self.site, settings)
        account = self.site / "accounts" / slug("Assets:Cash")
        self.assertIn('href="page/2/index.html"', (account / "index.html").read_text())
        second = (account / "page/2/index.html").read_text()
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", second)
        self.assertNotIn("<img src=x", second)
        results = next(self.site.glob("reports/*/results.csv")).read_text()
        self.assertIn("'=HYPERLINK(1)", results)
