"""RL-01 real SQLite/Qt/ZIP boundaries using only disposable synthetic data."""
from __future__ import annotations

from test_environment import install, network_attempts
install()  # Before any DFS/Qt imports.

import copy
from contextlib import contextmanager
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import tracemalloc
import unittest
from unittest import mock
import zipfile

from PyQt5 import QtCore, QtWidgets
import review_report as rr
from review_report_ui import ReviewReportDialog
from main_window import ResultsLearningDialog
from learning_db import init_historical_import_tables


def raw(**kwargs):
    return {'entry_fee': '10', 'winnings': '20', 'currency': 'USD',
            'actual_points': '100', 'contest type': 'classic', 'rank': '1', 'field_size': '100', **kwargs}


@contextmanager
def connection(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class ReviewReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rl01-')
        self.root = Path(self.temp.name)
        self.db = self.root / 'history' / 'exports.sqlite'
        self.diag = self.root / 'history' / 'build-diagnostics.json'
        self.env = mock.patch.dict(os.environ, {'DFS_OPTIMIZER_DATA_DIR': str(self.root)})
        self.env.start()
        self.dialogs = []
        self.network_before = list(network_attempts)

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            self.drain(dialog)
        self.app.processEvents()
        self.assertEqual(self.network_before, network_attempts)
        self.env.stop()
        self.temp.cleanup()

    def seed(self, rows=None):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db)
        init_historical_import_tables(conn)
        conn.execute("INSERT INTO exports(export_id,created_at,sport,contest_type,app_version,build_style,salary_strategy) VALUES ('e','2026-09-10T01:00:00-04:00','NFL','classic','1.21.3','Balanced','Near Cap')")
        conn.execute("INSERT INTO lineups(lineup_id,export_id,lineup_index,roster_ids_json) VALUES ('l','e',1,'[12345]')")
        conn.execute("INSERT INTO lineup_players(lineup_player_id,lineup_id,slot,name,player_id) VALUES ('p','l','CPT','PRIVATE_PLAYER_RL01','12345')")
        conn.execute("INSERT INTO historical_imports(import_id,created_at,notes) VALUES ('i','2026-09-11T00:00:00Z','ok')")
        for i, value in enumerate(rows if rows is not None else [raw()]):
            self.insert(conn, i, value)
        conn.commit()
        conn.close()

    def insert(self, conn, i, value, *, day='2026-09-10', sport='NFL', match='l'):
        conn.execute('INSERT INTO historical_results(result_id,import_id,sport,slate_date,contest_name,raw_json,matched_lineup_id,match_method,roi,entry_fee,winnings) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (str(i), 'i', sport, day, 'PRIVATE_CONTEST', json.dumps(value), match, 'player_names', -50, 50, None))

    def capture(self, options=rr.Options(), **kwargs):
        return rr.capture(options, db_path=self.db, diagnostic_path=self.diag, **kwargs)

    def ui_zip(self, details=False):
        d = self.dialog()
        d.detail.setChecked(details)
        d.generate.click()
        self.drain(d)
        self.assertIsNotNone(d._report)
        target = self.root / 'ui-review.zip'
        with mock.patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
            d.save.click()
            self.drain(d)
        with zipfile.ZipFile(target) as z:
            self.assertEqual(z.read('summary.md').decode(), d.preview.toPlainText())
            self.assertEqual(z.read('evidence.json').decode(), d.evidence_preview.toPlainText())
            if details:
                self.assertEqual(z.read('lineups.csv').decode().replace('\r\n', '\n'), d.detail_preview.toPlainText())
            self.assertIsNone(z.testzip())
        return d._report

    def dialog(self):
        d = ReviewReportDialog(db_path=self.db, diagnostic_path=self.diag)
        self.dialogs.append(d)
        return d

    def drain(self, dialog, timeout=10):
        deadline = time.monotonic() + timeout
        while getattr(dialog, '_job', None) is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.app.processEvents()
        self.assertIsNone(getattr(dialog, '_job', None), 'Report worker failed to retire')

    def dump(self):
        with connection(self.db) as conn:
            return '\n'.join(conn.iterdump())

    def test_missing_empty_and_legacy_dialog_boundary(self):
        report = self.capture()
        self.assertEqual(report.data['database']['state'], 'missing')
        self.assertFalse(self.db.parent.exists())
        parent = ResultsLearningDialog()
        self.dialogs.append(parent)
        self.assertTrue(self.db.exists())  # Explicit preexisting legacy behavior.
        self.assertIsNotNone(parent.findChild(QtWidgets.QPushButton, 'exportReviewReportButton'))
        before = self.dump()
        d = self.dialog()
        d.generate.click()
        self.drain(d)
        self.assertIsNotNone(d._report)
        self.assertEqual(before, self.dump())

    def test_real_ui_preview_zip_no_writes_and_frozen_retry(self):
        self.seed()
        before = self.dump()
        d = self.dialog()
        d.generate.click()
        self.drain(d)
        captured = d._report.evidence
        self.assertEqual(d.preview.toPlainText(), d._report.summary())
        target = self.root / 'review.zip'
        with mock.patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
            d.save.click()
            self.drain(d)
        with zipfile.ZipFile(target) as z:
            self.assertEqual(set(z.namelist()), {'summary.md', 'evidence.json'})
            self.assertEqual(z.read('evidence.json'), captured)
            self.assertEqual(z.read('summary.md').decode(), d.preview.toPlainText())
        self.assertEqual(before, self.dump())
        with connection(self.db) as conn:
            self.insert(conn, 1, raw(winnings='0'))
        rr.publish(d._report, target)
        with zipfile.ZipFile(target) as z:
            self.assertEqual(z.read('evidence.json'), captured)
        old_id = d._report.data['report_id']
        d.generate.click()
        self.drain(d)
        self.assertNotEqual(old_id, d._report.data['report_id'])
        self.assertEqual(d._report.data['database']['coverage']['selected_results'], 2)

    def test_committed_wal_snapshot_under_concurrent_writer(self):
        self.seed()
        writer = sqlite3.connect(self.db)
        writer.execute('PRAGMA journal_mode=WAL')
        self.insert(writer, 1, raw())
        writer.commit()
        changed = []
        def progress(_):
            if not changed:
                self.insert(writer, 2, raw())
                writer.commit()
                changed.append(True)
        report = self.capture(progress=progress)
        self.assertEqual(report.data['database']['coverage']['selected_results'], 2)
        self.assertEqual(self.capture().data['database']['coverage']['selected_results'], 3)
        writer.close()

    def test_filters_dates_formats_and_timezone_basis(self):
        self.seed([raw(), raw(**{'contest type': 'showdown'}), raw(**{'contest type': ''})])
        with connection(self.db) as conn:
            conn.execute("UPDATE historical_results SET slate_date='not a date' WHERE result_id='2'")
            self.insert(conn, 3, raw(), day='2026-09-10T23:30:00-04:00')
            self.insert(conn, 4, raw(), day='2026-09-11T00:30:00+04:00')
            self.insert(conn, 5, raw(), sport='NBA')
        opts = rr.Options(start='2026-09-10', end='2026-09-10', sport='NFL', kind='classic')
        d = self.capture(opts).data['database']
        self.assertEqual(d['coverage']['selected_results'], 2)
        self.assertEqual(d['filter_exclusions']['format_unknown'], 1)
        all_dates = self.capture().data['database']['coverage']
        self.assertEqual(all_dates['unknown_date'], 1)
        self.assertIsNone(rr.recorded_date('09/10/2026'))
        with self.assertRaises(ValueError):
            rr.Options(start='2026-09-11', end='2026-09-10')

    def test_source_error_and_partial_schema_states(self):
        self.db.parent.mkdir()
        self.db.write_bytes(b'not SQLite')
        self.assertEqual(self.capture().data['database']['state'], 'unavailable_or_invalid')
        self.db.unlink()
        with connection(self.db) as conn:
            conn.execute('CREATE TABLE historical_results(result_id TEXT)')
        report = self.capture()
        self.assertEqual(report.data['database']['sources']['historical_results']['state'], 'unsupported_schema')
        self.assertIn('unsupported_schema', report.summary())
        with mock.patch('review_report.sqlite3.connect', side_effect=sqlite3.OperationalError('PRIVATE_PATH')):
            self.assertNotIn('PRIVATE_PATH', self.capture().evidence.decode())

    def test_cash_common_cohort_zero_missing_and_legacy_preservation(self):
        self.seed([raw(entry_fee='10', winnings='20'), raw(entry_fee='90', winnings='0'), raw(entry_fee='50', winnings='')])
        before = self.dump()
        d = self.capture().data['database']
        values = d['groups'][0]['values']
        for key, value in [('paid_fees', 100), ('paid_winnings', 20), ('paid_net', -80), ('paid_roi_pct', -80), ('known_fees_subtotal', 150)]:
            self.assertEqual(values[key]['value'], value)
        self.assertEqual(values['paid_roi_pct']['qualified_count'], 2)
        self.assertEqual(values['paid_roi_pct']['target_count'], 3)
        self.assertIsNone(values['whole_cohort_net']['value'])
        self.assertIsNone(values['whole_cohort_roi_pct']['value'])
        self.assertEqual(before, self.dump())
        for invalid in (None, '', '  ', 'bad', 'NaN', 'Infinity', '1e9999', True, {}, -1, '(5)'):
            with self.subTest(invalid=invalid):
                result = rr.cash(raw(winnings=invalid))
                self.assertIsNone(result['net'])
        self.assertEqual(rr.cash(raw(winnings='0'))['net'], -10)

    def test_free_prizes_currency_and_cash_rank_separation(self):
        self.seed([raw(entry_fee='0', winnings='5'), raw(winnings='5', rank='1')])
        values = self.capture().data['database']['groups'][0]['values']
        self.assertEqual(values['zero_fee_prizes']['value'], 5)
        self.assertEqual(values['paid_net']['value'], -5)
        self.assertIsNone(values['whole_cohort_roi_pct']['value'])
        for payload in (raw(currency=''), raw(**{'entry fee currency': 'USD', 'winnings currency': 'CAD'}), raw(**{'prize type': 'ticket'})):
            self.assertIsNone(rr.cash(payload)['net'])
        no_money = raw(rank='1')
        no_money.pop('winnings')
        self.assertIsNone(rr.cash(no_money)['net'])

    def test_ownership_and_forecasts_do_not_promote_legacy_evidence(self):
        self.seed()
        with connection(self.db) as conn:
            conn.execute('UPDATE lineups SET sim_edge=80, sim_scenarios=1000, sim_cash_rate=0, avg_ownership=0')
        report = self.capture()
        self.assertIn('Legacy ownership basis and coverage are unverified', report.summary())
        self.assertIn('Forecast comparison unavailable', report.summary())
        self.assertNotIn('low-ownership', json.dumps(report.data['database']))
        # No claiming a simulation or matching role based on legacy count/Edge.
        self.assertNotIn('sim_matched_rows', report.data['database'])

    def test_legacy_match_and_settings_are_not_newest_forecast(self):
        self.seed([raw(), raw(**{'contest type': 'showdown'})])
        with connection(self.db) as conn:
            conn.execute("UPDATE historical_results SET slate_date='2026-08-01' WHERE result_id='1'")
            conn.execute("UPDATE exports SET app_version='', build_style='PRIVATE_ACCOUNT', own_mode='PRIVATE_SECRET'")
        report = self.capture(rr.Options(details=True))
        db = report.data['database']
        self.assertEqual(db['coverage']['recorded_matches'], 2)
        self.assertTrue(all(r['association'] == 'legacy_unverified' for r in db['details']))
        self.assertIsNone(db['settings'][0]['recorded_app_version'])
        self.assertEqual(db['settings'][0]['build_style'], 'unknown')
        self.assertEqual(db['details'][1]['recorded_export_slots'][0]['slot'], 'CPT')

    def test_diagnostics_states_and_application_distinction(self):
        self.seed()
        self.assertEqual(self.capture().data['diagnostics']['state'], 'missing')
        for contents in ('bad', '[]', '{"records":{}}'):
            self.diag.write_text(contents)
            self.assertEqual(self.capture().data['diagnostics']['state'], 'invalid')
        records = [{'sport': 'NFL', 'contest_type': 'classic', 'created_at': '2026-09-10T00:00:00Z',
                    'status': 'completed', 'application': {'status': 'not_applied', 'reason': 'cancelled'},
                    'lineup_details': [{'secret': 'PRIVATE_PLAYER'}], 'timing': {'total_seconds': 1.5}},
                   {'status': 'cancelled', 'application': {'status': 'applied', 'reason': 'applied'}}]
        self.diag.write_text(json.dumps({'records': records}))
        diag = self.capture().data['diagnostics']
        self.assertEqual(diag['records'][0]['computation'], 'completed')
        self.assertEqual(diag['records'][0]['application'], 'not_applied')
        self.assertFalse(diag['complete_history'])
        self.assertNotIn('PRIVATE_PLAYER', json.dumps(diag))
        with mock.patch.object(rr, 'MAX_DIAGNOSTIC_BYTES', 2):
            self.assertEqual(self.capture().data['diagnostics']['state'], 'size_limit')

    def test_default_privacy_and_opt_in_csv(self):
        canaries = ('PRIVATE_ACCOUNT', 'PRIVATE_ENTRY', 'PRIVATE_PLAYER_RL01', 'PRIVATE_CONTEST', 'FAKE_SECRET_RL01_CANARY', 'PRIVATE_USER')
        self.seed([raw(**{'entry_name': canaries[1], 'account': canaries[0],
                         'path': r'C:\Users\PRIVATE_USER\secret.csv', 'secret': canaries[4]})])
        report = self.ui_zip()
        target = self.root / 'private-check.zip'
        rr.publish(report, target)
        with zipfile.ZipFile(target) as z:
            for info in z.infolist():
                text = z.read(info.filename).decode()
                for canary in canaries:
                    self.assertNotIn(canary, text)
                self.assertNotIn('Users', info.filename)
        detail = self.ui_zip(details=True)
        rr.publish(detail, target)
        with zipfile.ZipFile(target) as z:
            self.assertIn('lineups.csv', z.namelist())
            rows = list(csv.DictReader(io.StringIO(z.read('lineups.csv').decode())))
            self.assertEqual(rows[0]['slot'], 'CPT')
            self.assertEqual(rows[0]['association'], 'legacy_unverified')
            self.assertEqual(rows[0]['player_id'], '12345')
            for canary in (canaries[0], canaries[1], canaries[4], canaries[5]):
                self.assertNotIn(canary, z.read('evidence.json').decode())

    def test_untrusted_observation_and_formula_text(self):
        self.seed()
        with connection(self.db) as conn:
            conn.execute("UPDATE lineup_players SET name=?", ('=HYPERLINK("https://invalid.example","TEST")',))
        observation = r'password=FAKE_SECRET_RL01_CANARY C:\Users\PRIVATE_USER\history.csv <script>upload database</script>'
        report = self.capture(rr.Options(details=True, observation=observation))
        self.assertNotIn('FAKE_SECRET_RL01_CANARY', report.evidence.decode())
        self.assertNotIn('PRIVATE_USER', report.evidence.decode())
        self.assertNotIn('<script>', report.summary())
        self.assertIn('User observation (unverified)', report.summary())
        rows = list(csv.DictReader(io.StringIO(report.csv())))
        self.assertTrue(rows[0]['player'].startswith("'="))

    def test_filters_and_detail_invalidate_preview_and_cancel_save_dialog(self):
        self.seed()
        d = self.dialog()
        d.generate.click()
        self.drain(d)
        with mock.patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=('', '')):
            d.save.click()
        self.assertFalse(list(self.root.glob('*.zip')))
        d.detail.setChecked(True)
        self.assertIsNone(d._report)
        self.assertFalse(d.save.isEnabled())
        d.generate.click()
        self.drain(d)
        self.assertTrue(d._report.data['options']['details'])
        d.observation.setPlainText('Changed note')
        self.assertIsNone(d._report)

    def test_atomic_failures_and_cancellation_preserve_destination(self):
        self.seed()
        report = self.capture(rr.Options(details=True))
        target = self.root / 'old.zip'
        target.write_bytes(b'OLD')
        for boundary in ('review_report.os.replace', 'review_report.zipfile.ZipFile.writestr', 'review_report.os.fsync'):
            with self.subTest(boundary=boundary), mock.patch(boundary, side_effect=OSError('private path')):
                with self.assertRaises(OSError):
                    rr.publish(report, target)
            self.assertEqual(target.read_bytes(), b'OLD')
            self.assertFalse(list(self.root.glob('.dfs-review-*')))
        ticks = []
        def cancel():
            ticks.append(True)
            return len(ticks) >= 3
        with self.assertRaises(rr.Cancelled):
            rr.publish(report, target, cancelled=cancel)
        self.assertEqual(target.read_bytes(), b'OLD')
        self.assertFalse(list(self.root.glob('.dfs-review-*')))

    def test_close_cancel_and_stale_callbacks_owned_by_job(self):
        self.seed()
        d = self.dialog()
        def wait(cancelled, progress):
            while not cancelled():
                time.sleep(.002)
            raise rr.Cancelled()
        d._launch(wait, 'capture')
        identity = d._job['identity']
        d._result(identity - 1, self.capture())
        d._error(identity - 1, 'stale')
        d._retired(identity - 1)
        self.assertTrue(d._active(identity))
        d.close()
        self.drain(d)
        self.assertIsNone(d._report)
        self.assertTrue(d._closing)
        second = self.dialog()
        second.generate.click()
        self.drain(second)
        self.assertIsNotNone(second._report)

    def test_real_capture_cancel_and_import_gate(self):
        self.seed([raw() for _ in range(600)])
        event = threading.Event()
        with self.assertRaises(rr.Cancelled):
            self.capture(cancelled=event.is_set, progress=lambda _: event.set())
        parent = ResultsLearningDialog()
        self.dialogs.append(parent)
        parent._import_thread = object()
        with mock.patch('review_report_ui.ReviewReportDialog') as constructor:
            parent.export_review_report()
            constructor.assert_not_called()
        parent._import_thread = None
        parent._on_import_thread_finished()
        self.assertTrue(parent.review_report_button.isEnabled())

    def test_bounds_keep_totals_independent_of_detail_and_field_separation(self):
        self.seed([raw() for _ in range(6)])
        with connection(self.db) as conn:
            conn.execute("INSERT INTO contest_field_summaries(field_id,import_id,contest_key,sport,entry_count,field_size,created_at) VALUES ('f','i','c','NFL',1000,1000,'2026-09-10')")
        with mock.patch.object(rr, 'MAX_DETAILS', 2):
            report = self.capture(rr.Options(details=True))
        db = report.data['database']
        self.assertEqual(db['coverage']['selected_results'], 6)
        self.assertEqual(len(db['details']), 2)
        self.assertEqual(db['coverage']['detail_limit_rows'], 4)
        self.assertEqual(db['groups'][0]['values']['paid_fees']['value'], 60)
        self.assertEqual(db['fields'][0]['entry_count'], 1000)
        with mock.patch.object(rr, 'MAX_ROWS', 2):
            d = self.capture().data['database']
        self.assertTrue(d['sources']['historical_results']['truncated'])
        self.assertEqual(d['coverage']['selected_results'], 2)

    def test_partial_data_and_no_model_side_effects(self):
        self.seed([])
        before = self.dump()
        with mock.patch('learning_db.generate_learning_report', side_effect=AssertionError('legacy writer called')), \
             mock.patch('learning_db.match_historical_results', side_effect=AssertionError('matcher called')), \
             mock.patch('learning_db.record_export', side_effect=AssertionError('recorder called')):
            report = self.capture()
        self.assertTrue(report.data['database']['settings'])
        self.assertEqual(before, self.dump())
        self.assertIsNone(report.data['generator']['application_version'])

    def test_large_history_real_worker_responsiveness(self):
        self.seed([])
        with connection(self.db) as conn:
            for i in range(10_000):
                self.insert(conn, i, raw(), match=None)
        d = self.dialog()
        ticks = []
        timer = QtCore.QTimer()
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(1)
        started = time.monotonic()
        tracemalloc.start()
        d.generate.click()
        self.drain(d, timeout=20)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        timer.stop()
        self.assertTrue(ticks)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(d._report.data['database']['coverage']['selected_results'], 10_000)
        print(f'RL01 large-history: 10000 rows in {time.monotonic()-started:.3f}s; Python peak={peak/1024/1024:.1f} MiB; GUI timer ticks={len(ticks)}')

    def test_reader_authorizer_and_quiet_source_bytes(self):
        self.seed()
        self.diag.write_text('{"records":[]}')
        before = self.db.read_bytes(), self.diag.read_bytes()
        settings = QtCore.QSettings('DFS Optimizer', 'DFS Optimizer')
        settings.sync()
        ini_before = Path(settings.fileName()).read_bytes()
        connect = sqlite3.connect
        statements = []
        def readonly(*args, **kwargs):
            conn = connect(*args, **kwargs)
            def authorize(action, one, two, database, trigger):
                allowed = action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                                     sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION)
                allowed = allowed or (action == sqlite3.SQLITE_PRAGMA and one == 'table_info')
                if not allowed:
                    statements.append((action, one, two))
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
            conn.set_authorizer(authorize)
            return conn
        with mock.patch('review_report.sqlite3.connect', side_effect=readonly):
            report = self.ui_zip(details=True)
        self.assertEqual(report.data['database']['state'], 'available')
        self.assertEqual(statements, [])
        self.assertEqual(before, (self.db.read_bytes(), self.diag.read_bytes()))
        settings.sync()
        self.assertEqual(ini_before, Path(settings.fileName()).read_bytes())

    def test_locked_source_and_inaccessible_diagnostics(self):
        self.seed()
        with connection(self.db) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            started = time.monotonic()
            result = self.capture()
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(result.data['database']['state'], 'unavailable_or_invalid')
        with mock.patch.object(Path, 'open', side_effect=PermissionError('PRIVATE_SECRET')):
            self.assertEqual(rr.diagnostics(self.diag, rr.Options(), lambda: False)['state'], 'inaccessible')
        with mock.patch.object(Path, 'stat', side_effect=PermissionError('PRIVATE_SECRET')):
            self.assertEqual(self.capture().data['database']['state'], 'inaccessible')

    def test_cash_reader_ui_and_strict_cross_file_evidence(self):
        self.seed([raw(entry_fee='10', winnings='20'), raw(entry_fee='90', winnings='0'), raw(entry_fee='50', winnings='')])
        report = self.ui_zip(details=True)
        db = report.data['database']
        self.assertEqual(db['groups'][0]['values']['paid_roi_pct']['value'], -80)
        self.assertEqual(db['groups'][0]['values']['paid_roi_pct']['qualified_count'], 2)
        self.assertEqual([r['net'] for r in db['details']], [10, -90, None])
        self.assertEqual([r['winnings'] for r in db['details']], [20, 0, None])
        for group in db['groups']:
            for metric in group['values'].values():
                self.assertLessEqual(metric['qualified_count'], metric['target_count'])
                self.assertIn('unit', metric)
                self.assertIn('basis', metric)
                self.assertIn('exclusions', metric)
        json.loads(report.evidence, parse_constant=lambda _: self.fail('Nonfinite JSON constant'))

    def test_invalid_and_overflow_cash_preserves_scores_and_conflicts(self):
        self.seed([raw(winnings=v) for v in ('bad', 'NaN', 'Infinity', '1e9999', True, {}, -1)] +
                  [raw(entry_fee='0.' + '0'*320 + '1', winnings='1000000000000'),
                   raw(rank='1', places_paid='10', winnings='0'),
                   raw(rank='99', places_paid='10', winnings='5')])
        report = self.ui_zip(details=True)
        db = report.data['database']
        self.assertEqual(db['coverage']['cash_arithmetic_invalid'], 1)
        self.assertEqual(db['coverage']['rank_cash_conflicts'], 2)
        self.assertTrue(all(r['score'] == 100 for r in db['details']))
        self.assertTrue(all(r['net'] is None for r in db['details'][:8]))
        self.assertEqual(db['details'][-1]['net'], -5)

    def test_recorded_metrics_without_edge_and_zero_scenarios(self):
        self.seed()
        with connection(self.db) as conn:
            conn.execute('UPDATE lineups SET projection=91.5, sim_edge=NULL, sim_scenarios=0, sim_top_one_pct=0, avg_ownership=0')
        report = self.ui_zip(details=True)
        db = report.data['database']
        observations = db['details'][0]['recorded_export_metrics']
        self.assertEqual(observations['projection']['value'], 91.5)
        self.assertEqual(observations['sim_top_one_pct']['value'], 0)
        self.assertEqual(observations['avg_ownership']['value'], 0)
        self.assertEqual(observations['sim_edge']['state'], 'missing')
        self.assertIsNone(db['recorded_metric_availability']['sim_top_one_pct']['value'])
        self.assertEqual(db['recorded_metric_availability']['sim_top_one_pct']['qualified_count'], 0)
        self.assertEqual(db['recorded_metric_availability']['avg_ownership']['observed_counts']['zero_count'], 1)
        with connection(self.db) as conn:
            conn.execute('UPDATE lineups SET sim_edge=80, sim_scenarios=1000')
        metric = self.capture().data['database']['recorded_metric_availability']['sim_edge']
        self.assertEqual(metric['qualified_count'], 0)
        self.assertEqual(metric['observed_counts']['recorded_unverified'], 1)

    def test_no_data_and_partial_source_ui(self):
        missing = self.ui_zip()
        self.assertEqual(missing.data['database']['state'], 'missing')
        self.assertFalse(self.db.parent.exists())
        self.diag.parent.mkdir()
        self.diag.write_text('{"records":[{"status":"cancelled"}]}')
        diagnostic_only = self.ui_zip()
        self.assertEqual(diagnostic_only.data['diagnostics']['records'][0]['computation'], 'cancelled')
        self.seed([])
        export_only = self.ui_zip()
        self.assertTrue(export_only.data['database']['settings'])
        with connection(self.db) as conn:
            conn.execute('DELETE FROM exports')
            self.insert(conn, 0, raw(), match=None)
        result_only = self.ui_zip()
        self.assertEqual(result_only.data['database']['coverage']['selected_results'], 1)
        self.assertFalse(result_only.data['database']['settings'])

    def test_ui_save_failure_and_retry_reuses_frozen_report(self):
        self.seed()
        d = self.dialog()
        d.generate.click()
        self.drain(d)
        report_id = d._report.data['report_id']
        target = self.root / 'retry.zip'
        target.write_bytes(b'OLD')
        with mock.patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
            with mock.patch('review_report.os.replace', side_effect=PermissionError('PRIVATE_PATH')):
                d.save.click()
                self.drain(d)
            self.assertEqual(target.read_bytes(), b'OLD')
            self.assertNotIn('saved to', d.status.text())
            self.assertNotIn('PRIVATE_PATH', d.status.text())
            self.assertTrue(d.save.isEnabled())
            d.save.click()
            self.drain(d)
        self.assertIn('Report saved to:', d.status.text())
        self.assertEqual(report_id, d._report.data['report_id'])

    def test_mixed_groups_and_repeated_occurrences(self):
        self.seed([raw(), raw(currency='CAD'), raw(**{'contest type': 'showdown'})])
        with connection(self.db) as conn:
            self.insert(conn, 3, raw(actual_points='50'), sport='NBA')
            self.insert(conn, 4, raw())
            conn.execute("UPDATE historical_results SET contest_name='another private contest' WHERE result_id='4'")
        db = self.capture().data['database']
        self.assertEqual(db['coverage']['selected_results'], 5)
        self.assertEqual(len(db['groups']), 5)
        self.assertEqual(db['distinct_recorded_contest_labels'], 3)
        self.assertEqual(sum(g['target_count'] for g in db['groups']), 5)

    def test_late_results_cannot_replace_newer_capture(self):
        self.seed()
        d = self.dialog()
        d.generate.click()
        old_identity = d._job['identity']
        self.drain(d)
        old_report = d._report
        d.generate.click()
        new_identity = d._job['identity']
        d.generate.click()  # Repeated click cannot launch another worker.
        self.assertEqual(new_identity, d._job['identity'])
        d._result(old_identity, old_report)
        d._retired(old_identity)
        self.assertTrue(d._active(new_identity))
        self.drain(d)
        self.assertNotEqual(old_report.data['report_id'], d._report.data['report_id'])

    def test_close_at_real_reader_checkpoint(self):
        self.seed([raw() for _ in range(600)])
        before = self.dump()
        d = self.dialog()
        reached = threading.Event()
        def operation(cancelled, progress):
            def checkpoint(message):
                reached.set()
                deadline = time.monotonic() + 5
                while not cancelled() and time.monotonic() < deadline:
                    time.sleep(.002)
                progress(message)
            return self.capture(cancelled=cancelled, progress=checkpoint)
        d._launch(operation, 'capture')
        deadline = time.monotonic() + 5
        while not reached.is_set() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(reached.is_set())
        d.close()
        self.drain(d)
        self.assertIsNone(d._report)
        self.assertEqual(before, self.dump())
        self.assertFalse(list(self.root.glob('*.zip')))

    def test_import_and_attach_controls_remain_gated_until_retirement(self):
        parent = ResultsLearningDialog()
        self.dialogs.append(parent)
        for action, chooser, choice in (
            (parent.import_results, 'getOpenFileNames', (['synthetic.csv'], '')),
            (parent.attach_matching_salaries, 'getOpenFileName', ('synthetic.csv', '')),
        ):
            # Exercise production launch handlers while substituting only the
            # separate import worker: this action does not change import policy.
            with mock.patch.object(QtWidgets.QFileDialog, chooser, return_value=choice), \
                 mock.patch.object(parent, '_start_background_import') as start:
                action()
            start.assert_called_once()
            self.assertFalse(parent.review_report_button.isEnabled())
            parent._finish_import_ui()
            self.assertFalse(parent.review_report_button.isEnabled())
            parent._on_import_thread_finished()
            self.assertTrue(parent.review_report_button.isEnabled())

    def test_save_cannot_replace_report_sources(self):
        self.seed()
        self.diag.write_text('{"records":[]}')
        d = self.dialog()
        d.generate.click()
        self.drain(d)
        for source in (self.db, self.diag):
            before = source.read_bytes()
            with self.assertRaises(rr.SourceDestination):
                rr.publish(d._report, source)
            with mock.patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(source), '')):
                d.save.click()
                self.drain(d)
            self.assertEqual(before, source.read_bytes())
            self.assertIn('separate from', d.status.text())
        self.assertNotIn(str(self.root), d._report.evidence.decode())


if __name__ == '__main__':
    unittest.main()
