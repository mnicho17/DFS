import csv
import tempfile
import unittest
from pathlib import Path
from learning_db import import_historical_result_csvs, generate_learning_report, record_export
from test_learning_results import _showdown_lineup, _export_rows

class ResultsAuditTests(unittest.TestCase):
    def test_scores_and_captain_checks_use_side_table_without_overwriting_entry_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=str(Path(tmp)/'history.sqlite');path=Path(tmp)/'NFL Showdown 20-Max.csv'
            lineup=_showdown_lineup();players=[lineup['Captain']]+lineup['Flex']
            names=[p['Name'] for p in players]
            record_export(kind='showdown',sport='NFL',lineups=[lineup],rows=_export_rows(),salary_cap=50000,export_path='test.csv',validation={},db_path=db)
            with path.open('w',newline='',encoding='utf-8') as f:
                w=csv.writer(f);w.writerow(['Rank','EntryId','EntryName','Points','Lineup','Player','Roster Position','%Drafted','FPTS'])
                for i in range(30):
                    side=[names[i],'FLEX',10,10] if i<6 else [names[0],'CPT',5,15] if i==6 else ['','','','']
                    w.writerow([i+1,1000+i,'Example_User (1/1)' if i==0 else 'Other',65,'CPT '+names[0]+' FLEX '+' FLEX '.join(names[1:])]+side)
            import_historical_result_csvs([str(path)],username='Example_User',db_path=db,archive_files=False)
            text=generate_learning_report(db_path=db,username='Example_User')['text']
            self.assertIn('forecast matches 1, unmatched 0',text)
            self.assertIn('1 checked, 0 mismatches',text)
            self.assertIn('1 player pairs checked; 0 differ',text)
            self.assertIn('Largest player forecast misses',text)
            self.assertIn('Ownership units are not recorded',text)
            self.assertNotIn(str(path),text)
            # A corrupt Captain score must flag both consistency checks.
            text_csv=path.read_text(encoding='utf-8').replace(',CPT,5,15',',CPT,5,16')
            path.write_text(text_csv,encoding='utf-8')
            text=generate_learning_report(db_path=db,username='Example_User')['text']
            self.assertIn('1 checked, 1 mismatches',text)
            self.assertIn('1 player pairs checked; 1 differ',text)
            path.unlink()
            self.assertIn('Original standings unavailable',generate_learning_report(db_path=db,username='Example_User')['text'])
