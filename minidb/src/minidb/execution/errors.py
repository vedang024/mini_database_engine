"""Errors raised while planning and executing a statement."""


class QueryError(Exception):
    """Base class for query-level errors."""


class PlanError(QueryError):
    """The statement is valid SQL but makes no sense for this database.

    Examples: unknown table or column, wrong number of values, comparing a number
    with text. These are the *semantic* errors of Phase 0: the parser cannot see
    them because it has no catalog.
    """
