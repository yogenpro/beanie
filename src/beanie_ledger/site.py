"""Build and publish an ordinary directory of static files."""

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from itertools import islice
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import csv
import json
import os
import posixpath
import re
import tomllib

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from beancount.core import data

from .model import BuildError, Ledger
from .statements import ending_statements, income_statement, monthly_activity


@dataclass(frozen=True)
class Settings:
    title: str | None = None
    page_size: int = 50
    queries: list[str] | None = None
    query_csv: bool = False

    @classmethod
    def load(cls, path):
        if path is None:
            return cls()
        try:
            with path.open("rb") as file:
                settings = cls(**tomllib.load(file))
        except (tomllib.TOMLDecodeError, TypeError) as exc:
            raise BuildError(f"Invalid site configuration: {exc}") from exc
        if settings.title is not None and (
                not isinstance(settings.title, str) or not settings.title.strip()):
            raise BuildError("title must be a nonempty string.")
        if type(settings.page_size) is not int or not 10 <= settings.page_size <= 200:
            raise BuildError("page_size must be an integer from 10 to 200.")
        if settings.queries is not None and (
                not isinstance(settings.queries, list)
                or not all(isinstance(name, str) for name in settings.queries)
                or len(set(settings.queries)) != len(settings.queries)):
            raise BuildError("queries must be a list of distinct saved query names.")
        if type(settings.query_csv) is not bool:
            raise BuildError("query_csv must be a boolean.")
        return settings


@dataclass(frozen=True)
class BuildStats:
    files: int
    bytes: int


def slug(value):
    stem = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:52].rstrip("-")
    return f"{stem or 'item'}-{sha256(value.encode()).hexdigest()[:12]}"


def money(value):
    text = format(value, ",f")
    if "." in text:
        whole, fraction = text.split(".")
        return whole + "." + fraction.rstrip("0").ljust(2, "0")
    return text + ".00"


