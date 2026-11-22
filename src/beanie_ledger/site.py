"""Build and publish an ordinary directory of static files."""

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version
from itertools import islice
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
import csv
import json
import os
import posixpath
import re
import tomllib

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from beancount.core import data
from beancount.core.convert import get_cost

from .model import BuildError, Ledger
from .analytics import StatementReports, rows
from .charts import trend
from .valuation import period_end, valuation_modes


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


def leaf_filename(value):
    return PureWindowsPath(PurePosixPath(value).name).name


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
        self.account_paths = {name: f"accounts/{slug(name)}/index.html" for name in ledger.account_tree}
        report_names = list(ledger.queries) if settings.queries is None else settings.queries
        self.saved_links = [(name, f"reports/{slug(name)}/index.html") for name in report_names]
        self.transaction_ids = {id(entry): index for index, entry in enumerate(ledger.transactions, 1)}
        self.css = self.write_styles()
        self.sidebar_script = self.write_script("sidebar.js")
        self.report_script = self.write_script("reports.js")
        self.statements = StatementReports(ledger)
        self.browser_script = self.write_script("browse.js")

    def write_script(self, name):
        return self.write_asset(name, (Path(__file__).with_name("static") / name).read_bytes())

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

    def page_context(self, path):
        def link(target):
            relative = posixpath.relpath(target, str(PurePosixPath(path).parent))
            return relative

        def account_url(account):
            return link(self.account_paths[account]) if account in self.account_paths else None

        return dict(link=link, account_url=account_url)

    def page(self, template, path, title, active, **context):
        urls = self.page_context(path)
        output = self.output / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            self.environment.get_template(template).render(
                title=title, site_title=self.title, active=active,
                stylesheet=urls["link"](self.css), sidebar_script=urls["link"](self.sidebar_script),
                saved_links=self.saved_links,
                transaction_id=lambda entry: self.transaction_ids[id(entry)],
                **urls, **context,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def numbered_paths(first, count):
        folder = str(PurePosixPath(first).parent)
        return [first, *(f"{folder}/page/{page}/index.html" for page in range(2, count + 1))]

    def render(self):
        ledger = self.ledger
        for period in ("all", *ledger.periods):
            for active, directory, title in (
                ("income", "income", "Income Statement"),
                ("balance", "balance-sheet", "Balance Sheet"),
                ("trial", "trial-balance", "Trial Balance"),
            ):
                self.render_statement(title, active, directory, period)

        self.render_journal()

        grouped = defaultdict(list)
        for name, opened in ledger.accounts.items():
            grouped[name.split(":")[0]].append((name, opened, ledger.closed.get(name)))
        for name in ledger.account_tree:
            self.render_account(name)
        self.page("accounts.html", "accounts/index.html", "Accounts", "accounts",
                  groups=grouped, browser_script=self.browser_script)

        self.page("periods.html", "activity/index.html", "Income & spending", "activity",
                  years=[period for period in ledger.periods if len(period) == 4],
                  months=[period for period in ledger.periods if len(period) == 7])
        for period in ledger.periods:
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

        self.render_holdings()
        self.render_commodities()
        self.render_ledger_views()

        saved = []
        for report in ledger.query_reports(self.settings.queries):
            directory = f"reports/{slug(report.name)}"
            path = f"{directory}/index.html"
            csv_path = f"{directory}/results.csv" if self.settings.query_csv else None
            if csv_path:
                write_csv(self.output / csv_path, report.columns, report.rows)
            self.table_pages(path, report.name, "reports", report.columns, report.rows,
                             csv_path=csv_path, query=ledger.queries[report.name])
            saved.append((report.name, path, len(report.rows)))
        self.page("reports.html", "reports/index.html", "Query", "reports", reports=saved)
        self.page("404.html", "404.html", "Page not found", "")

    def render_statement(self, title, active, directory, period):
        ledger = self.ledger
        is_income = active == "income"
        landing = "index.html" if is_income else f"{directory}/index.html"
        path = landing if period == "all" else f"{directory}/{period}/index.html"
        urls = self.page_context(path)
        modes = {}
        default_columns = None
        for mode, label in valuation_modes(ledger.currencies).items():
            if is_income:
                columns, amounts, timeline = self.statements.income(period, mode)
            else:
                columns, amounts, timeline = self.statements.ending(period, mode, active)
            if mode == "cost":
                default_columns = columns
                tables = None
            else:
                tables = self.environment.get_template("statement_tables.html").render(
                    columns=columns, currencies=ledger.currencies, **urls,
                )
            accounts = {account for account, _ in amounts}
            accounts.update(account for _, month in timeline for account, _ in month)
            accounts |= {
                ":".join(account.split(":")[:depth])
                for account in accounts for depth in range(1, len(account.split(":")))
            }
            payload = {
                "roots": list((ledger.income, ledger.expenses) if is_income else
                              (*ledger.balance_roots, *((ledger.income, ledger.expenses) if active == "trial" else ()))),
                "accounts": {account: urls["account_url"](account) for account in sorted(accounts)},
                "currencies": ledger.currencies,
                "rows": rows(amounts),
                "timeline": [
                    [month, rows(values), urls["link"](f"income/{month}/index.html")
                     if is_income and month in ledger.periods else None]
                    for month, values in timeline
                ],
                "tables": tables,
                "report": active,
            }
            asset = self.write_asset("statement.json", json.dumps(payload, separators=(",", ":")).encode("utf-8"))
            modes[mode] = label, asset
        self.page("statement.html", path, title, active, columns=default_columns,
                  currencies=ledger.currencies, report_script=self.report_script, valuations=modes,
                  periods=ledger.periods, period=period, landing=landing, report_directory=directory,
                  is_income=is_income, as_of=None if is_income else period_end(period, ledger.as_of))

    def render_account(self, name):
        ledger = self.ledger
        journal_path = self.account_paths[name]
        directory = PurePosixPath(journal_path).parent
        tabs = [
            ("journal", "Account Journal", journal_path),
            ("changes", "Changes (monthly)", f"{directory}/changes/index.html"),
            ("balances", "Balances (monthly)", f"{directory}/balances/index.html"),
        ]
        months = ledger.account_months(name)
        currencies = sorted({currency for _, _, balances in months for currency, _ in balances},
                            key=lambda code: (code not in ledger.currencies, code))
        series = [
            (currency, trend([(month, dict(balances).get(currency, 0)) for month, _, balances in months]))
            for currency in currencies
        ]
        context = dict(
            name=name, opened=ledger.accounts.get(name), closed=ledger.closed.get(name),
            includes_children=any(path.startswith(name + ":") for path in ledger.account_tree),
            tabs=tabs,
        )
        pages = list(batches(ledger.register(name), self.settings.page_size)) or [[]]
        paths = self.numbered_paths(journal_path, len(pages))
        for index, (path, chunk) in enumerate(zip(paths, pages)):
            self.page("register.html", path, name, "accounts",
                      rows=chunk, row_count=len(ledger.registers[name]), paths=paths, index=index,
                      series=series if index == 0 else [],
                      positions=ledger.positions(name) if index == 0 else [],
                      browser_script=self.browser_script, journal_entry_path=self.journal_entry_path, **context)
        for key, _, path in tabs[1:]:
            self.page("account_months.html", path, name, "accounts",
                      months=reversed(months), mode=key, series=series if key == "balances" else [],
                      **context)

    def render_journal(self):
        groups = defaultdict(list)
        for entry in reversed(self.ledger.entries):
            kind = type(entry).__name__
            if kind in {"Transaction", "Balance", "Open", "Close", "Pad", "Note", "Document", "Event", "Price", "Commodity"}:
                groups[kind].append(entry)
        years = defaultdict(list)
        for entry in groups["Transaction"]:
            search_terms = (
                entry.payee or "", entry.narration or "", *sorted(entry.tags), *sorted(entry.links),
                *(posting.account for posting in entry.postings),
            )
            url = f"../{self.journal_entry_path(entry)}#transaction-{self.transaction_ids[id(entry)]}"
            years[entry.date.year].append([
                entry.date.isoformat(), entry.payee or entry.narration or "Transaction",
                " ".join(search_terms).lower(), url,
            ])
        search_index = {
            year: self.write_asset("journal-index.json", json.dumps(rows, separators=(",", ":")).encode("utf-8"))
            for year, rows in years.items()
        }
        labels = [
            ("Transaction", "Transactions"), ("Balance", "Balance checks"), ("Note", "Notes"),
            ("Document", "Documents"), ("Event", "Events"), ("Open", "Opened accounts"),
            ("Close", "Closed accounts"), ("Pad", "Pads"), ("Price", "Prices"), ("Commodity", "Commodities"),
        ]
        filters = [
            (kind, label, "journal/index.html" if kind == "Transaction" else f"journal/{kind.lower()}/index.html")
            for kind, label in labels if kind == "Transaction" or groups[kind]
        ]
        for kind, label, first in filters:
            entries = groups[kind]
            pages = list(batches(entries, self.settings.page_size)) or [[]]
            paths = self.numbered_paths(first, len(pages))
            for index, (path, chunk) in enumerate(zip(paths, pages)):
                self.page("journal.html", path, "Journal", "journal", entries=chunk,
                          filters=filters, kind=kind, label=label, basename=leaf_filename,
                          paths=paths, index=index, count=len(entries), browser_script=self.browser_script,
                          search_index=search_index)

    def journal_entry_path(self, entry):
        offset = len(self.transaction_ids) - self.transaction_ids[id(entry)]
        page = offset // self.settings.page_size + 1
        return "journal/index.html" if page == 1 else f"journal/page/{page}/index.html"

    def render_holdings(self):
        ledger = self.ledger
        holdings = [
            (account, position, get_cost(position), ledger.valuation(position))
            for account in ledger.accounts if account.split(":")[0] in ledger.asset_roots
            for position in ledger.positions(account)
        ]
        tabs = [
            ("all", "Holdings", "holdings/index.html"),
            ("account", "By account", "holdings/by-account/index.html"),
            ("currency", "By currency", "holdings/by-currency/index.html"),
            ("cost", "By cost currency", "holdings/by-cost-currency/index.html"),
        ]
        for key, _, path in tabs:
            groups = defaultdict(list)
            if key != "all":
                for account, position, cost, value in holdings:
                    group = {"account": account, "currency": position.units.currency, "cost": cost.currency}[key]
                    groups[group].append((position, cost, value))
            totals = []
            for group, lots in sorted(groups.items()):
                units = defaultdict(Decimal)
                costs = defaultdict(Decimal)
                market = defaultdict(Decimal)
                unpriced = 0
                for position, cost, value in lots:
                    units[position.units.currency] += position.units.number
                    costs[cost.currency] += cost.number
                    if value is None:
                        unpriced += 1
                    else:
                        market[value[1]] += value[0]
                totals.append((group, sorted(units.items()), sorted(costs.items()), sorted(market.items()), unpriced))
            self.page("holdings.html", path, "Holdings", "holdings",
                      holdings=holdings, totals=totals, mode=key, tabs=tabs, as_of=ledger.as_of,
                      valuation_currency=ledger.currencies[0] if ledger.currencies else None)

    def render_commodities(self):
        pairs = defaultdict(list)
        for entry in self.ledger.entries:
            if isinstance(entry, data.Price):
                pairs[entry.currency, entry.amount.currency].append((entry.date, entry.amount.number))
        paths = {pair: f"commodities/{slug('/'.join(pair))}/index.html" for pair in sorted(pairs)}
        options = [(pair, paths[pair], prices[-1]) for pair, prices in sorted(pairs.items())]
        self.page("commodities.html", "commodities/index.html", "Commodities", "commodities",
                  pairs=options)
        for (base, quote), values in sorted(pairs.items()):
            title = f"{base} / {quote}"
            points = [(date.isoformat(), amount) for date, amount in values]
            self.page("commodity.html", paths[base, quote], title, "commodities", pairs=options,
                      quote=quote, prices=reversed(values), trend=trend(points), browser_script=self.browser_script)

    def render_ledger_views(self):
        ledger = self.ledger
        postings = Counter(posting.account for entry in ledger.transactions for posting in entry.postings)
        activities = {}
        latest_checks = {}
        for entry in ledger.entries:
            if isinstance(entry, data.Transaction):
                for posting in entry.postings:
                    activities[posting.account] = entry.date
            elif isinstance(entry, data.Balance):
                latest_checks[entry.account] = entry
        counts = sorted(Counter(type(entry).__name__ for entry in ledger.entries).items())
        self.page("statistics.html", "statistics/index.html", "Statistics", "statistics",
                  counts=counts, postings=sorted(postings.items(), key=lambda item: (-item[1], item[0])),
                  activity=[(account, activities.get(account), latest_checks.get(account)) for account in ledger.accounts])
        for cls, tab, title, columns, get_row in (
            (data.Event, "events", "Events", ("Date", "Type", "Description"),
             lambda entry: (entry.date, entry.type, entry.description)),
            (data.Document, "documents", "Documents", ("Account", "Date", "Name"),
             lambda entry: (entry.account, entry.date, leaf_filename(entry.filename))),
        ):
            self.table_pages(f"{tab}/index.html", title, tab, columns,
                             [get_row(entry) for entry in reversed(ledger.entries) if isinstance(entry, cls)],
                             show_accounts=cls is data.Document, back=False)
        self.table_pages("options/index.html", "Options", "options", ("Key", "Value"), [
            ("Ledger title", self.title),
            ("Operating currencies", ", ".join(ledger.currencies) or "—"),
            ("Booking method", ledger.options["booking_method"].name),
            ("Account types", ", ".join((*ledger.balance_roots, ledger.income, ledger.expenses))),
            ("Rows per page", self.settings.page_size),
            ("Saved query CSV exports", "Enabled" if self.settings.query_csv else "Disabled"),
        ], back=False)
        self.page("help.html", "help/index.html", "Help", "help")

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
                query=context.get("query"),
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
