"""RL-05B synthetic evidence, SQLite transactions and real Qt worker delivery."""
from test_environment import install
install()
import copy
from contextlib import closing
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PyQt5 import QtCore, QtWidgets
import analysis_imports as ai
import historical_identity as hi
import historical_coverage as hc
import test_historical_identity as fixtures
from test_analysis_imports import salary_file
from build_snapshots import create_snapshot, save_snapshot


def acceptance_history(root):
    """19 genuine imported contests, distinct slates; no private history required."""
    db = root/'history.sqlite'
    results, salaries = root/'results',root/'salaries'
    results.mkdir(); salaries.mkdir()
    kinds = ['UNRESOLVED']*3 + ['salary_ambiguous']*2 + ['snapshot_ambiguous','invalid_pair'] + ['SALARY_QUALIFIED']*3 + ['SNAPSHOT_QUALIFIED']*3 + ['BUILD_QUALIFIED']*3 + ['OUTCOME_QUALIFIED']*3
    snapshots = {}
    for index, state in enumerate(kinds,1):
        day = f'09/{index:02d}/2026'
        roles = ['CPT']+['FLEX']*5
        names = [f'Player{index} {n}' for n in ('Alpha','Bravo','Charlie','Delta','Echo','Foxtrot')]
        # Distinct player identities make even undated result sources unambiguous.
        if state!='UNRESOLVED':
            path = salary_file(salaries/f'salary-{index}.csv',day=day)
            text = path.read_text(encoding='utf-8-sig')
            for base,name in zip(('Alpha','Bravo','Charlie','Delta','Echo','Foxtrot'),names):
                text=text.replace(base,name)
            path.write_text(text,encoding='utf-8-sig')
            if state=='salary_ambiguous':
                (salaries/f'alternate-{index}.csv').write_text(text.replace(',5000,',',5100,'),encoding='utf-8-sig')
            manifest=ai._salary_manifest(path,lambda:False)
            players=[]
            for p in [p for p in manifest['players'] if p['role']=='FLEX']:
                captain=next(c for c in manifest['players'] if c['name']==p['name'] and c['role']=='CPT')
                players.append(dict(Name=p['name'],Position=p['position'],Team=p['team'],Opponent=p['opponent'],
                    FlexID=p['id'],CptID=captain['id'],FlexSalary=p['salary'],CptSalary=captain['salary'],
                    GameInfo=p['raw']['gameinfo'],FlexProjection=10,CptProjection=15))
            snap=create_snapshot(players,dict(sport='NFL',contest_kind='showdown'),{})
            snap['created_at']=f'2026-09-{index:02d}T18:00:00-04:00'
            snapshots[index]=snap
        with (results/f'private-contest-{index}.csv').open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f)
            w.writerow(['Rank','EntryId','EntryName','Points','Lineup','ContestId','Player','Roster Position','FPTS'])
            values=[0,-1,10,10,10,10]
            if state=='BUILD_QUALIFIED': values[2]=None
            for row in range(60):
                side=[names[row],'FLEX',values[row]] if row<6 else ['','','']
                w.writerow([row+1,index*1000+row,'Synthetic_Private_User',sum(v or 0 for v in values),
                    ' '.join(role+' '+name for role,name in zip(roles,names)),f'private-id-{index}',*side])
    imported=ai.import_folders(results,salaries,db_path=str(db),username='Synthetic_Private_User')
    if imported['errors']:raise AssertionError(imported['errors'])
    with closing(sqlite3.connect(db)) as conn:
        sources=ai._sources(conn)
    for index,state in enumerate(kinds,1):
        if state not in ('UNRESOLVED','salary_ambiguous'):
            result=next(s for s in sources if s['name']==f'private-contest-{index}.csv')
            salary=next(s for s in sources if s['name']==f'salary-{index}.csv')
            ai.save_pair(result['hash'],salary['hash'],db_path=str(db),confirm_date=True)
            if state=='invalid_pair':
                Path(salary['snapshot']).write_text('changed revision')
        if state in ('SNAPSHOT_QUALIFIED','BUILD_QUALIFIED','OUTCOME_QUALIFIED','snapshot_ambiguous'):
            snap=snapshots[index]
            save_snapshot(str(root/'snapshots'/f'{snap["input_id"]}.json'),snap)
            if state=='snapshot_ambiguous':
                alt=copy.deepcopy(snap);alt['inputs']['players'][0]['FlexProjection']=99
                alt['input_id']=hi._digest(alt['inputs'])
                save_snapshot(str(root/'snapshots'/f'{alt["input_id"]}.json'),alt)
            if state in ('BUILD_QUALIFIED','OUTCOME_QUALIFIED'):
                factory=fixtures.HistoricalIdentityTests()
                factory.root, factory.snap, factory.roles=root,snap,roles
                stamp=f'2026-09-{index:02d}T18:10:00-04:00'
                factory.archive(f'build-{index}.zip',audit_id=str(index),created_at=stamp)
                factory.archive(f'build-{index}-b.zip',audit_id=str(index)+'b',created_at=stamp)
    hi.reconcile(db)
    return db


class CoverageBackendTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.HistoricalIdentityTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def ambiguous(self):
        f=self.f
        f.fixture(); f.snapshot()
        alt=copy.deepcopy(f.snap);alt['inputs']['players'][0]['FlexProjection']=99
        alt['input_id']=hi._digest(alt['inputs']);f.snapshot(alt)
        rows=hi.reconcile(f.db)
        d=rows[0].data
        return d,{d['identity_id']:hi._digest(f.snap)}

    def test_manual_snapshot_persists_version_timestamp_and_raw_results(self):
        d,choices=self.ambiguous();f=self.f
        raw=f.query('SELECT * FROM historical_results')
        after=hi.reconcile(f.db,snapshot_choices=choices)[0].data
        self.assertEqual(after['snapshot_evidence']['input_id'],f.snap['input_id'])
        self.assertEqual(after['snapshot_evidence']['association_method'],'user_confirmed')
        self.assertEqual(after['snapshot_resolution']['evidence_version'],hi.METHOD_VERSION)
        self.assertTrue(after['snapshot_resolution']['confirmed_at'])
        self.assertEqual(raw,f.query('SELECT * FROM historical_results'))
        self.assertEqual(after,hi.reconcile(f.db)[0].data)

    def test_later_unique_snapshot_does_not_replace_manual_choice(self):
        d,choices=self.ambiguous();f=self.f
        hi.reconcile(f.db,snapshot_choices=choices)
        alt=copy.deepcopy(f.snap);alt['inputs']['players'][0]['FlexProjection']=50
        alt['input_id']=hi._digest(alt['inputs']);alt['created_at']='2026-09-21T19:00:00-04:00'
        f.snapshot(alt)
        self.assertEqual(hi.reconcile(f.db)[0].data['snapshot_evidence']['input_id'],f.snap['input_id'])

    def test_invalid_manual_revision_stays_conflict_without_retargeting(self):
        d,choices=self.ambiguous();f=self.f
        hi.reconcile(f.db,snapshot_choices=choices)
        before=f.query('SELECT * FROM historical_evidence_resolutions')
        (f.root/'snapshots'/f'{f.snap["input_id"]}.json').unlink()
        after=hi.reconcile(f.db)[0].data
        self.assertEqual(after['state'],'CANDIDATE')
        self.assertIn('invalid_saved_snapshot_resolution',after['conflicts'])
        self.assertIsNone(after['snapshot_evidence'])
        self.assertEqual(before,f.query('SELECT * FROM historical_evidence_resolutions'))

    def test_saved_snapshot_respects_later_contest_id_evidence(self):
        d,choices=self.ambiguous();f=self.f
        hi.reconcile(f.db,snapshot_choices=choices)
        other=next(c for c in d['snapshot_candidates'] if c['input_id']!=f.snap['input_id'])
        (f.root/'contest-snapshots.json').write_text(json.dumps({'123':[other['input_id']]}))
        self.assertIn('invalid_saved_snapshot_resolution',hi.reconcile(f.db)[0].data['conflicts'])

    def test_no_arbitrary_choice_for_unambiguous_snapshot(self):
        f=self.f;f.fixture();f.snapshot();d=hi.reconcile(f.db)[0].data
        with self.assertRaisesRegex(ValueError,'no longer ambiguous'):
            hi.reconcile(f.db,snapshot_choices={d['identity_id']:hi._digest(f.snap)})
        self.assertEqual(f.query('SELECT * FROM historical_evidence_resolutions'),[])

    def test_stale_choice_never_saves(self):
        d,choices=self.ambiguous();f=self.f
        with self.assertRaisesRegex(ValueError,'no longer qualifies'):
            hi.reconcile(f.db,snapshot_choices={d['identity_id']:'f'*64})
        self.assertEqual(f.query('SELECT * FROM historical_evidence_resolutions'),[])

    def test_bulk_choice_error_rolls_back_prior_choice(self):
        d,choices=self.ambiguous();f=self.f
        before=f.query('SELECT * FROM historical_contest_identities')
        with self.assertRaises(ValueError):
            hi.reconcile(f.db,snapshot_choices={**choices,'missing':'bad'})
        self.assertEqual(f.query('SELECT * FROM historical_evidence_resolutions'),[])
        self.assertEqual(before,f.query('SELECT * FROM historical_contest_identities'))

    def test_cancel_after_choice_before_commit_rolls_back_everything(self):
        d,choices=self.ambiguous();f=self.f
        before=f.query('SELECT * FROM historical_contest_identities')
        cancel=threading.Event()
        def progress(text):
            if text.startswith('Saving all'):cancel.set()
        with self.assertRaises(ai.ImportCancelled):
            hi.reconcile(f.db,snapshot_choices=choices,cancelled=cancel.is_set,progress=progress)
        self.assertEqual(f.query('SELECT * FROM historical_evidence_resolutions'),[])
        self.assertEqual(before,f.query('SELECT * FROM historical_contest_identities'))

    def test_progress_and_idempotent_receipts_do_not_change_sources(self):
        f=self.f;f.fixture();f.snapshot();f.archive();before=hi.reconcile(f.db)
        rows=f.query('SELECT * FROM historical_contest_identities');progress=[]
        after=hi.reconcile(f.db,progress=progress.append)
        self.assertEqual(rows,f.query('SELECT * FROM historical_contest_identities'))
        self.assertTrue(any('Verifying' in t for t in progress))
        self.assertEqual(hc.changes(before,after)['newly_qualified'],dict(salary=0,snapshot=0,build=0,outcome=0))

    def test_capabilities_do_not_require_actual_scores_for_risk(self):
        f=self.f;f.fixture(scores=[0,-1,None,10,10,10]);f.snapshot();f.archive()
        data=hi.reconcile(f.db)[0].data
        self.assertTrue(hc.capabilities(data)['portfolio_risk'])
        self.assertFalse(hc.capabilities(data)['hindsight'])
        self.assertFalse(hc.capabilities(data)['compute_timing'])
        self.assertEqual(data['actual_score_evidence']['known_scores'],10)

    def test_fast_read_never_opens_source_or_reconciles(self):
        f=self.f;f.fixture();hi.reconcile(f.db)
        with patch('historical_identity.derive_contests',side_effect=AssertionError),patch('analysis_imports._verify',side_effect=AssertionError),patch('builtins.open',side_effect=AssertionError):
            self.assertEqual(len(hc.load_saved(f.db)['contests']),1)

    def test_unknown_database_read_does_not_create_history(self):
        self.assertEqual(hc.load_saved(self.f.db)['contests'],())
        self.assertFalse(self.f.db.exists())

    def test_corrupt_cached_row_is_disclosed_not_qualified(self):
        f=self.f;f.fixture();hi.reconcile(f.db)
        f.change("UPDATE historical_contest_identities SET evidence_hash='bad'")
        saved=hc.load_saved(f.db)
        self.assertEqual(saved['invalid_rows'],1)
        self.assertEqual(saved['contests'],())
        self.assertEqual(saved['pending_imports'],1)

    def test_new_sources_are_reported_pending_without_scanning(self):
        f=self.f;f.fixture()
        self.assertEqual(hc.load_saved(f.db)['pending_imports'],1)
        hi.reconcile(f.db)
        self.assertEqual(hc.load_saved(f.db)['pending_imports'],0)

    def test_salary_choices_show_human_evidence_and_versioned_confirmation(self):
        f=self.f;f.fixture(dated=False)
        state=ai.pairing_state(f.db);candidate=state['results'][0]['saved_pair']
        self.assertTrue(candidate['dates']);self.assertTrue(candidate['games'])
        self.assertEqual(candidate['player_roles'],12)
        self.assertTrue(candidate['imported_at'])
        version,method,stamp=f.query('SELECT evidence_version,basis,created_at FROM analysis_salary_pairs')[0]
        self.assertEqual(version,1);self.assertIn('user-confirmed',method);self.assertTrue(stamp)

    def test_salary_only_import_reconciles_older_results(self):
        f=self.f;f.fixture();f.change('DELETE FROM analysis_salary_pairs')
        f.change("DELETE FROM analysis_sources WHERE kind='salary'")
        self.assertEqual(hi.reconcile(f.db)[0].data['state'],'UNRESOLVED')
        from analysis_imports_ui import CombinedImportWorker
        worker=CombinedImportWorker(salary_folder=str(f.salaries),db_path=str(f.db))
        done=[];worker.finished.connect(done.append);worker.run()
        self.assertTrue(done[0]['coverage_reconciled'])
        self.assertEqual(hc.load_saved(f.db)['contests'][0].data['state'],'SALARY_QUALIFIED')

    def build_candidates(self, *, ambiguous=False, archive_latest=True):
        f = self.f
        f.fixture(scores=[0,-1,10,10,10,10]); f.snapshot(); f.archive('older.zip')
        newer = copy.deepcopy(f.snap)
        newer['inputs']['players'][0]['FlexProjection'] = 99
        newer['input_id'] = hi._digest(newer['inputs'])
        if not ambiguous:
            newer['created_at'] = '2026-09-21T19:00:00-04:00'
        f.snapshot(newer)
        if archive_latest:
            f.archive('newer.zip', snap=newer, created_at='2026-09-21T19:10:00-04:00')
        return hi.reconcile(f.db)

    def assert_build_details_are_read_only(self, contests, qualified, candidates):
        before = [c.data for c in contests]
        counts = hc.aggregate(contests)
        def source_bytes():
            # SQLite readers may create WAL/shared-memory sidecars; compare the
            # database logically and the immutable source files byte for byte.
            return {p.relative_to(self.f.root):p.read_bytes() for p in self.f.root.rglob('*')
                    if p.is_file() and not p.name.startswith(self.f.db.name)}
        files = source_bytes()
        with closing(sqlite3.connect(self.f.db)) as conn:
            database = tuple(conn.iterdump())
        with patch('analysis_imports._verify', side_effect=AssertionError), patch('historical_identity._inventory', side_effect=AssertionError):
            saved = hc.load_saved(self.f.db)['contests']
            rendered = saved[0].data
            text = hc.detail_text(rendered)
        linked, other = text.split('Other generated archive candidates (not qualified build evidence):')
        self.assertIn('Archives linked to the current qualified snapshot:', linked)
        for section, builds in ((linked,qualified),(other,candidates)):
            for build in builds:
                self.assertIn(build['archive_id'], section)
                self.assertEqual(text.count(build['archive_id']), 1)
                self.assertIn(build['input_id'], section)
                self.assertIn(build['recorded_at'], section)
                self.assertIn(f"{build['output_count']} lineups", section)
        self.assertIn('not established', text)
        self.assertEqual(before, [c.data for c in saved])
        self.assertEqual(before[0], rendered)
        self.assertEqual(counts, hc.aggregate(saved))
        self.assertEqual(files, source_bytes())
        with closing(sqlite3.connect(self.f.db)) as conn:
            self.assertEqual(database, tuple(conn.iterdump()))

    def test_build_details_separate_two_snapshot_inputs(self):
        contests = self.build_candidates()
        data = contests[0].data
        self.assertEqual(data['state'], 'OUTCOME_QUALIFIED')
        qualified = data['build_evidence']
        other = [b for b in data['build_candidates'] if b['input_id']!=qualified[0]['input_id']]
        self.assertEqual((len(qualified),len(other)), (1,1))
        self.assert_build_details_are_read_only(contests, qualified, other)

    def test_newer_snapshot_does_not_promote_older_candidate_archive(self):
        contests = self.build_candidates(archive_latest=False)
        data = contests[0].data
        self.assertEqual(data['state'], 'SNAPSHOT_QUALIFIED')
        self.assertEqual(data['build_evidence'], [])
        self.assertFalse(hc.capabilities(data)['portfolio_risk'])
        self.assertEqual(len(data['build_candidates']), 1)
        self.assert_build_details_are_read_only(contests, [], data['build_candidates'])

    def test_ambiguous_snapshots_expose_candidates_without_qualifying_builds(self):
        contests = self.build_candidates(ambiguous=True)
        data = contests[0].data
        self.assertEqual(data['state'], 'CANDIDATE')
        self.assertIsNone(data['snapshot_evidence'])
        self.assertEqual(data['build_evidence'], [])
        self.assertEqual(len(data['build_candidates']), 2)
        self.assertFalse(hc.capabilities(data)['portfolio_risk'])
        self.assert_build_details_are_read_only(contests, [], data['build_candidates'])

    def test_distinct_archives_sharing_one_input_remain_visible_once(self):
        f = self.f
        f.fixture(); f.snapshot(); f.archive('one.zip'); f.archive('two.zip',audit_id='second')
        contests = hi.reconcile(f.db)
        data = contests[0].data
        self.assertEqual(data['state'], 'OUTCOME_QUALIFIED')
        self.assertEqual(len(data['build_evidence']), 2)
        self.assertEqual(len({b['input_id'] for b in data['build_evidence']}), 1)
        self.assert_build_details_are_read_only(contests, data['build_evidence'], [])


class CoverageAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.tmp=tempfile.TemporaryDirectory(prefix='rl05b-acceptance-')
        cls.root=Path(cls.tmp.name)
        cls.db=acceptance_history(cls.root)
        cls.saved=hc.load_saved(cls.db)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def widget(self):
        from historical_coverage_ui import HistoricalCoverageWidget
        w=HistoricalCoverageWidget(str(self.db));self.addCleanup(w.deleteLater)
        return w

    def test_19_contest_counts_reconcile_to_actual_qualification(self):
        summary=hc.aggregate(self.saved['contests'])
        self.assertEqual(summary['total_historical_contests'],19)
        self.assertEqual(summary['states'],dict(UNRESOLVED=3,CANDIDATE=4,SALARY_QUALIFIED=3,SNAPSHOT_QUALIFIED=3,BUILD_QUALIFIED=3,OUTCOME_QUALIFIED=3))
        self.assertEqual(summary['evidence_levels'],dict(results=19,salary=13,snapshot=9,build=6,outcome=10))
        self.assertEqual(summary['categories'],dict(ready=3,needs_review=4,missing_evidence=12,conflict=2))

    def test_ready_filter(self):
        w=self.widget();w.filter.setCurrentIndex(w.filter.findData('ready'))
        self.assertEqual(w.table.rowCount(),3)

    def test_newly_outcome_qualified_requires_full_chain_not_just_scores(self):
        counts=hc.changes((),self.saved['contests'])['newly_qualified']
        self.assertEqual(counts,dict(salary=13,snapshot=9,build=6,outcome=3))

    def test_needs_review_filter(self):
        w=self.widget();w.filter.setCurrentIndex(w.filter.findData('needs_review'))
        self.assertEqual(w.table.rowCount(),4)

    def test_missing_filter(self):
        w=self.widget();w.filter.setCurrentIndex(w.filter.findData('missing_evidence'))
        self.assertEqual(w.table.rowCount(),12)

    def test_conflict_filter(self):
        w=self.widget();w.filter.setCurrentIndex(w.filter.findData('conflict'))
        self.assertEqual(w.table.rowCount(),2)

    def test_all_exclusive_state_filters(self):
        w=self.widget()
        for state,count in hc.aggregate(self.saved['contests'])['states'].items():
            w.filter.setCurrentIndex(w.filter.findData(state))
            self.assertEqual(w.table.rowCount(),count)

    def test_format_and_sport_filters(self):
        w=self.widget();w.format_filter.setCurrentText('classic')
        self.assertEqual(w.table.rowCount(),0)
        w.format_filter.setCurrentText('showdown')
        self.assertEqual(w.table.rowCount(),13)

    def test_detail_chain_and_multiple_build_disclaimer(self):
        w=self.widget();w.filter.setCurrentIndex(w.filter.findData('BUILD_QUALIFIED'))
        text=w.details.toPlainText()
        self.assertIn('10 / 12',text);self.assertIn('Original',text.title())
        self.assertIn('not established',text);self.assertIn('Portfolio Risk / RL-06: evidence available',text)
        self.assertIn('Hindsight / RL-07: unavailable',text)

    def test_fast_tab_open_has_no_source_io_and_is_responsive(self):
        started=time.monotonic()
        with patch('analysis_imports._verify',side_effect=AssertionError),patch('historical_identity._inventory',side_effect=AssertionError):
            w=self.widget();w.show();self.app.processEvents()
        self.assertEqual(w.table.rowCount(),19)
        self.assertLess(time.monotonic()-started,2)
        w.close()

    def test_snapshot_chooser_discloses_coverage_timing_and_builds(self):
        from historical_coverage_ui import SnapshotChoiceDialog
        data=next(c.data for c in self.saved['contests'] if 'conflicting_latest_snapshots' in c.data['conflicts'])
        w=SnapshotChoiceDialog(data);self.addCleanup(w.deleteLater)
        self.assertEqual(w.options.count(),2)
        self.assertIn('6 players',w.details.toPlainText())
        self.assertIn('Earliest game',w.details.toPlainText())
        self.assertFalse(w.save.isEnabled())
        w.confirm.setChecked(True);w._select();self.assertIsNotNone(w.choice)

    def test_shareable_report_counts_and_privacy(self):
        from review_report import capture
        report=capture(db_path=self.db)
        data=report.data['database']['historical_identity']
        self.assertEqual(data['states'],hc.aggregate(self.saved['contests'])['states'])
        self.assertEqual(data['capability_evidence']['portfolio_risk'],6)
        encoded=json.dumps(data)
        for secret in ('Synthetic_Private_User','private-contest','private-id-',str(self.root),'Player1',self.saved['contests'][0].data['identity_id']):
            self.assertNotIn(secret,encoded)
        self.assertIn('Downstream prerequisite counts',report.summary())


class CoverageWorkerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':self.tmp.name});self.env.start();self.addCleanup(self.env.stop)
        from main_window import ResultsLearningDialog
        self.dialog=ResultsLearningDialog()
        self.addCleanup(self.cleanup_dialog)

    def cleanup_dialog(self):
        self.dialog.reject();self.drain();self.dialog.deleteLater();self.app.processEvents()

    def drain(self):
        end=time.monotonic()+12
        while self.dialog._import_thread is not None and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.assertIsNone(self.dialog._import_thread)
        self.app.processEvents()

    def completed_before_retirement(self, start):
        """Deliver a real worker payload but defer the GUI's retirement callback."""
        retired = []
        with patch.object(self.dialog, '_on_import_thread_finished', side_effect=retired.append):
            start()
            job = self.dialog._import_job
            end = time.monotonic()+12
            while not retired and time.monotonic()<end:
                self.app.processEvents(); time.sleep(.002)
            self.assertEqual(retired, [job])
            self.assertIsNotNone(job['completion'])
            self.assertFalse(self.dialog.import_new_button.isEnabled())
        return job

    def test_cancel_queued_salary_review_suppresses_dialog_and_follow_on_import(self):
        with patch('analysis_imports_ui.SalaryMatchesDialog') as chooser, patch('analysis_imports_ui.CombinedImportWorker') as importer:
            job = self.completed_before_retirement(self.dialog.review_salary_matches)
            self.assertFalse(job['completion'][1]['cancelled'])
            self.dialog.cancel_import()
            self.assertFalse(self.dialog.import_new_button.isEnabled())
            self.dialog._on_import_thread_finished(job)
            self.drain()
        chooser.assert_not_called(); importer.assert_not_called()
        self.assertTrue(self.dialog.import_new_button.isEnabled())

    def test_salary_review_normal_delivery_once_ignores_stale_and_duplicate_callbacks(self):
        with patch('analysis_imports_ui.SalaryMatchesDialog') as chooser, patch('analysis_imports_ui.CombinedImportWorker') as importer:
            chooser.return_value.exec_.return_value = QtWidgets.QDialog.Rejected
            first = self.completed_before_retirement(self.dialog.review_salary_matches)
            self.dialog._on_import_thread_finished(first)
            self.assertEqual(chooser.call_count, 1)
            second = self.completed_before_retirement(self.dialog.review_salary_matches)
            completion = second['completion']
            self.dialog._job_completed(first, lambda _:self.fail('stale completion applied'), {})
            self.dialog._on_import_thread_finished(first)
            self.dialog._job_completed(second, lambda _:self.fail('duplicate completion applied'), {})
            self.assertIs(second['completion'], completion)
            self.assertIs(self.dialog._import_job, second)
            self.dialog._on_import_thread_finished(second)
            self.dialog._on_import_thread_finished(second)
            self.drain()
            self.assertEqual(chooser.call_count, 2)
        importer.assert_not_called()

    def test_close_during_salary_review_suppresses_continuation(self):
        import analysis_imports_ui as ui
        entered, release = threading.Event(), threading.Event()
        original = ui.pairing_state
        def held(*args, **kwargs):
            entered.set(); release.wait(5)
            return original(*args, **kwargs)
        with patch.object(ui, 'pairing_state', side_effect=held), patch.object(ui, 'SalaryMatchesDialog') as chooser, patch.object(ui, 'CombinedImportWorker') as importer:
            self.dialog.show(); self.dialog.review_salary_matches()
            try:
                end = time.monotonic()+3
                while not entered.is_set() and time.monotonic()<end:
                    self.app.processEvents(); time.sleep(.002)
                self.assertTrue(entered.is_set())
                self.dialog.close()
                self.assertFalse(self.dialog.import_new_button.isEnabled())
            finally:
                release.set(); self.drain()
        chooser.assert_not_called(); importer.assert_not_called()
        self.assertFalse(self.dialog.isVisible())

    def test_late_cancel_preserves_committed_reconciliation_and_completion(self):
        f = fixtures.HistoricalIdentityTests(); f.setUp(); self.addCleanup(f.tearDown)
        f.fixture(); f.snapshot(); f.archive()
        self.dialog.db_path = str(f.db); self.dialog.coverage.db_path = str(f.db)
        job = self.completed_before_retirement(self.dialog.reconcile_history)
        self.assertTrue(job['completion'][1]['committed'])
        before = f.query('SELECT * FROM historical_contest_identities')
        self.assertEqual(hc.load_saved(f.db)['contests'][0].data['state'], 'OUTCOME_QUALIFIED')
        self.dialog.cancel_import()
        self.assertFalse(self.dialog.import_new_button.isEnabled())
        self.dialog._on_import_thread_finished(job)
        self.drain()
        self.assertEqual(before, f.query('SELECT * FROM historical_contest_identities'))
        self.assertIn('Reconciliation committed', self.dialog.coverage.status.text())

    def test_existing_dialog_tab_runs_real_reconciliation(self):
        self.assertEqual(self.dialog.results_tabs.tabText(1),'Historical Coverage')
        self.dialog.coverage.reconcile_button.click()
        self.assertFalse(self.dialog.import_new_button.isEnabled())
        self.drain()
        self.assertIn('Reconciliation committed',self.dialog.coverage.status.text())
        self.assertTrue(self.dialog.import_new_button.isEnabled())

    def test_close_during_reconciliation_cancels_without_late_apply(self):
        entered,release=threading.Event(),threading.Event()
        original=hi.derive_contests
        def held(*args,**kwargs):
            entered.set();release.wait(15);return original(*args,**kwargs)
        with patch.object(hi,'derive_contests',side_effect=held):
            self.dialog.show();self.dialog.reconcile_history()
            try:
                # Match the suite's worker-drain allowance on busy Windows runners.
                end=time.monotonic()+12
                while not entered.is_set() and time.monotonic()<end:self.app.processEvents();time.sleep(.005)
                self.assertTrue(entered.is_set());self.dialog.close()
                self.assertTrue(self.dialog._import_worker.cancelled.is_set())
            finally:
                release.set();self.drain()
        self.assertFalse(self.dialog.isVisible())
        self.assertIsNone(self.dialog.coverage.started)

    def test_stale_and_duplicate_deliveries_do_not_apply(self):
        self.dialog.reconcile_history();active=self.dialog._import_job
        stale=dict(completion=None)
        self.dialog._job_completed(stale,lambda x:self.fail('stale applied'),{})
        self.dialog._on_import_thread_finished(stale)
        self.assertIs(self.dialog._import_job,active)
        self.drain();text=self.dialog.coverage.status.text()
        self.dialog._on_import_thread_finished(active)
        self.assertEqual(text,self.dialog.coverage.status.text())

    def test_worker_cancelled_before_start_never_reconciles_sources(self):
        from historical_coverage_ui import CoverageWorker
        worker=CoverageWorker(self.dialog.db_path);worker.request_cancel();done=[]
        worker.finished.connect(done.append)
        with patch.object(hi,'derive_contests',side_effect=AssertionError):worker.run()
        self.assertTrue(done[0]['cancelled']);self.assertFalse(done[0]['committed'])

    def test_error_retires_and_unlocks_controls(self):
        with patch('historical_coverage_ui.reconcile',side_effect=OSError),patch('PyQt5.QtWidgets.QMessageBox.critical') as message:
            self.dialog.reconcile_history();self.drain()
        self.assertEqual(message.call_count,1)
        self.assertIn('failed',self.dialog.coverage.status.text())
        self.assertTrue(self.dialog.coverage.isEnabled())


if __name__=='__main__':unittest.main()
