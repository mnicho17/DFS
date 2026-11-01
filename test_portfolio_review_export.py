from test_environment import install
install()
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from PyQt5 import QtWidgets
from portfolio_review_export import snapshot_text,save_text
from winner_portfolio_ui import WinnerPortfoliosDialog
from winner_portfolio_review import review_winners
from test_winner_portfolio_review import WinnerPortfolioTests
from test_portfolio_risk_evidence import logical_db


class PortfolioExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def folder(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);root=Path(t.name)
        (root/'history').mkdir();return root
    def dialog(self):
        f=WinnerPortfolioTests.fixture(self);d=WinnerPortfoliosDialog(f.db);self.addCleanup(d.close)
        r=review_winners(f.db,'2026-09-22');d.load_result(r);return f,d,r

    def test_utf8_snapshot_scope_receipts_and_atomic_success(self):
        root=self.folder();target=root/'report.txt';target.write_bytes(b'old')
        text=snapshot_text('Completed overview '+chr(233),'Selected entrant','2026-10-31',(('contest','source-hash','salary-hash'),))
        save_text(target,text,root/'history'/'exports.sqlite')
        self.assertEqual(target.read_text(encoding='utf-8'),text)
        self.assertIn('SELECTED VIEW AT SAVE',text);self.assertIn('source-hash',text)
        self.assertIn('does not recheck current files',text)
        self.assertNotIn('SELECTED VIEW AT SAVE',snapshot_text('overview','overview','date'))
        with self.assertRaises(ValueError):snapshot_text('','anything','date')

    def test_source_and_application_paths_are_protected(self):
        import portfolio_review_export as module
        root=self.folder();db=root/'history'/'exports.sqlite';db.write_bytes(b'protected')
        targets=(db,root/'history'/'report.txt',Path(module.__file__).parent/'protected-export-test.txt',root/'salary.csv',root/'salary.csv:report.txt')
        for target in targets:
            with self.subTest(target=str(target)),self.assertRaises(ValueError):save_text(target,'report',db)
        self.assertEqual(db.read_bytes(),b'protected')

    def test_failed_atomic_write_preserves_existing_file_and_disallows_fallback(self):
        root=self.folder();target=root/'report.txt';target.write_bytes(b'previous')
        fake=Mock();fake.open.return_value=True;fake.write.return_value=1;fake.errorString.return_value='write failed'
        with patch('portfolio_review_export.QtCore.QSaveFile',return_value=fake):
            with self.assertRaises(OSError):save_text(target,'a full report',root/'history'/'exports.sqlite')
        fake.setDirectWriteFallback.assert_called_once_with(False);fake.cancelWriting.assert_called_once()
        fake.commit.assert_not_called();self.assertEqual(target.read_bytes(),b'previous')
        with patch('portfolio_review_export.MAX_TEXT_BYTES',1):
            with self.assertRaises(ValueError):save_text(root/'large.txt','large report',root/'history'/'exports.sqlite')
        self.assertFalse((root/'large.txt').exists())

    def test_save_is_disabled_until_completed_and_never_exports_pending(self):
        root=self.folder();d=WinnerPortfoliosDialog(root/'history'/'exports.sqlite');self.addCleanup(d.close)
        self.assertFalse(d.overview_button.isEnabled());d.show_overview();self.assertEqual(d.report.toPlainText(),'');self.assertFalse(d.save_button.isEnabled());d._pending={'private':'unfinished'}
        with patch.object(QtWidgets.QFileDialog,'getSaveFileName',side_effect=AssertionError('No incomplete export')):d.save_completed()

    def test_selected_export_is_frozen_before_save_dialog_and_does_not_reanalyze(self):
        f,d,r=self.dialog();root=self.folder();target=root/'selected.txt'
        parent=d.tree.topLevelItem(0);d.tree.setCurrentItem(parent.child(0));display=d.report.toPlainText()
        overview=d._completed_overview;before=logical_db(f.db)
        def choose(*args):
            d._completed_overview='NEW REVIEW';d.report.setPlainText('NEW DISPLAY');d._pending={'unfinished':True}
            return str(target),''
        with patch('winner_portfolio_ui.review_winners',side_effect=AssertionError('No reanalysis')),patch('data_paths.review_reports_directory',return_value=root),patch.object(QtWidgets.QFileDialog,'getSaveFileName',side_effect=choose):d.save_completed()
        text=target.read_text(encoding='utf-8')
        self.assertIn(overview,text);self.assertIn(display,text);self.assertNotIn('NEW REVIEW',text);self.assertNotIn('NEW DISPLAY',text)
        self.assertIn(r['evidence'][0]['source'],text);self.assertEqual(before,logical_db(f.db))

    def test_cancel_or_failure_retains_completed_evidence_for_retry(self):
        f,d,r=self.dialog();root=self.folder();overview=d._completed_overview
        with patch('data_paths.review_reports_directory',return_value=root),patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=('','')),patch('portfolio_review_export.save_text') as save:
            d.save_completed();save.assert_not_called()
        with patch('data_paths.review_reports_directory',return_value=root),patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(root/'retry.txt'),'')),patch('portfolio_review_export.save_text',side_effect=OSError('denied')):
            d.save_completed()
        self.assertIn('Could not save',d.status.text());self.assertEqual(d._completed_overview,overview);self.assertTrue(d.save_button.isEnabled())
        with patch('data_paths.review_reports_directory',return_value=root),patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(root/'retry.txt'),'')):d.save_completed()
        self.assertTrue((root/'retry.txt').exists());self.assertIn('Saved completed review',d.status.text())

    def test_failed_commit_cancels_without_modifying_existing_destination(self):
        root=self.folder();target=root/'report.txt';target.write_bytes(b'old')
        fake=Mock();fake.open.return_value=True;fake.write.return_value=6;fake.commit.return_value=False;fake.errorString.return_value='commit failed'
        with patch('portfolio_review_export.QtCore.QSaveFile',return_value=fake):
            with self.assertRaises(OSError):save_text(target,'report',root/'history'/'exports.sqlite')
        fake.cancelWriting.assert_called_once();self.assertEqual(target.read_bytes(),b'old')

    def test_full_overview_restores_captured_scope_preserving_search_and_pending(self):
        f,d,r=self.dialog();overview=d._completed_overview;before=logical_db(f.db)
        parent=d.tree.topLevelItem(0);entrant=parent.child(0)
        d.tree.setCurrentItem(entrant);self.assertNotEqual(d.report.toPlainText(),overview)
        d.find.setText(entrant.text(0));expanded=parent.isExpanded()
        hidden=[parent.child(i).isHidden() for i in range(parent.childCount())]
        d._pending={'unfinished':True};d.status.setText('Another review is running')
        with patch('winner_portfolio_ui.review_winners',side_effect=AssertionError('No reanalysis')),patch('winner_portfolio_ui.render_overview',side_effect=AssertionError('Use captured overview')):
            d.overview_button.click()
        self.assertEqual(d.report.toPlainText(),overview);self.assertIsNone(d.tree.currentItem())
        self.assertFalse(d.tree.selectedItems());self.assertEqual(d.find.text(),entrant.text(0))
        self.assertEqual(parent.isExpanded(),expanded)
        self.assertEqual([parent.child(i).isHidden() for i in range(parent.childCount())],hidden)
        self.assertIs(d.data,r);self.assertEqual(d._pending,{'unfinished':True})
        self.assertEqual(d.status.text(),'Another review is running');self.assertEqual(before,logical_db(f.db))
        root=self.folder();target=root/'overview.txt'
        with patch('data_paths.review_reports_directory',return_value=root),patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(target),'')):
            d.save_completed()
        text=target.read_text(encoding='utf-8')
        self.assertEqual(text.count(overview),1);self.assertNotIn('SELECTED VIEW AT SAVE',text)
        self.assertNotIn('unfinished',text)
