"""Phase 4 demo: a slotted page on a scratch file, flushed and reopened."""

import tempfile
from pathlib import Path

from minidb.storage.disk_manager import DiskManager
from minidb.storage.record import Record
from minidb.storage.slotted_page import SlottedPage


def show(sp: SlottedPage, title: str) -> None:
    print(f"-- {title}")
    print(
        f"   slots={sp.num_slots} free_end={sp.free_end} "
        f"contiguous_free={sp.contiguous_free()} total_free={sp.total_free()}"
    )


path = Path(tempfile.mkdtemp()) / "slotted.db"

with DiskManager(path, 4096) as disk:
    page_id = disk.allocate_page()
    sp = SlottedPage(disk.fetch_page(page_id))
    rids = {}
    for rec in [
        Record(1, "Ann", "ann@x.com"),
        Record(2, "Bob", "bob@x.com"),
        Record(3, "Cy", "cy@x.com"),
    ]:
        rids[rec.id] = sp.rid(sp.insert(rec.to_bytes()))
        print("inserted", rec.id, "->", rids[rec.id])
    show(sp, "after 3 inserts")
    sp.delete(rids[2].slot)
    show(sp, "after deleting id=2")
    disk.flush_page(sp.page)

# a brand-new DiskManager = a "restart"
with DiskManager(path, 4096) as disk:
    sp = SlottedPage(disk.fetch_page(page_id))
    for slot, data in sp.iter_records():
        print("read", sp.rid(slot), Record.from_bytes(data))