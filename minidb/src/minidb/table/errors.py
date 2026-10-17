"""Exceptions raised by the table layer (schemas, rows, tables, catalog)."""


class TableError(Exception):
    """Base class for all table-layer errors."""


class SchemaError(TableError):
    """A table or column definition is invalid."""


class RowError(TableError):
    """A row does not match its schema, or stored row bytes cannot be decoded."""


class RowNotFoundError(TableError):
    """No live row exists at this record id (never stored, or deleted)."""


class TableExistsError(TableError):
    """CREATE TABLE for a name that is already taken."""


class TableNotFoundError(TableError):
    """The named table does not exist."""


class CatalogError(TableError):
    """The catalog and the files on disk disagree."""

class RowValidationError(RowError):
    """A row broke one or more schema rules. `problems` lists every violation."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems