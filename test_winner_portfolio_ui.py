from test_environment import install
install()
import threading,time,unittest
from unittest.mock import patch
from PyQt5 import QtWidgets,QtCore
import test_winner_portfolio_review as fixtures
from winner_portfolio_review import review_winners
from winner_portfolio_ui import WinnerPortfoliosDialog,WinnerWorker

class WinnerPortfolioUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def fixture(self):return fixtures.WinnerPortfolioTests.fixture(self)
    def wait_retired(self,d):
        end=time.monotonic()+5
        while d._thread is not None and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
        self.assertIsNone(d._thread)
    def test_open_is_explicit_and_selection_displays_entire_portfolio(self):
        f=self.fixture()
        with patch('winner_portfolio_ui.review_winners',side_effect=AssertionError('No automatic work')):
            d=WinnerPortfoliosDialog(f.db);self.addCleanup(d.close);self.assertIsNone(d._thread)
        d.load_result(review_winners(f.db,'2026-09-22'))
        self.assertEqual(d.tree.topLevelItemCount(),1)
        parent=d.tree.topLevelItem(0);self.assertEqual(parent.childCount(),1)
        d.tree.setCurrentItem(parent.child(0));text=d.report.toPlainText()
        self.assertIn('Entries: 50',text);self.assertIn('rank-1 entries: 1',text);self.assertIn('including losing entries',text)
        d.find.setText('absent username');self.assertTrue(parent.isHidden())
        d.find.setText('winner');self.assertFalse(parent.isHidden())
    def test_cancelled_worker_suppresses_partial_result(self):
        worker=WinnerWorker('unused','2026-09-22','showdown','2026-01-01');seen=[];done=[]
        worker.result.connect(seen.append);worker.done.connect(lambda:done.append(True))
        def run(*args,**kwargs):worker.stop.set();return {'partial':True}
        with patch('winner_portfolio_ui.review_winners',side_effect=run):worker.run()
        self.assertEqual(seen,[]);self.assertEqual(done,[True])
    def test_close_cancels_and_waits_for_worker_and_retains_previous_completed_data(self):
        f=self.fixture();data=review_winners(f.db,'2026-09-22');entered=threading.Event()
        def run(path,through,cancelled,progress,**kwargs):
            entered.set()
            while not cancelled():time.sleep(.005)
            return {'partial':True}
        d=WinnerPortfoliosDialog(f.db);self.addCleanup(d.close);d.load_result(data);d.show()
        with patch('winner_portfolio_ui.review_winners',side_effect=run):
            d.start();end=time.monotonic()+5
            while not entered.is_set() and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
            self.assertTrue(entered.is_set());d.accept();self.assertIsNotNone(d._thread)
            d.close();self.assertIsNotNone(d._thread);self.assertTrue(d._closing);self.wait_retired(d)
        self.assertIs(d.data,data);self.assertIn('No partial review',d.status.text());self.assertFalse(d.isVisible())
    def test_workspace_action_pauses_import_watcher_and_releases_on_error(self):
        from main_window import ResultsLearningDialog
        owner=ResultsLearningDialog();self.addCleanup(owner.close)
        self.assertIsNotNone(owner.history_explore.findChild(QtWidgets.QPushButton,'reviewWinnerPortfolios'))
        def modal():self.assertEqual(owner.history_refresh._reports,1);raise RuntimeError('dialog failed')
        with patch('winner_portfolio_ui.WinnerPortfoliosDialog') as dialog:
            dialog.return_value.exec_.side_effect=modal
            with self.assertRaisesRegex(RuntimeError,'dialog failed'):owner.history_explore.open_winners()
        self.assertEqual(owner.history_refresh._reports,0)
        owner._import_thread=object()
        try:
            with patch('winner_portfolio_ui.WinnerPortfoliosDialog',side_effect=AssertionError('Active import')):owner.history_explore.open_winners()
            self.assertIn('active history task',owner.history_explore.status.text())
        finally:owner._import_thread=None

if __name__=='__main__':unittest.main()
