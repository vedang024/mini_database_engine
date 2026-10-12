"""Configuration: load settings from a TOML file and validate them."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

MIN_PAGE_SIZE = 512
MAX_PAGE_SIZE = 65536
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
DB_FILE_NAME = "minidb.db"

class ConfigError(Exception):
    """Raised when the configuration is invalid."""


@dataclass(frozen=True) #config cant be changed after loading 
class DatabaseConfig:
    data_dir: Path = Path("data")
    page_size: int = 4096

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        size = self.page_size
        if (
            not isinstance(size, int)
            or not (MIN_PAGE_SIZE <= size <= MAX_PAGE_SIZE)
            or size & (size - 1) != 0  # not a power of two
        ):
            raise ConfigError(
                f"page_size must be a power of two between "
                f"{MIN_PAGE_SIZE} and {MAX_PAGE_SIZE}, got {size!r}"
            )
    @property
    def db_path(self) -> Path:
        return self.data_dir / DB_FILE_NAME


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    format: str = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

    def __post_init__(self) -> None:
        level = str(self.level).upper()
        if level not in VALID_LOG_LEVELS:
            raise ConfigError(
                f"log level must be one of {sorted(VALID_LOG_LEVELS)}, got {self.level!r}"
            )
        object.__setattr__(self, "level", level)


@dataclass(frozen=True)
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


T = TypeVar("T")


def _build_section(cls: type[T], values: dict[str, Any], name: str) -> T:
    """Build a config section, rejecting unknown keys so typos fail loudly."""
    unknown = set(values) - {f.name for f in fields(cls)}
    if unknown:
        raise ConfigError(f"Unknown key(s) in [{name}]: {sorted(unknown)}")
    return cls(**values)


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration. With no path, return the built-in defaults."""
    if path is None:
        return Config()

    try:
        with open(path, "rb") as f:  # tomllib requires binary mode
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc

    unknown = set(raw) - {"database", "logging"}
    if unknown:
        raise ConfigError(f"Unknown config section(s): {sorted(unknown)}")

    return Config(
        database=_build_section(DatabaseConfig, raw.get("database", {}), "database"),
        logging=_build_section(LoggingConfig, raw.get("logging", {}), "logging"),
    )