def axis_number(value):
    text = format(value, ",f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def batches(items, size):
    iterator = iter(items)
    while chunk := list(islice(iterator, size)):
        yield chunk


def table_cell(value):
    return "—" if value is None else str(value)


def csv_cell(value):
    text = "" if value is None else str(value)
    if isinstance(value, str) and (
            text.lstrip().startswith(("=", "+", "-", "@"))
            or text.startswith(("\t", "\n", "\r"))):
        return "'" + text
    return text


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([csv_cell(column) for column in columns])
        writer.writerows([csv_cell(cell) for cell in row] for row in rows)


class Site:
    def __init__(self, ledger, settings, output):
        self.ledger = ledger
        self.settings = settings
        self.output = output
        self.title = settings.title or ledger.options.get("title") or "Beanie"
        self.environment = Environment(
            loader=PackageLoader("beanie_ledger"),
            autoescape=select_autoescape(("html",)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters.update(money=money, cell=table_cell, axis_number=axis_number)
        self.account_paths = {name: f"accounts/{slug(name)}/index.html" for name in ledger.accounts}
        report_names = list(ledger.queries) if settings.queries is None else settings.queries
        self.saved_links = [(name, f"reports/{slug(name)}/index.html") for name in report_names]
        self.transaction_ids = {id(entry): index for index, entry in enumerate(ledger.transactions, 1)}
        self.css = self.write_styles()
        sidebar = Path(__file__).with_name("static") / "sidebar.js"
        self.sidebar_script = self.write_asset(sidebar.name, sidebar.read_bytes())

    def write_styles(self):
        directory = Path(__file__).with_name("static")
        tabler = (directory / "tabler.min.css").read_bytes()
        notice = (directory / "tabler-license.txt").read_bytes()
        styles = tabler + b"\n/*!\n" + notice + b"\n*/\n" + (directory / "site.css").read_bytes()
        return self.write_asset("site.css", styles)

    def write_asset(self, name, content):
        filename = Path(name)
        digest = sha256(content).hexdigest()[:16]
        output_path = f"assets/{filename.stem}-{digest}{filename.suffix}"
        path = self.output / output_path
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return output_path

    def page(self, template, path, title, active, **context):
        def link(target):
            relative = posixpath.relpath(target, str(PurePosixPath(path).parent))
            return relative

        def account_url(account):
            return link(self.account_paths[account]) if account in self.account_paths else None

        output = self.output / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            self.environment.get_template(template).render(
                title=title, site_title=self.title, active=active, link=link,
                stylesheet=link(self.css), sidebar_script=link(self.sidebar_script),
                account_url=account_url,
                saved_links=self.saved_links,
                transaction_id=lambda entry: self.transaction_ids[id(entry)],
                **context,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def numbered_paths(first, count):
        folder = str(PurePosixPath(first).parent)
        return [first, *(f"{folder}/page/{page}/index.html" for page in range(2, count + 1))]

    def render(self):
        ledger = self.ledger
        balance_sheet, trial_balance = ending_statements(ledger)
        for title, path, active, columns in (
            ("Balance Sheet", "balance-sheet/index.html", "balance", [balance_sheet[:1], balance_sheet[1:]]),
            ("Trial Balance", "trial-balance/index.html", "trial", [trial_balance]),
        ):
            self.page("statement.html", path, title, active, columns=columns,
                      currencies=ledger.currencies, chart=None, period=None, as_of=ledger.as_of)
        self.render_income("index.html", "all")

        journal = list(batches(reversed(ledger.transactions), self.settings.page_size)) or [[]]
        journal_paths = self.numbered_paths("journal/index.html", len(journal))
        for index, (path, entries) in enumerate(zip(journal_paths, journal)):
            self.page("journal.html", path, "Journal", "journal", entries=entries,
                      paths=journal_paths, index=index, count=len(ledger.transactions))

        grouped = defaultdict(list)
        for name, opened in ledger.accounts.items():
            grouped[name.split(":")[0]].append((name, opened, ledger.closed.get(name)))
            pages = list(batches(ledger.register(name), self.settings.page_size)) or [[]]
            paths = self.numbered_paths(self.account_paths[name], len(pages))
            for index, (path, rows) in enumerate(zip(paths, pages)):
                self.page(
                    "register.html", path, name, "accounts",
                    name=name, opened=opened, closed=ledger.closed.get(name),
                    positions=ledger.positions(name) if index == 0 else [],
                    rows=rows, row_count=len(ledger.registers[name]), paths=paths, index=index,
                )
        self.page("accounts.html", "accounts/index.html", "Accounts", "accounts", groups=grouped)

        self.page("periods.html", "activity/index.html", "Income & spending", "activity",
                  years=[period for period in ledger.periods if len(period) == 4],
                  months=[period for period in ledger.periods if len(period) == 7])
        for period in ledger.periods:
            self.render_income(f"income/{period}/index.html", period)
            report = ledger.flow_report(period)
            sections = (
                ("Categories", ("Account", "Currency", "Net amount", "Type"), report.categories),
                ("Payees", ("Payee", "Currency", "Net spending"), report.payees),
            )
            for name, columns, rows in sections:
                path = f"activity/{period}/{'payees/' if name == 'Payees' else ''}index.html"
                self.table_pages(
                    path, f"{period} · {name}", "activity", columns, rows,
                    period=period, totals=report.totals, show_accounts=name == "Categories",
                )

        holdings = [
            (account, position, ledger.valuation(position))
            for account in ledger.accounts if account.split(":")[0] in ledger.asset_roots
            for position in ledger.positions(account)
        ]
        self.page(
            "holdings.html", "holdings/index.html", "Holdings", "holdings",
            holdings=holdings, as_of=ledger.as_of,
            valuation_currency=ledger.currencies[0] if ledger.currencies else None,
        )

        quotes = [(entry.date, entry.currency, entry.amount)
                  for entry in reversed(ledger.entries) if isinstance(entry, data.Price)]
        self.table_pages("commodities/index.html", "Commodities", "commodities",
                         ("Date", "Commodity", "Quoted price"), quotes, back=False)
        counts = sorted(Counter(type(entry).__name__ for entry in ledger.entries).items())
        self.table_pages("statistics/index.html", "Statistics", "statistics",
                         ("Directive", "Count"), counts, back=False)

        saved = []
        for report in ledger.query_reports(self.settings.queries):
            directory = f"reports/{slug(report.name)}"
            path = f"{directory}/index.html"
            csv_path = f"{directory}/results.csv" if self.settings.query_csv else None
            if csv_path:
                write_csv(self.output / csv_path, report.columns, report.rows)
            self.table_pages(path, report.name, "reports", report.columns, report.rows, csv_path=csv_path)
            saved.append((report.name, path, len(report.rows)))
        self.page("reports.html", "reports/index.html", "Query", "reports", reports=saved)
        self.page("404.html", "404.html", "Page not found", "")

    def render_income(self, path, period):
        ledger = self.ledger
        chart = monthly_activity(ledger, period)
        self.page("statement.html", path, "Income Statement", "income",
                  columns=income_statement(ledger, period), currencies=ledger.currencies,
                  chart=chart, period=period, as_of=None)

    def table_pages(self, first, title, active, columns, rows, **context):
        pages = list(batches(rows, self.settings.page_size)) or [[]]
        paths = self.numbered_paths(first, len(pages))
        for index, (path, chunk) in enumerate(zip(paths, pages)):
            self.page(
                "table.html", path, title, active, columns=columns, rows=chunk,
                paths=paths, index=index, row_count=len(rows),
                period=context.get("period"), csv_path=context.get("csv_path"),
                totals=context.get("totals", ()), show_accounts=context.get("show_accounts", False),
                back=context.get("back", True),
            )


@contextmanager
def build_lock(path):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BuildError(f"Build lock exists at {path}. Check for an active build.") from exc
    os.close(descriptor)
    try:
        yield
    finally:
        path.unlink()


def build_site(ledger_path, output, config_path=None):
    ledger_path = ledger_path.resolve(strict=True)
    if output.is_symlink():
        raise BuildError(f"Refusing to replace a symbolic link: {output}")
    output = output.resolve()
    settings = Settings.load(config_path)
    ledger = Ledger(ledger_path)
    sources = [ledger_path, *(Path(path).resolve() for path in ledger.options["include"])]
    if config_path:
        sources.append(config_path.resolve())
    if any(source.is_relative_to(output) for source in sources):
        raise BuildError("The output directory contains a ledger source or the site configuration.")

    output.parent.mkdir(parents=True, exist_ok=True)
    marker = ".beanie-site.json"
    with build_lock(output.parent / f".{output.name}.beanie.lock"):
        if output.exists() and (
            not output.is_dir()
            or (any(output.iterdir()) and not (output / marker).is_file())
        ):
            raise BuildError(f"{output} is not empty and was not created by Beanie.")
        with TemporaryDirectory(prefix=f".{output.name}.beanie-", dir=output.parent) as temp:
            rendered = Path(temp) / "rendered"
            rendered.mkdir()
            Site(ledger, settings, rendered).render()
            manifest = {
                "schema_version": 1,
                "generator_version": version("beanie-ledger"),
                "ledger_as_of": ledger.as_of.isoformat() if ledger.as_of else None,
                "valuation_currency": ledger.currencies[0] if ledger.currencies else None,
            }
            revision = os.environ.get("CF_PAGES_COMMIT_SHA")
            if revision and re.fullmatch(r"[0-9a-f]{40}", revision):
                manifest["source_revision"] = revision
            (rendered / marker).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            sizes = [path.stat().st_size for path in rendered.rglob("*") if path.is_file()]
            previous = Path(temp) / "previous"
            if output.exists():
                output.rename(previous)
            try:
                rendered.rename(output)
            except OSError:
                if previous.exists():
                    previous.rename(output)
                raise
    return BuildStats(len(sizes), sum(sizes))
