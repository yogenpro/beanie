from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit

from beanie_ledger.model import BuildError, Ledger
from beanie_ledger.site import build_site, slug


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
            {"EUR": ("0", "3.75"), "USD": ("3600.00", "1580.73")},
        )
        positions = ledger.positions("Assets:Investments")
        self.assertEqual(str(positions[0].cost.number), "40.00")
        value, currency, price_date = ledger.valuation(positions[0])
        self.assertEqual((str(value), currency, price_date.isoformat()), ("228.3750", "USD", "2025-11-17"))
        self.assertIsNone(ledger.valuation(ledger.positions("Assets:Travel-Cash")[0]))

    def test_render_all_pages_and_references(self):
        settings = self.root / "site.toml"
        settings.write_text("page_size = 10\nquery_csv = true\n", encoding="utf-8")
        build_site(EXAMPLE, self.site, settings)
        account = self.site / "accounts" / slug("Assets:Checking")
        self.assertIn("Register", (account / "index.html").read_text())
        empty_account = self.site / "accounts" / slug("Assets:Reserves") / "index.html"
        self.assertIn("No transactions on this account.", empty_account.read_text())
        self.assertEqual(len(list(self.site.glob("reports/*/results.csv"))), 2)
        for html in self.site.rglob("*.html"):
            parser = References()
            parser.feed(html.read_text())
            for url in parser.urls:
                parsed = urlsplit(url)
                with self.subTest(html=html.relative_to(self.site), url=url):
                    self.assertFalse(parsed.scheme or parsed.netloc or parsed.path.startswith("/"))
                    if parsed.path:
                        self.assertTrue((html.parent / parsed.path).is_file())

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
