from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import List, Sequence


@dataclass(frozen=True)
class EntriesTemplate:
    rows: List[List[str]]
    entry_row_indexes: List[int]
    roster_start: int
    roster_headers: List[str]

    @property
    def entry_count(self) -> int:
        return len(self.entry_row_indexes)


def read_entries_template(path: str) -> EntriesTemplate:
    """Read a DraftKings entry-edit CSV without discarding its extra columns."""
    with open(path, "r", newline="", encoding="utf-8-sig") as handle:
        rows = [list(row) for row in csv.reader(handle)]
    if not rows:
        raise ValueError("The DraftKings entries file is empty.")

    header = rows[0]
    required = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
    if len(header) < 5 or [cell.strip() for cell in header[:4]] != required:
        raise ValueError(
            "This is not a DraftKings entry-edit file. Download your entered lineups "
            "from DraftKings and select that CSV."
        )

    roster_start = 4
    roster_headers: List[str] = []
    for value in header[roster_start:]:
        label = value.strip().upper()
        if not label:
            break
        roster_headers.append(label)
    if not roster_headers:
        raise ValueError("The entries file does not contain roster columns.")

    entry_rows = [
        index for index, row in enumerate(rows[1:], start=1)
        if row and row[0].strip().isdigit()
    ]
    if not entry_rows:
        raise ValueError("The entries file does not contain any editable Entry ID rows.")
    return EntriesTemplate(rows, entry_rows, roster_start, roster_headers)


def write_updated_entries(
    template_path: str,
    output_path: str,
    lineup_rows: Sequence[Sequence[str]],
) -> EntriesTemplate:
    """Replace only roster cells while preserving entry IDs and DK metadata."""
    template = read_entries_template(template_path)
    lineups = [[str(value or "").strip() for value in row] for row in lineup_rows]
    if len(lineups) != template.entry_count:
        raise ValueError(
            f"The file has {template.entry_count} entries, but {len(lineups)} lineups are saved. "
            "Save exactly one lineup for every entry before creating the upload file."
        )
    slot_count = len(template.roster_headers)
    for index, lineup in enumerate(lineups, start=1):
        if len(lineup) != slot_count:
            raise ValueError(
                f"Lineup {index} has {len(lineup)} roster slots; this entries file requires {slot_count}."
            )
        if any(not value for value in lineup):
            raise ValueError(f"Lineup {index} contains a blank DraftKings player ID.")
        if len(set(lineup)) != len(lineup):
            raise ValueError(f"Lineup {index} contains the same DraftKings player ID twice.")

    rows = [list(row) for row in template.rows]
    required_width = template.roster_start + slot_count
    for row_index, lineup in zip(template.entry_row_indexes, lineups):
        row = rows[row_index]
        if len(row) < required_width:
            row.extend([""] * (required_width - len(row)))
        row[template.roster_start:required_width] = lineup

    with open(output_path, "w", newline="", encoding="utf-8-sig") as handle:
        csv.writer(handle).writerows(rows)
    return template

