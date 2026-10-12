from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from types import TracebackType

from minidb.storage.errors import DiskManagerError, InvalidPageError

logger = logging.getLogger(__name__)

PageId = int

MAGIC = b"MINIDB01"
HEADER = struct.Struct("<8sI")  # magic (8 bytes), page_size (uint32)
HEADER_PAGE_ID: PageId = 0
FIRST_DATA_PAGE_ID: PageId = 1


class DiskManager:
    def __init__(self, path: str | Path, page_size: int = 4096) -> None:
        self._path = Path(path)
        self._page_size = page_size
        self._num_pages = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # "r+b": read/write an existing file WITHOUT truncating it.
        # "w+b": create a new file. Never use it on an existing database!
        mode = "r+b" if self._path.exists() else "w+b"
        # buffering=0: no Python-level buffer; our calls go straight to the OS.
        self._file = open(self._path, mode, buffering=0)
        try:
            self._open_or_initialize()
        except Exception:
            self._file.close()
            raise

    # ---- properties ---------------------------------------------------
    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def num_pages(self) -> int:
        """Total pages in the file, INCLUDING the header page."""
        return self._num_pages

    # ---- public API ---------------------------------------------------
    def allocate_page(self) -> PageId:
        """Append a zero-filled page and return its id (always >= 1)."""
        page_id = self._num_pages
        self._write_at(page_id * self._page_size, bytes(self._page_size))
        self._num_pages += 1
        logger.debug("Allocated page %d", page_id)
        return page_id

    def read_page(self, page_id: PageId) -> bytes:
        self._check_page_id(page_id)
        data = self._read_at(page_id * self._page_size, self._page_size)
        if len(data) != self._page_size:
            raise DiskManagerError(f"Short read on page {page_id}: got {len(data)} bytes")
        return data

    def write_page(self, page_id: PageId, data: bytes) -> None:
        self._check_page_id(page_id)
        if len(data) != self._page_size:
            raise InvalidPageError(
                f"Page data must be exactly {self._page_size} bytes, got {len(data)}"
            )
        self._write_at(page_id * self._page_size, data)

    def sync(self) -> None:
        """Force OS-cached writes onto the physical device (durability)."""
        os.fsync(self._file.fileno())

    def close(self) -> None:
        if not self._file.closed:
            self.sync()
            self._file.close()
            logger.debug("Closed %s", self._path)

    def __enter__(self) -> "DiskManager":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- internals ----------------------------------------------------
    def _open_or_initialize(self) -> None:
        size = os.fstat(self._file.fileno()).st_size
        if size == 0:  # brand-new (or empty) file: write the header page
            header = HEADER.pack(MAGIC, self._page_size).ljust(self._page_size, b"\x00")
            self._write_at(0, header)
            self.sync()
            self._num_pages = 1
            logger.info("Created %s (page_size=%d)", self._path, self._page_size)
            return

        raw = self._read_at(0, HEADER.size)
        if len(raw) < HEADER.size:
            raise DiskManagerError(f"{self._path} is not a MiniDB file (too short)")
        magic, stored_page_size = HEADER.unpack(raw)
        if magic != MAGIC:
            raise DiskManagerError(f"{self._path} is not a MiniDB file (bad magic bytes)")
        if stored_page_size != self._page_size:
            raise DiskManagerError(
                f"page size mismatch: file was created with {stored_page_size}, "
                f"but {self._page_size} was requested"
            )
        if size % self._page_size != 0:
            raise DiskManagerError(
                f"File size {size} is not a multiple of page size {self._page_size} "
                "(truncated or corrupt file)"
            )
        self._num_pages = size // self._page_size
        logger.info("Opened %s with %d pages", self._path, self._num_pages)

    def _read_at(self, offset: int, length: int) -> bytes:
        self._file.seek(offset)
        return self._file.read(length)

    def _write_at(self, offset: int, data: bytes) -> None:
        self._file.seek(offset)
        written = self._file.write(data)
        if written != len(data):
            raise DiskManagerError(f"Short write: wrote {written} of {len(data)} bytes")

    def _check_page_id(self, page_id: PageId) -> None:
        if not (FIRST_DATA_PAGE_ID <= page_id < self._num_pages):
            raise InvalidPageError(
                f"Page id {page_id} out of range [{FIRST_DATA_PAGE_ID}, {self._num_pages})"
            )