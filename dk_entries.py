from __future__ import annotations

import csv
import os
import tempfile
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

    def selected_rows(self, contest_id=None):
        if contest_id is None:
            return list(self.entry_row_indexes)
        rows = [i for i in self.entry_row_indexes if self.rows[i][2].strip() == str(contest_id).strip()]
        if not rows:
            raise ValueError("The selected contest is not present in this entries file.")
        return rows

    def contests(self):
        groups = {}
        for i in self.entry_row_indexes:
            row = self.rows[i]
            key = row[2].strip()
            item = groups.setdefault(key, dict(id=key, name=row[1], fee=row[3], count=0))
            item['count'] += 1
        return list(groups.values())


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
    if any(len(rows[i]) < 4 or not rows[i][2].strip() for i in entry_rows):
        raise ValueError("An entry is missing its contest ID or metadata.")
    if len({rows[i][0].strip() for i in entry_rows}) != len(entry_rows):
        raise ValueError("Duplicate Entry IDs found; download a fresh entries file.")
    return EntriesTemplate(rows, entry_rows, roster_start, roster_headers)


def write_updated_entries(
    template_path: str,
    output_path: str,
    lineup_rows: Sequence[Sequence[str]],
    *, contest_id=None,
) -> EntriesTemplate:
    """Replace only roster cells while preserving entry IDs and DK metadata."""
    template = read_entries_template(template_path)
    target_rows = template.selected_rows(contest_id)
    lineups = [[str(value or "").strip() for value in row] for row in lineup_rows]
    if len(lineups) != len(target_rows):
        raise ValueError(
            f"The selected scope has {len(target_rows)} entries, but {len(lineups)} lineups are saved. "
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
    for row_index, lineup in zip(target_rows, lineups):
        row = rows[row_index]
        if len(row) < required_width:
            row.extend([""] * (required_width - len(row)))
        row[template.roster_start:required_width] = lineup

    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(output_path)), suffix='.csv.tmp')
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerows(rows)
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return template

