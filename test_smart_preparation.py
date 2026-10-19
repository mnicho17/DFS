from test_environment import install
install()
import datetime as dt
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets
from slate_catalog import scan, rows
from smart_preparation import prepare_one
from test_showdown_library import players


class SmartPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.folder=self.root/'salaries';self.folder.mkdir()
        self.db=self.root/'catalog.sqlite';self.output=self.root/'candidates'
        self.path=self.folder/'slate.csv'
        self.path.write_text('Position,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev\n'
            'WR,Example,123,FLEX,5000,SEA@DEN 10/20/2026 08:15PM ET,SEA\n'
            'WR,Example,124,CPT,7500,SEA@DEN 10/20/2026 08:15PM ET,SEA\n')
        scan(self.db,self.folder)
        self.today=dt.date(2026,10,19)
        self.ps=players()
        for i,p in enumerate(self.ps):
            p.update(FlexSalary=6000+i*100,CptSalary=9000+i*150,GameInfo='SEA@DEN 10/20/2026 08:15PM ET')
        from csv_loading import CsvLoadWorker
        def prepared(worker):worker.finished.emit(dict(players=copy.deepcopy(self.ps),live_summary={}))
        mock=patch.object(CsvLoadWorker,'run',prepared);mock.start();self.addCleanup(mock.stop)
        mock=patch('candidate_library.code_id',return_value='synthetic-code');mock.start();self.addCleanup(mock.stop)
        self.own=dict(total={'example':100},cpt={},flex={})
        mock=patch('main_window.OwnershipSimWorker._simulate',return_value=self.own)
        self.estimate=mock.start();self.addCleanup(mock.stop)

    def run_job(self,**kw):
        return prepare_one(self.db,self.folder,self.output,kw.pop('cancelled',lambda:False),lambda text:None,now=self.today,seconds=.1,**kw)

    def test_real_library_partial_checkpoint_resumes_without_repeating_ownership(self):
        from candidate_library import metadata
        result=self.run_job()
        self.assertEqual(result['state'],'Queued')
        self.assertTrue(Path(result['library']).exists())
        count=metadata(result['library'])['count']
        second=self.run_job()
        self.assertEqual(self.estimate.call_count,1)
        self.assertGreaterEqual(metadata(second['library'])['count'],count)
        self.assertEqual(len(list((self.output/'discovered').glob('*.json'))),1)

    def test_completed_target_is_skipped_on_restart(self):
        with patch('smart_preparation.TARGET',1):
            result=self.run_job()
            self.assertEqual(result['state'],'Prepared candidates')
            second=self.run_job()
        self.assertNotIn('library',second)
        self.assertEqual(self.estimate.call_count,1)

    def test_cancellation_before_start_does_not_estimate_or_create_library(self):
        result=self.run_job(cancelled=lambda:True)
        self.assertTrue(result['cancelled']);self.estimate.assert_not_called()
        self.assertFalse(list(self.output.glob('*.dfslib')))

    def test_missing_or_changed_source_is_never_prepared_from_stale_catalog(self):
        self.path.write_text(self.path.read_text()+'\n')
        result=self.run_job()
        self.assertIn('changed',result['preparation'])
        self.estimate.assert_not_called()
        self.assertFalse(list(self.output.glob('*.dfslib')))

    def test_error_requires_attention_instead_of_repeated_ownership_work(self):
        self.estimate.side_effect=ValueError('No valid ownership entries')
        self.assertIn('error',self.run_job())
        self.assertNotIn('error',self.run_job())
        self.assertEqual(self.estimate.call_count,1)

    def test_more_than_seven_days_ahead_is_not_queued(self):
        self.today=dt.date(2026,10,1)
        self.assertNotIn('library',self.run_job())
        self.estimate.assert_not_called()

    def test_shared_storage_limit_does_not_delete_existing_data(self):
        self.output.mkdir();existing=self.output/'manual-library';existing.write_bytes(b'keep')
        with patch('smart_preparation.BUDGET',1):result=self.run_job()
        self.assertIn('budget',result['preparation'])
        self.assertEqual(existing.read_bytes(),b'keep')
        self.estimate.assert_not_called()


if __name__=='__main__':unittest.main()
