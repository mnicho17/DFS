"""Persisted pairing state, real SQLite/source verification, and Qt consumers."""
from test_environment import install, network_attempts
install()

from contextlib import closing
import csv
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PyQt5 import QtWidgets
import analysis_imports as imports
from test_analysis_imports import results_file, salary_file


class PairingFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'history.sqlite'
        self.results = self.root / 'results'
        self.salaries = self.root / 'salaries'
        self.results.mkdir(); self.salaries.mkdir()
        self.network_before = list(network_attempts)

    def tearDown(self):
        self.assertEqual(network_attempts, self.network_before)

    def seed(self, count=1, salaries=1):
        for i in range(count):
            results_file(self.results / f'contest-{i:02}.csv', entries=2, contest_id=str(i))
        for i in range(salaries):
            salary_file(self.salaries / f'salary-{i}.csv', offset=i * 100)
        result = imports.import_folders(str(self.results), str(self.salaries), db_path=str(self.db))
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['pairs_added'], 0)  # Missing result date requires explicit confirmation.
        return imports.pairing_state(self.db)

    def save(self, row):
        return imports.save_pair(row['hash'], row['candidates'][0]['hash'],
                                 db_path=str(self.db), confirm_date=True)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return '\n'.join(conn.iterdump())

    def report(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return '\n'.join(imports.report_lines(conn))


class PairingStateTests(PairingFixture):
    def test_missing_and_empty_database_are_read_only(self):
        for existing in (False, True):
            if existing:
                sqlite3.connect(self.db).close()
            state = imports.pairing_state(self.db)
            self.assertEqual(state['results_cataloged'], 0)
            self.assertEqual(state['saved_pairings'], 0)
            self.assertEqual(state['unpaired_results'], 0)
            self.assertEqual(state['warnings'], [])
            self.assertEqual(self.db.exists(), existing)
            if existing:
                self.assertEqual(self.dump(), 'BEGIN TRANSACTION;\nCOMMIT;')

    def test_results_without_salaries_have_no_match(self):
        state = self.seed(salaries=0)
        self.assertEqual(state['unpaired_results'], 1)
        self.assertEqual(state['results'][0]['status'], 'NO_COMPATIBLE_MATCH')

    def test_compatible_unsaved_is_ready_not_paired(self):
        state = self.seed()
        self.assertEqual((state['saved_pairings'], state['unpaired_results']), (0, 1))
        self.assertEqual(state['results'][0]['status'], 'READY_TO_PAIR')

    def test_save_refreshes_status_and_report(self):
        row = self.seed()['results'][0]
        self.assertIn('Results awaiting a salary match: 1', self.report())
        self.save(row)
        state = imports.pairing_state(self.db)
        self.assertEqual(state['results'][0]['status'], 'PAIRED')
        self.assertEqual(state['qualified_saved_pairings'], 1)
        self.assertIn('Results awaiting a salary match: 0', self.report())

    def test_three_results_share_one_salary(self):
        for row in self.seed(count=3)['results']:
            self.save(row)
        state = imports.pairing_state(self.db)
        self.assertEqual((state['saved_pairings'], state['salary_snapshots'], state['unpaired_results']), (3, 1, 0))
        self.assertEqual(len({r['salary_hash'] for r in state['results']}), 1)

    def test_twenty_four_results_sixteen_then_twenty_four_pairs(self):
        rows = self.seed(count=24)['results']
        for row in rows[:16]:
            self.save(row)
        state = imports.pairing_state(self.db)
        self.assertEqual((state['results_cataloged'], state['saved_pairings'], state['unpaired_results']), (24, 16, 8))
        self.assertEqual(sum(r['status'] == 'READY_TO_PAIR' for r in state['results']), 8)
        self.assertIn('Results awaiting a salary match: 8', self.report())
        for row in rows[16:]:
            self.save(row)
        state = imports.pairing_state(self.db)
        self.assertEqual((state['results_cataloged'], state['saved_pairings'], state['unpaired_results']), (24, 24, 0))
        self.assertEqual(state['warnings'], [])
        self.assertIn('Results awaiting a salary match: 0', self.report())

    def test_changed_snapshot_invalid_pair_stays_persisted(self):
        self.save(self.seed()['results'][0])
        with closing(sqlite3.connect(self.db)) as conn:
            salary = next(s for s in imports._sources(conn) if s['kind'] == 'salary')
        Path(salary['snapshot']).write_bytes(b'changed snapshot')
        before = self.dump()
        state = imports.pairing_state(self.db)
        self.assertEqual((state['qualified_saved_pairings'], state['invalid_saved_pairings']), (0, 1))
        self.assertEqual(state['results'][0]['status'], 'INVALID_SAVED_PAIR')
        self.assertIn('changed', state['results'][0]['saved_pair']['reason'])
        self.assertEqual(state['unpaired_results'], 0)
        self.assertEqual(self.dump(), before)
        self.assertIn('Invalid saved pairings: 1', self.report())

    def test_current_qualification_invalidates_without_retargeting(self):
        row = self.seed(salaries=2)['results'][0]
        self.save(row)
        with patch.object(imports, 'qualify_pair', return_value=dict(compatible=False, automatic=False, reason='Slate dates conflict.')):
            state = imports.pairing_state(self.db)
        self.assertEqual(state['results'][0]['status'], 'INVALID_SAVED_PAIR')
        self.assertEqual(state['results'][0]['salary_hash'], row['candidates'][0]['hash'])
        self.assertEqual(state['results'][0]['saved_pair']['reason'], 'Slate dates conflict.')

    def test_missing_source_is_invalid_and_does_not_leak_path(self):
        self.save(self.seed()['results'][0])
        with closing(sqlite3.connect(self.db)) as conn:
            for source in imports._sources(conn):
                if source['kind'] == 'results':
                    Path(source['snapshot']).unlink()
        state = imports.pairing_state(self.db)
        self.assertEqual(state['invalid_saved_pairings'], 1)
        self.assertNotIn(str(self.root), state['results'][0]['saved_pair']['reason'])

    def test_orphan_result_exposes_count_invariant_warning(self):
        row = self.seed()['results'][0]
        self.save(row)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DELETE FROM analysis_sources WHERE hash=?', (row['hash'],))
        before = self.dump()
        state = imports.pairing_state(self.db)
        self.assertEqual((state['orphaned_pairings'], state['invalid_saved_pairings']), (1, 1))
        self.assertTrue(state['warnings'])
        self.assertIn('Pairing counts need review', self.report())
        self.assertNotIn('Results awaiting a salary match: -1', self.report())
        self.assertEqual(self.dump(), before)

    def test_orphan_salary_is_invalid_with_saved_revision(self):
        row = self.seed()['results'][0]
        self.save(row)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DELETE FROM analysis_sources WHERE hash=?', (row['candidates'][0]['hash'],))
        state = imports.pairing_state(self.db)
        self.assertEqual(state['orphaned_pairings'], 1)
        self.assertEqual(state['results'][0]['status'], 'INVALID_SAVED_PAIR')
        self.assertEqual(state['results'][0]['saved_pair']['hash'], row['candidates'][0]['hash'])

    def test_coverage_counts_readable_and_unreadable(self):
        path = results_file(self.results / 'contest.csv', entries=3)
        with path.open('a', newline='', encoding='utf-8') as handle:
            csv.writer(handle).writerow([4, 2000, 'Example_User', 0, 'LOCKED', 'one'])
        state = self.seed(count=0, salaries=0)
        row = state['results'][0]
        self.assertEqual((row['readable'], row['entries'], row['unreadable'], row['readable_pct']), (3, 4, 1, 75.0))

    def test_status_is_deterministic_and_never_writes_sources_or_history(self):
        self.seed(count=3)
        before = self.dump()
        files = {p: p.read_bytes() for root in (self.results, self.salaries, self.root / 'analysis_sources') for p in root.rglob('*') if p.is_file()}
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('PRAGMA query_only=ON')
            first = imports.pairing_state(conn=conn)
            second = imports.pairing_state(conn=conn)
        self.assertEqual(first, second)
        self.assertEqual(before, self.dump())
        self.assertEqual(files, {p: p.read_bytes() for p in files})

    def test_cancel_status_and_save_preserve_pairings(self):
        row = self.seed()['results'][0]
        before = self.dump()
        with self.assertRaises(imports.ImportCancelled):
            imports.pairing_state(self.db, cancelled=lambda: True)
        with self.assertRaises(imports.ImportCancelled):
            imports.save_pair(row['hash'], row['candidates'][0]['hash'], db_path=self.db, confirm_date=True, cancelled=lambda: True)
        self.assertEqual(before, self.dump())

    def test_mismatched_database_is_diagnosed(self):
        self.seed()
        with closing(sqlite3.connect(self.db)) as conn:
            state = imports.pairing_state(self.root / 'alternate.sqlite', conn=conn)
        self.assertTrue(any('database identity' in warning.lower() for warning in state['warnings']))
        self.assertEqual(state['database_path'], str(self.db.resolve()))
        self.assertNotIn(str(self.root), '\n'.join(state['warnings']))

    def test_cancellation_during_source_verification_is_read_only(self):
        row = self.seed()['results'][0]
        self.save(row)
        before = self.dump()
        ticks = []
        def cancelled():
            ticks.append(True)
            return len(ticks) >= 5
        with self.assertRaises(imports.ImportCancelled):
            imports.pairing_state(self.db, cancelled=cancelled)
        self.assertGreaterEqual(len(ticks), 5)
        self.assertEqual(before, self.dump())


class PairingDialogTests(PairingFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def dialog(self):
        from analysis_imports_ui import SalaryMatchesDialog
        dialog = SalaryMatchesDialog(db_path=self.db)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_paired_view_is_explicit_and_hides_unrelated_rejections(self):
        row = self.seed()['results'][0]
        self.save(row)
        salary_file(self.salaries / 'unrelated.csv', fmt='classic')
        imports.import_folders(str(self.results), str(self.salaries), db_path=self.db)
        dialog = self.dialog()
        text = dialog.details.toPlainText()
        for expected in ('Status: PAIRED', 'Salary file:', 'Revision:', 'Qualification:', 'Existing pairing preserved.', 'Readable result rosters: 2 / 2 (100.0%)'):
            self.assertIn(expected, text)
        self.assertNotIn('unrelated.csv', text)
        self.assertIn('unrelated.csv', dialog.diagnostics.toPlainText())
        self.assertTrue(dialog.diagnostics.isHidden())
        self.assertFalse(dialog.salaries.isEnabled())
        self.assertFalse(dialog.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled())

    def test_ready_view_allows_save_and_reopens_paired(self):
        self.seed()
        dialog = self.dialog()
        self.assertIn('Status: READY_TO_PAIR', dialog.details.toPlainText())
        self.assertTrue(dialog.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled())
        dialog.confirm_date.setChecked(True)
        dialog._select()
        r, s, confirm = dialog.selection
        imports.save_pair(r, s, db_path=self.db, confirm_date=confirm)
        reopened = self.dialog()
        self.assertIn('Status: PAIRED', reopened.details.toPlainText())
        self.assertEqual(reopened.state['unpaired_results'], 0)

    def test_invalid_view_preserves_saved_revision_and_disables_save(self):
        row = self.seed()['results'][0]
        self.save(row)
        with closing(sqlite3.connect(self.db)) as conn:
            Path(imports._sources(conn)[0]['snapshot']).write_bytes(b'changed')
        dialog = self.dialog()
        text = dialog.details.toPlainText()
        self.assertIn('Status: INVALID_SAVED_PAIR', text)
        self.assertIn(row['candidates'][0]['hash'], text)
        self.assertIn('no longer qualifies', text)
        self.assertFalse(dialog.buttons.button(QtWidgets.QDialogButtonBox.Save).isEnabled())

    def test_no_match_view_explains_rejections(self):
        self.seed()
        with patch.object(imports, 'qualify_pair', return_value=dict(compatible=False, automatic=False, reason='Roster format differs.')):
            dialog = self.dialog()
        self.assertIn('Status: NO_COMPATIBLE_MATCH', dialog.details.toPlainText())
        self.assertIn('Roster format differs.', dialog.details.toPlainText())

    def test_report_and_dialog_use_same_backend_state(self):
        from learning_db import generate_learning_report
        self.seed(count=3)
        report = generate_learning_report(db_path=str(self.db))
        dialog = self.dialog()
        self.assertEqual(report['pairing_state'], dialog.state)
        self.assertEqual(report['pairing_state']['unpaired_results'], 3)

    def test_worker_save_refreshes_report_on_pinned_database(self):
        from analysis_imports_ui import CombinedImportWorker
        row = self.seed()['results'][0]
        worker = CombinedImportWorker(pair=(row['hash'], row['candidates'][0]['hash'], True), db_path=self.db)
        results, errors = [], []
        worker.finished.connect(results.append); worker.error.connect(errors.append)
        with patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': str(self.root / 'different-profile')}):
            worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(results[0]['report']['pairing_state']['unpaired_results'], 0)
        self.assertEqual(results[0]['report']['pairing_state'], self.dialog().state)
        self.assertFalse((self.root / 'different-profile').exists())

    def test_main_window_save_and_refresh_keep_same_database_and_counts(self):
        from main_window import ResultsLearningDialog
        from analysis_imports_ui import CombinedImportWorker, SalaryMatchesDialog
        row = self.seed()['results'][0]
        with patch('learning_db.history_db_path', return_value=str(self.db)):
            parent = ResultsLearningDialog()
        self.addCleanup(parent.deleteLater)
        self.assertIn('Results awaiting a salary match: 1', parent.report.toPlainText())
        worker = CombinedImportWorker(pair=(row['hash'], row['candidates'][0]['hash'], True), db_path=parent.db_path)
        worker.finished.connect(parent._on_combined_import_finished)
        with patch.object(QtWidgets.QMessageBox, 'information'):
            worker.run()
        self.assertIn('Results awaiting a salary match: 0', parent.report.toPlainText())
        with patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': str(self.root / 'wrong-profile')}):
            parent.refresh_report()
            with patch('analysis_imports_ui.SalaryMatchesDialog', wraps=SalaryMatchesDialog) as factory, patch.object(SalaryMatchesDialog, 'exec_', return_value=QtWidgets.QDialog.Rejected):
                parent.review_salary_matches()
            self.assertEqual(factory.call_args.kwargs['db_path'], str(self.db))
        self.assertIn('Results awaiting a salary match: 0', parent.report.toPlainText())
        self.assertFalse((self.root / 'wrong-profile').exists())


if __name__ == '__main__':
    unittest.main()
