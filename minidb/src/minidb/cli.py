"""Command-line entry point: parse args, load config, set up logging."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from minidb import __version__
from minidb.config import ConfigError, load_config
from minidb.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minidb", description="MiniDB database engine")
    parser.add_argument("--config", help="path to a TOML configuration file")
    parser.add_argument("--version", action="version", version=f"minidb {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the program and return a process exit code."""
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config.logging)

    logger.info("MiniDB %s starting", __version__)
    logger.info(
        "data_dir=%s page_size=%d", config.database.data_dir, config.database.page_size
    )
    logger.info("Engine not implemented yet; nothing to do. Exiting.")
    return 0