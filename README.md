# Beanie

Beanie is a static site generator for Beancount 3. It turns a validated ledger
into a read-only Tabler site with account registers, income and spending reports,
holdings, and saved queries. The generated pages work without JavaScript.

## Try it

Install Python 3.11 or later. From a clone of this repository, build the synthetic
example and serve it over loopback:

```sh
uv sync --locked
uv run --locked beanie build examples/main.beancount --output dist
uv run --locked python -m http.server --bind 127.0.0.1 --directory dist 8000
```

Open http://127.0.0.1:8000. For another ledger, pass the main Beancount file as
the first argument to `beanie build`. Includes and plugins are loaded by
Beancount. Install any trusted plugins your ledger requires in the build
environment.

The GitHub repository and command are called Beanie. The Python distribution
is named `beanie-ledger` to distinguish it from the unrelated Beanie ODM.
Install it from a Git tag or pin the full commit SHA in your project's lockfile.

```sh
uv add 'beanie-ledger @ git+https://github.com/yogenpro/beanie.git@v0.1.0'
```

## Options

Pass `--config site.toml` to customize the build. All keys are optional.

```toml
title = "Household ledger"
page_size = 50
queries = ["Spending by category"]
query_csv = false
```

The default is to run all saved Beancount `query` directives. Use `queries = []`
to omit them. Selected query results appear as HTML tables. Enable CSV only if
you intend to publish the selected query output as a downloadable file.

Income and spending use the ledger's income and expense postings, grouped by
native currency. Refunds reduce spending. The holdings page keeps native units,
lot cost, and a market value in the first operating currency in separate
columns. It uses prices recorded in the ledger and shows the price date or
labels the position unpriced. A valid ledger build does not establish statement
reconciliation or historical completeness.

## Hosting

Publish the output directory on any static host. For Cloudflare Pages with Git
integration, install the consuming repository's locked dependencies, run
`beanie build` with its ledger entry point, and configure Pages to serve
`dist`. Relative links also work beneath a path prefix. The build requires no
Node or cloud SDK.

Treat generated HTML and CSV as financial data. Keep the ledger and build output
private unless you intend to share them. Configure and verify access for every
deployment hostname and path before publishing a private ledger. The included
examples contain invented transactions.

Beanie rebuilds the output in a temporary directory and publishes it when
rendering succeeds. It accepts a new directory, an empty directory, or a
previous Beanie build. Give each site its own output directory.

## Contributing

Run `uv run --locked python -m unittest discover -s tests -v`. Use synthetic
ledgers in public examples, screenshots, and issues.

Beanie is licensed under GPL-2.0-only. The bundled Tabler and Bootstrap stylesheet
is MIT-licensed. See [third-party notices](THIRD_PARTY_NOTICES.md).
