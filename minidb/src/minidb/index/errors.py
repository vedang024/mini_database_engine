"""Exceptions raised by the B+ tree index."""

from minidb.storage.errors import StorageError


class BTreeError(StorageError):
    """Base class for index errors."""


class InvalidKeyError(BTreeError):
    """A key is not an int64."""


class DuplicateKeyError(BTreeError):
    """The key is already in the (unique) index."""


class CorruptIndexError(BTreeError):
    """A node, the meta page, or the tree structure is inconsistent."""