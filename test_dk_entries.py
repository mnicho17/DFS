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

    def test_interleaved_contests_and_sequential_updates_preserve_every_other_cell(self):
        for slots in (['CPT']+['FLEX']*5, ['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']):
            with tempfile.TemporaryDirectory() as folder:
                source=os.path.join(folder,'source.csv');first=os.path.join(folder,'first.csv');final=os.path.join(folder,'final.csv')
                rows=[['Entry ID','Contest Name','Contest ID','Entry Fee']+slots+['','Instructions']]
                for i,c in enumerate(('77','88','77','88')):
                    rows.append([str(101+i),'Same contest name',c,'$5']+['old']*len(slots)+['','preserve salary table'])
                rows += [[],['','','','','embedded metadata']]
                with open(source,'w',newline='',encoding='utf-8-sig') as f:csv.writer(f).writerows(rows)
                a=[[str(1000+i*100+j) for j in range(len(slots))] for i in range(2)]
                b=[[str(2000+i*100+j) for j in range(len(slots))] for i in range(2)]
                write_updated_entries(source,first,a,contest_id='77')
                interim=read_entries_template(first)
                self.assertEqual(interim.rows[2],rows[2]);self.assertEqual(interim.rows[4],rows[4])
                write_updated_entries(first,final,b,contest_id='88')
                actual=read_entries_template(final).rows
                for i in range(len(rows)):
                    if 1<=i<=4:
                        expected=list(rows[i]);expected[4:4+len(slots)]=(a if i%2 else b)[(i-1)//2]
                        self.assertEqual(actual[i],expected)
                    else:self.assertEqual(actual[i],rows[i])
                with open(source,newline='',encoding='utf-8-sig') as f:self.assertEqual(list(csv.reader(f)),rows)

    def test_scope_errors_do_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source=self._template(folder);output=os.path.join(folder,'out.csv')
            with open(output,'w') as f:f.write('keep existing file')
            for target,lineups in [('missing',[['1','2','3']]),('77',[['1','2','3']])]:
                with self.assertRaises(ValueError):write_updated_entries(source,output,lineups,contest_id=target)
                with open(output) as f:self.assertEqual(f.read(),'keep existing file')
            with open(source,'a',newline='',encoding='utf-8') as f:csv.writer(f).writerow(['101','Other','88','$1','x','y','z'])
            with self.assertRaisesRegex(ValueError,'Duplicate Entry IDs'):read_entries_template(source)

    def test_scope_dialog_requires_deliberate_matching_selection(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5 import QtWidgets
        from entries_scope_ui import EntriesScopeDialog
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            source=self._template(folder)
            with open(source,'a',newline='',encoding='utf-8') as f:csv.writer(f).writerow(['103','Showdown','88','$1','x','y','z'])
            d=EntriesScopeDialog(read_entries_template(source),2)
            self.assertFalse(d.ok.isEnabled())
            d.combo.setCurrentIndex(1);self.assertEqual(d.combo.currentData(),'77');self.assertTrue(d.ok.isEnabled())
            self.assertIn('1 other entries',d.note.text())
            d.combo.setCurrentIndex(2);self.assertFalse(d.ok.isEnabled())
            d.combo.setCurrentIndex(3);self.assertFalse(d.ok.isEnabled())
            d.reject();self.assertEqual(d.result(),QtWidgets.QDialog.Rejected)


if __name__ == "__main__":
    unittest.main()

