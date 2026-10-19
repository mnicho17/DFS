from test_environment import install
install()
import datetime
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from slate_catalog import scan, rows


class SlateCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'salaries'
        self.root.mkdir()
        self.db = Path(self.tmp.name)/'catalog.sqlite'

    def salary(self, name='slate.csv', date='10/20/2026'):
        path = self.root/name
        path.write_text('Position,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n'
                        f'WR,Example,123,FLEX,5000,SEA@DEN {date} 08:15PM ET,SEA,10\n'
                        f'WR,Example,124,CPT,7500,SEA@DEN {date} 08:15PM ET,SEA,10\n')
        return path

    def test_default_today_onward_and_all_dates(self):
        self.salary('old.csv', '10/18/2026')
        self.salary('today.csv', '10/19/2026')
        self.salary('future.csv')
        scan(self.db, self.root)
        self.assertEqual([r['date'] for r in rows(self.db,self.root,today='2026-10-19')], ['2026-10-19','2026-10-20'])
        self.assertEqual(len(rows(self.db,self.root,all_dates=True)),3)

    def test_restart_skips_hashing_and_parsing_unchanged_files(self):
        self.salary()
        scan(self.db,self.root)
        with patch('slate_catalog.read_players_csv',side_effect=AssertionError('reparsed')):
            result=scan(self.db,self.root)
        self.assertEqual(result,dict(checked=0,reused=1))

    def test_changed_file_reprocessed(self):
        path=self.salary();scan(self.db,self.root)
        path.write_text(path.read_text().replace('10/20/2026','10/21/2026'))
        self.assertEqual(scan(self.db,self.root)['checked'],1)
        self.assertEqual(rows(self.db,self.root,today='2026-10-19')[0]['date'],'2026-10-21')

    def test_duplicate_bytes_parsed_once_and_listed_once(self):
        path=self.salary();shutil.copyfile(path,self.root/'copy.csv')
        from slate_catalog import read_players_csv
        with patch('slate_catalog.read_players_csv',wraps=read_players_csv) as parse:
            scan(self.db,self.root)
        self.assertEqual(parse.call_count,1)
        self.assertEqual(len(rows(self.db,self.root,today='2026-10-19')),1)

    def test_removed_duplicate_leaves_available_copy(self):
        path=self.salary();shutil.copyfile(path,self.root/'copy.csv')
        scan(self.db,self.root);path.unlink();scan(self.db,self.root)
        self.assertTrue(rows(self.db,self.root,today='2026-10-19')[0]['path'].endswith('copy.csv'))

    def test_unknown_date_only_in_all_dates(self):
        self.salary(date='');scan(self.db,self.root)
        self.assertEqual(rows(self.db,self.root,today='2026-10-19'),[])
        self.assertEqual(rows(self.db,self.root,all_dates=True)[0]['date'],'')

    def test_invalid_csv_is_not_loadable_or_reparsed_unchanged(self):
        (self.root/'bad.csv').write_text('not a salary file\n')
        scan(self.db,self.root)
        self.assertEqual(rows(self.db,self.root,all_dates=True),[])
        self.assertEqual(scan(self.db,self.root)['reused'],1)

    def test_cancel_does_not_mark_known_files_absent(self):
        self.salary();scan(self.db,self.root)
        self.assertTrue(scan(self.db,self.root,cancelled=lambda:True)['cancelled'])
        self.assertEqual(len(rows(self.db,self.root,all_dates=True)),1)

    def test_folder_scope_does_not_leak_previous_folder(self):
        self.salary();scan(self.db,self.root)
        other=Path(self.tmp.name)/'other';other.mkdir()
        self.assertEqual(rows(self.db,other,all_dates=True),[])


if __name__=='__main__':unittest.main()
