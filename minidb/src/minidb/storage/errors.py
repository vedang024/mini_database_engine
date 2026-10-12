"""Exceptions raised by the storage layer. One base class so callers can catch them all."""


class StorageError(Exception):
    """Base class for all storage-layer errors."""


class DiskManagerError(StorageError):
    """File-level problem: bad header, wrong size, short read or write."""


class InvalidPageError(DiskManagerError):
    """Page id out of range, or page data of the wrong size."""


class RecordFormatError(StorageError):
    """A record could not be encoded, or its bytes could not be decoded."""


class RecordTooLargeError(StorageError):
    """A record can never fit in a single page."""


class CorruptPageError(StorageError):
    """A page's internal structure is inconsistent."""