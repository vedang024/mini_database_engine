"""Logging setup. Modules just call logging.getLogger(__name__)."""

from __future__ import annotations

import logging

from minidb.config import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    """Configure the root logger once, at program startup."""
    logging.basicConfig(
        level=config.level,
        format=config.format,
        force=True,  # replace existing handlers so calling twice is safe
    )