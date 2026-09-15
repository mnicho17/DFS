import csv
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from learning_db import import_results_folder

class ResultsFolderTests(unittest.TestCase):
    def test_only_new_results_including_subfolders(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'results';folder.mkdir();db=str(Path(tmp)/'history.sqlite')
            path=folder/'NFL Showdown.csv'
            with path.open('w',newline='',encoding='utf-8') as f:
                w=csv.writer(f);w.writerow(['Rank','EntryId','EntryName','Points','Lineup'])
                for i in range(30):w.writerow([i+1,1000+i,'Example_User',50,'CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'])
            (folder/'salary.csv').write_text('Name,ID,Salary\nAlpha,1234,5000\n',encoding='utf-8')
            first=import_results_folder(str(folder),username='Example_User',db_path=db,archive_files=False)
            self.assertEqual(first['files_imported'],1)
            self.assertEqual(first['personal_results_added'],30)
            self.assertEqual(first['ignored_csv_files'],1)
            sub=folder/'archive';sub.mkdir();renamed=sub/'renamed.csv';shutil.move(path,renamed)
            again=import_results_folder(str(folder),username='Example_User',db_path=db,archive_files=False)
            self.assertEqual(again['files_imported'],0)
            self.assertEqual(again['duplicates_skipped'],1)
            with closing(sqlite3.connect(db)) as c:
                self.assertEqual(c.execute('select count(*) from historical_results').fetchone()[0],30)
                self.assertEqual(c.execute('select source_path from historical_imports').fetchone()[0],str(renamed))
    def test_missing_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'unavailable'):
                import_results_folder(str(Path(tmp)/'missing'))
