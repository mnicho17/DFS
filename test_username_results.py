import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from learning_db import import_historical_result_csvs, generate_learning_report, _dk_username


class UsernameResultsTests(unittest.TestCase):
    def test_single_file_exact_username_and_repeat_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'NFL Showdown 150-Max.csv'
            db = str(Path(tmp)/'history.sqlite')
            with path.open('w',newline='',encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow(['Rank','EntryId','EntryName','Points','Lineup','Player','Roster Position','%Drafted','FPTS'])
                for i in range(30):
                    user = 'Test_User (1/150)' if i < 2 else 'Test_User2 (1/150)'
                    writer.writerow([i+1,1000+i,user,100-i,'CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot','','','','12.5'])
            # First field-only import can be enriched by reimporting with a username.
            import_historical_result_csvs([str(path)],db_path=db,archive_files=False)
            result = import_historical_result_csvs([str(path)],username='test_user',db_path=db,archive_files=False)
            self.assertEqual(result['personal_results_added'],2)
            again = import_historical_result_csvs([str(path)],username='TEST_USER',db_path=db,archive_files=False)
            self.assertEqual(again['personal_results_added'],0)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('select actual_points from historical_results order by row_index').fetchall(),[(100.,),(99.,)])
                self.assertEqual(conn.execute('select count(*) from contest_field_summaries').fetchone()[0],1)
            report = generate_learning_report(db_path=db,username='Test_User')['text']
            self.assertIn('submitted results: 2',report)
            self.assertIn('average score: 99.50',report)
            self.assertIn('Captain: alpha: yours 100.0%',report)

    def test_username_preserves_punctuation_and_strips_counter_only(self):
        self.assertEqual(_dk_username(' Example_User (7/150) '),'example_user')
        self.assertNotEqual(_dk_username('Example.User'),_dk_username('Example_User'))
