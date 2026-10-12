"""Command-line entry point: parse args, load config, set up logging, dispatch."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from minidb import __version__
from minidb.config import Config, ConfigError, load_config
from minidb.logging_setup import configure_logging
from minidb.storage.errors import StorageError
from minidb.storage.record import Record
from minidb.storage.record_store import RecordStore
from minidb.storage.disk_manager import FIRST_DATA_PAGE_ID, DiskManager

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minidb", description="MiniDB database engine")
    parser.add_argument("--config", help="path to a TOML configuration file")
    parser.add_argument("--version", action="version", version=f"minidb {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    insert = sub.add_parser("insert", help="insert one record (temporary dev command)")
    insert.add_argument("--id", type=int, required=True)
    insert.add_argument("--name", required=True)
    insert.add_argument("--email", required=True)
    sub.add_parser("scan", help="print every record (temporary dev command)")
    sub.add_parser("stats", help="display database page statistics")
    sub.add_parser("pages", help="display database pages")
    return parser


def _open_disk(config: Config) -> DiskManager:
    return DiskManager(config.database.db_path, config.database.page_size)


def _cmd_insert(config: Config, args: argparse.Namespace) -> int:
    with _open_disk(config) as disk:
        store = RecordStore(disk)
        page_id = store.insert(Record(args.id, args.name, args.email))
        store.flush()  # explicit durability point (close() also syncs)
    print(f"inserted id={args.id} into page {page_id}")
    return 0


def _cmd_scan(config: Config) -> int:
    count = 0
    with _open_disk(config) as disk:
        for record in RecordStore(disk).scan():
            print(f"{record.id} | {record.name} | {record.email}")
            count += 1
    print(f"({count} row(s))")
    return 0

def _cmd_stats(config: Config) -> int:
       with _open_disk(config) as disk:
           print("page  records  used  free")
           for page_id, count, used, free in RecordStore(disk).page_stats():
               print(f"{page_id:<5} {count:<8} {used:<5} {free}")
       return 0

def _cmd_pages(config: Config) -> int:
    with _open_disk(config) as disk:
        print("page  offset  first 16 bytes")
        print(f"{0:<5} {disk.offset_of(0):<7} (header page)")
        for page_id in range(FIRST_DATA_PAGE_ID, disk.num_pages):
            page = disk.fetch_page(page_id)
            print(f"{page_id:<5} {disk.offset_of(page_id):<7} {page.read(0, 16).hex()}")
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    """Run the program and return a process exit code."""
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config.logging)

    if args.command is None:
        logger.info("MiniDB %s starting", __version__)
        logger.info(
            "data_dir=%s page_size=%d", config.database.data_dir, config.database.page_size
        )
        logger.info("No command given (try 'insert' or 'scan'). Exiting.")
        return 0

    try:
        if args.command == "insert":
            return _cmd_insert(config, args)
        if args.command == "stats":
               return _cmd_stats(config)
        if args.command == "pages":
            return _cmd_pages(config)
        return _cmd_scan(config)
    except StorageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1