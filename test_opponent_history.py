from test_environment import install
install()
import copy
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import analysis_imports as ai
from opponent_history import index_contest, profile_preview, save_profile, sync_saved
from test_hindsight_evidence import SourceFixture
from test_portfolio_risk_evidence import logical_db, source_bytes


def results(contest='synthetic', duplicate=False, conflict=False):
    def edit(rows):
        h=rows[0];base=rows[1];entries=[]
        for n in range(100):
            r=list(base)
            for field,value in dict(Rank=n+1,EntryId=1000+n,EntryName='Winner (1/50)' if n<50 else 'Other',ContestId=contest,FieldSize=100).items():
                r[h.index(field)]=str(value)
            entries.append(r)
        if duplicate:entries.append(list(entries[0]))
        if conflict:
            r=list(entries[0]);r[h.index('Points')]='999';entries.append(r)
        rows[:]=[h]+entries+rows[2:]
    return edit


class OpponentHistoryTests(unittest.TestCase):
    def fixture(self, contest='synthetic', **kwargs):
        f=SourceFixture(edit_results=results(contest,**kwargs));self.addCleanup(f.close);return f

    def test_all_users_entries_slots_and_duplicate_import_are_persisted(self):
        f=self.fixture(duplicate=True);sources=source_bytes(f.root)
        original={name:f.query('SELECT * FROM '+name) for name in ('analysis_sources','analysis_salary_pairs')}
        first=index_contest(f.db,f.source['hash']);self.assertFalse(first['unchanged'])
        self.assertEqual(f.query('SELECT COUNT(*) FROM opponent_users')[0][0],2)
        self.assertEqual(f.query('SELECT COUNT(*) FROM opponent_entries')[0][0],100)
        self.assertEqual(f.query('SELECT COUNT(*) FROM opponent_entry_slots')[0][0],600)
        self.assertEqual(f.query('SELECT COUNT(*) FROM opponent_entry_slots WHERE player_id IS NULL')[0][0],0)
        plan=f.query('EXPLAIN QUERY PLAN SELECT contest_key FROM opponent_entries WHERE entry_id=?',('1000',))
        self.assertTrue(any('opponent_entries_identity' in str(row) for row in plan))
        before=logical_db(f.db)
        with patch('opponent_history.analyze_saved_contest',side_effect=AssertionError('unchanged index was rebuilt')):
            self.assertTrue(index_contest(f.db,f.source['hash'])['unchanged'])
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
        preview=profile_preview(f.db,'2026-09-22')
        self.assertEqual(preview['timeline'],[]);self.assertEqual(preview['users_total'],2)
        for name,rows in original.items():self.assertEqual(rows,f.query('SELECT * FROM '+name))

    def test_conflicting_entry_excluded_and_success_unknown_for_partial_field(self):
        f=self.fixture(conflict=True);index_contest(f.db,f.source['hash'])
        self.assertEqual(f.query('SELECT COUNT(*) FROM opponent_entries')[0][0],99)
        self.assertEqual(f.query('SELECT complete FROM opponent_contests')[0][0],0)
        self.assertTrue(all(r[0] is None for r in f.query('SELECT top_one_pct_entries FROM opponent_user_contest_stats')))

    def test_cutoff_exact_username_normalization_format_and_unknown_user(self):
        f=self.fixture();index_contest(f.db,f.source['hash'])
        self.assertEqual(profile_preview(f.db,'2026-09-21')['baseline']['entries'],0)
        p=profile_preview(f.db,'2026-09-22',username='WINNER (2/50)')
        self.assertEqual(p['observed']['entries'],50);self.assertEqual(p['baseline']['entries'],100)
        self.assertAlmostEqual(p['user_history_weight'],1/3)
        for values in p['construction_priors'].values():self.assertAlmostEqual(sum(values.values()),1)
        self.assertEqual(profile_preview(f.db,'2026-09-22','classic')['baseline']['entries'],0)
        unknown=profile_preview(f.db,'2026-09-22',username='unknown')
        self.assertEqual(unknown['observed']['entries'],0);self.assertEqual(unknown['user_history_weight'],0)
        self.assertEqual(p['timeline'][0]['top_one_pct_entries'],1)
        with self.assertRaises(ValueError):profile_preview(f.db,'bad-date')

    def test_changed_sources_or_mapping_exclude_cached_profiles_and_do_not_rewrite_stats(self):
        f=self.fixture();index_contest(f.db,f.source['hash']);before=logical_db(f.db)
        path=Path(f.salary['snapshot']);path.write_bytes(path.read_bytes()+b'\n')
        p=profile_preview(f.db,'2026-09-22');self.assertEqual(p['baseline']['entries'],0);self.assertEqual(len(p['excluded']),1)
        with self.assertRaises(ValueError):index_contest(f.db,f.source['hash'])
        self.assertEqual(before,logical_db(f.db))

    def test_cancel_during_transaction_rolls_back_schema_and_all_rows(self):
        f=self.fixture();before=logical_db(f.db)
        original=ai._verify;calls=[]
        def verify(source,cancelled):
            original(source,cancelled);calls.append(source)
        with patch('opponent_history.ai._verify',side_effect=verify):
            with self.assertRaises(ai.ImportCancelled):index_contest(f.db,f.source['hash'],lambda:len(calls)>=7)
        self.assertEqual(before,logical_db(f.db))

    def test_changed_pair_between_read_and_write_is_rejected(self):
        f=self.fixture()
        import opponent_history as oh
        original=oh.analyze_saved_contest
        def changed(*args,**kwargs):
            report=original(*args,**kwargs);f.change('DELETE FROM analysis_salary_pairs');return report
        with patch('opponent_history.analyze_saved_contest',side_effect=changed):
            with self.assertRaises(ValueError):index_contest(f.db,f.source['hash'])
        self.assertFalse(f.query("SELECT name FROM sqlite_master WHERE name='opponent_entries'"))

    def test_cancel_during_bulk_publish_rolls_back_all_rows(self):
        import opponent_history as oh
        import threading
        f=self.fixture();before=logical_db(f.db);stop=threading.Event();original=oh._connect
        def connect(path,readonly=False):
            conn=original(path,readonly)
            if not readonly:
                conn.set_trace_callback(lambda sql:stop.set() if sql.startswith('INSERT INTO opponent_entry_slots') else None)
            return conn
        with patch('opponent_history._connect',side_effect=connect):
            with self.assertRaises(ai.ImportCancelled):index_contest(f.db,f.source['hash'],stop.is_set)
        self.assertTrue(stop.is_set());self.assertEqual(before,logical_db(f.db))

    def test_distinct_exports_of_same_contest_do_not_double_count(self):
        f=self.fixture();index_contest(f.db,f.source['hash']);before=f.query('SELECT * FROM opponent_entries')
        other=self.fixture(duplicate=True)
        ai.import_folders(other.results,other.salaries,db_path=str(f.db),username='Winner')
        self.assertNotEqual(f.source['hash'],other.source['hash'])
        with self.assertRaisesRegex(ValueError,'different export'):index_contest(f.db,other.source['hash'])
        self.assertEqual(before,f.query('SELECT * FROM opponent_entries'))

    def test_repeated_success_cohort_contains_losing_entries_and_profile_versions(self):
        f=self.fixture('one');index_contest(f.db,f.source['hash'])
        for name in ('two','three'):
            other=self.fixture(name);ai.import_folders(other.results,other.salaries,db_path=str(f.db),username='Winner')
            index_contest(f.db,other.source['hash'])
        p=profile_preview(f.db,'2026-09-22',username='Winner')
        self.assertEqual(p['successful_users']['users'],1)
        self.assertEqual(p['successful_users']['entries'],150)
        self.assertEqual(p['baseline']['entries'],300)
        p['baseline']['entries']=999999
        profile_id=save_profile(f.db,p)
        import json
        saved=json.loads(f.query('SELECT profile_json FROM opponent_profile_versions WHERE profile_id=?',(profile_id,))[0][0])
        self.assertEqual(saved['baseline']['entries'],300)
        self.assertEqual(len(saved['evidence']),3)

    def test_worker_delivers_history_and_cancel_suppresses_result(self):
        from opponent_history_ui import HistoryWorker
        f=self.fixture();worker=HistoryWorker(f.db,'sync');delivered=[];errors=[]
        worker.result.connect(delivered.append);worker.error.connect(errors.append);worker.run()
        self.assertEqual(len(delivered),1);self.assertFalse(errors)
        worker.stop.set();worker.run();self.assertEqual(len(delivered),1);self.assertFalse(errors)

    def test_standard_export_without_contest_id_tracks_field_and_requires_optin_for_success(self):
        def edit(rows):
            results()(rows);h=rows[0]
            for row in rows[1:]:
                row[h.index('ContestId')]='';row[h.index('FieldSize')]=''
        f=SourceFixture(edit_results=edit);self.addCleanup(f.close)
        index_contest(f.db,f.source['hash'])
        p=profile_preview(f.db,'2026-09-22',min_contests=1,min_successes=1)
        self.assertEqual(p['baseline']['entries'],100);self.assertEqual(p['successful_users']['users'],0)
        self.assertTrue(all(u['top_one_pct_entries'] is None for u in p['users']))
        p=profile_preview(f.db,'2026-09-22',min_contests=1,min_successes=1,allow_observed_fields=True)
        self.assertEqual(p['successful_users']['users'],1);self.assertIn('incomplete',p['success_definition'])
        self.assertIn('entry-set',p['evidence'][0]['identity_basis'])

    def test_entry_count_cohort_and_success_thresholds_are_explicit(self):
        f=self.fixture();index_contest(f.db,f.source['hash'])
        p=profile_preview(f.db,'2026-09-22',entry_band='1')
        self.assertEqual(p['baseline']['entries'],0)
        with self.assertRaises(ValueError):profile_preview(f.db,'2026-09-22',min_contests=1,min_successes=2)
        from opponent_construction import construction_summary,salary_lookup
        players=f.salary['manifest']['players'];lineup=[(p['name'],p['role']) for p in players if p['role']=='FLEX'][:6]
        lineup[0]=(lineup[0][0],'CPT')
        self.assertEqual(construction_summary([(lineup,2)],players),
                         construction_summary([(lineup,2)],players,lookup=salary_lookup(players)))

    def test_dialog_queued_cancel_preserves_previous_preview_and_controls(self):
        from PyQt5 import QtWidgets
        from opponent_history_ui import OpponentHistoryDialog, HistoryWorker
        self.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        f=self.fixture();d=OpponentHistoryDialog(f.db);self.addCleanup(d.deleteLater)
        class Thread:
            def deleteLater(self):pass
        worker=HistoryWorker(f.db,'preview');d._worker=worker;d._thread=Thread();d.action='preview'
        previous={'old':True};d.profile=previous;d.report.setPlainText('previous')
        d.receive({'new':True});d.cancel();d.retire()
        self.assertIs(d.profile,previous);self.assertEqual(d.report.toPlainText(),'previous')
        self.assertTrue(d.preview_button.isEnabled());self.assertIsNone(d._pending)
