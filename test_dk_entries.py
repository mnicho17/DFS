from __future__ import annotations

import csv
import os
import tempfile
import unittest

from dk_entries import read_entries_template, write_updated_entries


class DraftKingsEntriesTests(unittest.TestCase):
    def _template(self, folder: str) -> str:
        path = os.path.join(folder, "entries.csv")
        rows = [
            ["Entry ID", "Contest Name", "Contest ID", "Entry Fee", "CPT", "FLEX", "FLEX", "", "Instructions"],
            ["101", "Showdown", "77", "$0.50", "Old CPT", "Old A", "Old B", "", "keep this"],
            ["102", "Showdown", "77", "$0.50", "Old CPT", "Old A", "Old B", "", "player pool"],
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerows(rows)
        return path

    def test_updates_only_roster_cells_and_preserves_entry_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._template(folder)
            output = os.path.join(folder, "updated.csv")
            template = write_updated_entries(source, output, [["1", "2", "3"], ["4", "5", "6"]])
            self.assertEqual(template.entry_count, 2)
            with open(output, newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[1][:4], ["101", "Showdown", "77", "$0.50"])
            self.assertEqual(rows[1][4:7], ["1", "2", "3"])
            self.assertEqual(rows[1][8], "keep this")
            self.assertEqual(rows[2][4:7], ["4", "5", "6"])

    def test_requires_exact_lineup_count(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._template(folder)
            with self.assertRaisesRegex(ValueError, "2 entries, but 1 lineups"):
                write_updated_entries(source, os.path.join(folder, "out.csv"), [["1", "2", "3"]])

    def test_recognizes_real_dk_entry_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._template(folder)
            template = read_entries_template(source)
            self.assertEqual(template.roster_headers, ["CPT", "FLEX", "FLEX"])


if __name__ == "__main__":
    unittest.main()

