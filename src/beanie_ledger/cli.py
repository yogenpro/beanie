"""The Beanie command line."""

import argparse
from pathlib import Path
import sys

from .model import BuildError
from .site import build_site


def main(argv=None):
    parser = argparse.ArgumentParser(prog="beanie", description="Build a static Beancount viewer.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build", help="Validate a ledger and generate its static site.")
    build.add_argument("ledger", type=Path, help="Main Beancount file.")
    build.add_argument("--output", type=Path, default=Path("dist"), help="Generated output directory.")
    build.add_argument("--config", type=Path, help="Optional TOML site configuration.")
    args = parser.parse_args(argv)
    try:
        stats = build_site(args.ledger, args.output, args.config)
    except (BuildError, OSError) as exc:
        print(f"beanie: {exc}", file=sys.stderr)
        return 1
    print(f"Built {stats.files:,} files ({stats.bytes:,} bytes) in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
