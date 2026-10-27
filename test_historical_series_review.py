from test_environment import install
install()
import threading,unittest
from pathlib import Path
from unittest.mock import patch
import analysis_imports as ai
import test_prior_portfolio_followup as fixtures
import historical_series_review as series
from winner_portfolio_review import review_winners
from test_portfolio_risk_evidence import logical_db,source_bytes

class HistoricalSeriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def fixture(self):
        helper=fixtures.PriorPortfolioFollowupTests('test_invalid_evaluation_range_and_cancellation');self.addCleanup(helper.doCleanups)
        f=helper.history();helper.day(f,'2026-09-04',4,loses=True);helper.day(f,'2026-09-05',5,loses=True);return f
    def review(self,f,**kwargs):return series.review_series(f.db,'2026-09-05',followup_from='2026-09-04',**kwargs)
    def test_bounded_batches_match_single_review_and_leave_originals_unchanged(self):
        f=self.fixture();before=logical_db(f.db);sources=source_bytes(f.root)
        r=self.review(f,evaluation_windows=[('2026-09-04','2026-09-04'),('2026-09-05','2026-09-05')])
        single=review_winners(f.db,'2026-09-05',review_mode='prior_qualified',followup_from='2026-09-04')
        self.assertEqual(r['contests'],single['contests']);self.assertEqual(len(r['evaluation_windows']),2)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
    def test_index_or_mapping_change_prevents_series_publication(self):
        f=self.fixture();original=series.review_winners;calls=[]
        def changed(*args,**kwargs):
            r=original(*args,**kwargs);calls.append(True);f.change("UPDATE opponent_contests SET name=name||' changed'");return r
        with patch('historical_series_review.review_winners',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'index or salary mappings changed'):self.review(f)
        self.assertEqual(len(calls),1)
    def test_raw_source_change_during_final_verification_is_rejected(self):
        f=self.fixture();done=[]
        path=Path(f.query("SELECT snapshot FROM analysis_sources WHERE kind='salary' ORDER BY hash LIMIT 1")[0][0])
        # Change all indexed salary originals at the final stage so the chosen
        # hash ordering cannot accidentally target an unused seed fixture.
        paths=[Path(r[0]) for r in f.query('SELECT snapshot FROM analysis_sources WHERE hash IN (SELECT salary_hash FROM opponent_contests)')]
        def progress(text):
            if text.startswith('Final series source verification') and not done:
                for p in paths:p.write_bytes(p.read_bytes()+b'\n')
                done.append(True)
        with self.assertRaises(ValueError):self.review(f,progress=progress)
    def test_cancellation_and_series_limit_never_return_partial_result(self):
        f=self.fixture();stop=threading.Event()
        def progress(text):
            if 'Final source verification' in text:stop.set()
        with self.assertRaises(ai.ImportCancelled):self.review(f,cancelled=stop.is_set,progress=progress)
        with patch('historical_series_review.MAX_SERIES_ENTRIES',1):
            with self.assertRaises(series.ReviewLimitError):self.review(f)
    def test_invalid_windows_and_batch_limits_fail_before_running_reviews(self):
        f=self.fixture()
        with patch('historical_series_review.review_winners',side_effect=AssertionError('No review')):
            with self.assertRaises(ValueError):self.review(f,evaluation_windows=[('2026-09-05','2026-09-05'),('2026-09-04','2026-09-04')])
            with patch('historical_series_review.MAX_BATCHES',0):
                with self.assertRaises(series.ReviewLimitError):self.review(f)
    def test_series_worker_captures_mode_and_dates_without_automatic_work(self):
        from winner_portfolio_ui import WinnerWorker
        w=WinnerWorker('unused','2026-09-05','showdown','2026-08-01','prior_series','2026-09-04');seen=[];w.result.connect(seen.append)
        with patch('historical_series_review.review_series',return_value={'done':True}) as run:w.run()
        self.assertEqual(run.call_args.kwargs['followup_from'],'2026-09-04');self.assertEqual(seen,[{'done':True}])

    def test_completed_batch_cancellation_does_not_publish_a_partial_series(self):
        f=self.fixture();stop=threading.Event();original=series.review_winners;calls=[]
        def completed(*args,**kwargs):
            r=original(*args,**kwargs);calls.append(True);stop.set();return r
        with patch('historical_series_review.review_winners',side_effect=completed):
            with self.assertRaises(ai.ImportCancelled):self.review(f,cancelled=stop.is_set,evaluation_windows=[('2026-09-04','2026-09-04'),('2026-09-05','2026-09-05')])
        self.assertEqual(len(calls),1)
    def test_profile_limit_is_independent_of_per_batch_entry_limit(self):
        f=self.fixture()
        with patch('historical_series_review.MAX_SERIES_PROFILES',1):
            with self.assertRaises(series.ReviewLimitError):self.review(f)
    def test_broader_mode_is_explicit_and_does_not_start_a_task_on_selection(self):
        from PyQt5 import QtWidgets
        from winner_portfolio_ui import WinnerPortfoliosDialog
        f=self.fixture()
        with patch('historical_series_review.review_series',side_effect=AssertionError('No automatic work')):
            d=WinnerPortfoliosDialog(f.db);self.addCleanup(d.close)
            index=d.mode.findData('prior_series');self.assertGreaterEqual(index,0);d.mode.setCurrentIndex(index)
            self.assertFalse(d.followup_from.isHidden());self.assertIsNone(d._thread)

    def test_unindexed_history_has_an_actionable_message(self):
        helper=fixtures.PriorPortfolioFollowupTests('test_invalid_evaluation_range_and_cancellation');self.addCleanup(helper.doCleanups)
        f=helper.fixture()
        with self.assertRaisesRegex(ValueError,'Index mapped results in Data and Learning first'):
            self.review(f)

if __name__=='__main__':unittest.main()
