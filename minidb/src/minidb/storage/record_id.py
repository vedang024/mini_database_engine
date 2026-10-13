"""RecordId: the stable address of one record in a table file."""

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class RecordId:
    page_id: int
    slot: int

    def __str__(self) -> str:
        return f"RID(page={self.page_id}, slot={self.slot})"