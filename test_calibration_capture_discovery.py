"""Independent capture discovery never changes selected forecast/build evidence."""
from test_environment import install
install()
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_distribution_build_selection as fixtures
import calibration_capture_discovery as discovery
from analysis_imports import ImportCancelled
from build_snapshots import fingerprint
from distribution_build_selection import save_choice
from distribution_calibration import summarize


class AutomaticCalibrationTests(unittest.TestCase):
    setUp = fixtures.SIMBuildSelectionTests.setUp
    build_fixture = fixtures.SIMBuildSelectionTests.build_fixture
    tearDown = fixtures.SIMBuildSelectionTests.tearDown
    snapshot = fixtures.SIMBuildSelectionTests.snapshot
    capture = fixtures.SIMBuildSelectionTests.capture

    def refresh(self, **kwargs):
        return discovery.refresh(self.db, **kwargs)

    def read(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return discovery.read(conn)

    def dump(self, protected=False):
        with closing(sqlite3.connect(self.db)) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {t:conn.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall()
                for t in tables if not protected or not t.startswith('calibration_')}

    def rewrite_capture(self, path, **changes):
        value=json.loads(path.read_text(encoding='utf-8'))
        value['payload'].update(changes);value['capture_id']=fingerprint(value['payload'])
        target=path.with_name(value['payload']['input_id']+'-'+value['capture_id']+'.json')
        target.write_text(json.dumps(value),encoding='utf-8')
        if target!=path:path.unlink()
        return target

    def test_earlier_capture_is_discovered_without_changing_forecast_or_build_choice(self):
        later=copy.deepcopy(self.players);later[0]['FlexProjection']=12
        latest,_=self.snapshot(later,'2099-09-21T19:00:00-04:00')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('CREATE TABLE result_snapshot_reviews(import_id TEXT PRIMARY KEY,payload TEXT)')
            conn.execute('INSERT INTO result_snapshot_reviews VALUES (?,?)',(self.ident,json.dumps(dict(input_id=latest['input_id']))));conn.commit()
            choice=discovery.catalog(conn,self.ident,self.root)['candidates'][0]
        save_choice(self.db,self.ident,choice)
        before=self.dump(protected=True)
        result=self.refresh();data=self.read()
        self.assertEqual(result['changed'],1);self.assertEqual(len(data['rows']),6)
        self.assertEqual({r['input_id'] for r in data['rows']},{self.snap['input_id']})
        self.assertEqual(before,self.dump(protected=True))
        self.assertTrue(all(r['snapshot_digest'] and r['capture_digest'] and r['salary_hash'] and r['result_hash'] for r in data['rows']))

    def test_warm_refresh_reuses_qualification_and_does_not_rewrite_index(self):
        self.refresh();before=self.dump()
        with patch.object(discovery,'catalog',side_effect=AssertionError('repeat qualification')), \
             patch('review_build_evidence.Reader.data',side_effect=AssertionError('repeat snapshot parsing')):
            result=self.refresh();self.assertEqual(len(self.read()['rows']),6)
        self.assertEqual(result['reused'],1);self.assertEqual(result['changed'],0)
        self.assertEqual(before,self.dump())

    def test_new_qualifying_capture_refreshes_and_latest_same_model_does_not_add_samples(self):
        self.refresh();self.capture(self.snap,finish='2099-09-21T18:03:00-04:00')
        self.assertEqual(self.read()['status'],'needs refresh')
        result=self.refresh();data=self.read()
        self.assertEqual(result['reused'],0);self.assertEqual(len(data['rows']),6)
        self.assertEqual({r['captured_at'] for r in data['rows']},{'2099-09-21T18:03:00-04:00'})
        self.assertEqual(summarize(data['rows'])['unique_player_games'],6)

    def test_unrelated_slate_snapshot_and_capture_do_not_requalify_this_result(self):
        self.refresh()
        players=copy.deepcopy(self.players)
        for p in players:p['GameInfo']='NE@SEA 09/22/2099 08:15PM ET'
        snap,_=self.snapshot(players,'2099-09-22T18:00:00-04:00')
        self.capture(snap,start='2099-09-22T18:01:00-04:00',finish='2099-09-22T18:02:00-04:00')
        with patch.object(discovery,'catalog',side_effect=AssertionError('unrelated requalification')):
            result=self.refresh()
        self.assertEqual(result['reused'],1);self.assertEqual(result['changed'],0)
        self.assertEqual(len(self.read()['rows']),6)

    def second_result(self, actual=10):
        import csv
        import analysis_imports as ai
        with self.results.open(newline='') as handle:rows=list(csv.reader(handle))
        for index,row in enumerate(rows[1:],1):row[1]=str(2000+index)
        rows[1][-1]=str(actual)
        path=self.results.with_name('9_21_2099_NFL second showdown.csv')
        with path.open('w',newline='') as handle:csv.writer(handle).writerows(rows)
        result=ai.import_folders(str(self.results.parent),str(self.salary.parent),db_path=self.db)
        self.assertEqual(result['errors'],[])

    def test_duplicate_contests_do_not_add_independent_outcomes(self):
        self.second_result();result=self.refresh()
        summary=summarize(self.read()['rows'])
        self.assertEqual(result['imports'],2)
        self.assertEqual(summary['unique_player_games'],6)
        self.assertEqual(summary['independent_games'],1)
        self.assertEqual(summary['excluded_repeated_or_conflicting'],6)

    def test_conflicting_outcomes_across_contests_are_withheld_for_all_models(self):
        self.second_result(actual=11);self.refresh()
        summary=summarize(self.read()['rows'])
        self.assertEqual(summary['conflicting_player_games'],1)
        self.assertEqual(summary['unique_player_games'],5)
        self.assertTrue(all(g['player_weighted']['player_games']<=5 for g in summary['groups']))

    def test_unrelated_same_day_game_does_not_requalify_existing_result(self):
        self.refresh();players=copy.deepcopy(self.players)
        for p in players:
            p['Team']='NYJ' if p['Team']=='NE' else 'BUF'
            p['GameInfo']='NYJ@BUF 09/21/2099 08:15PM ET'
        snap,_=self.snapshot(players,'2099-09-21T17:00:00-04:00')
        self.capture(snap,start='2099-09-21T17:01:00-04:00',finish='2099-09-21T17:02:00-04:00')
        with patch.object(discovery,'catalog',side_effect=AssertionError('other game requalified')):
            result=self.refresh()
        self.assertEqual(result['reused'],1);self.assertEqual(len(self.read()['rows']),6)

    def test_background_refresh_discovers_capture_without_new_result_or_forecast_rerun(self):
        from analysis_imports_ui import CombinedImportWorker
        def run():
            worker=CombinedImportWorker(str(self.results.parent),str(self.salary.parent),
                db_path=self.db,refresh_history=True,automatic=True)
            results=[];errors=[];worker.finished.connect(results.append);worker.error.connect(errors.append)
            worker.run();self.assertEqual(errors,[]);return results[0]
        with patch('performance_review.analyze_saved_results',side_effect=AssertionError('duplicate forecast analysis')):
            first=run();self.assertEqual(first['calibration_discovery']['rows'],6)
            with patch.object(discovery,'catalog',side_effect=AssertionError('warm boot qualification')):
                self.assertTrue(run()['unchanged_checkpoint'])
            self.capture(self.snap,finish='2099-09-21T18:03:00-04:00')
            with patch('analysis_imports_ui.import_folders',side_effect=AssertionError('capture-only reimport')), \
                 patch('opponent_history.sync_saved',side_effect=AssertionError('capture-only reindex')):
                updated=run()
        self.assertTrue(updated['calibration_only'])
        self.assertFalse(updated.get('unchanged_checkpoint'))
        self.assertEqual(updated['results_imported'],0)
        self.assertEqual(updated['calibration_discovery']['changed'],1)
        self.assertIn('Automatic calibration capture discovery',updated['report']['text'])

    def test_model_versions_stay_separate_without_extra_independent_games(self):
        new=self.capture(self.snap,finish='2099-09-21T18:03:00-04:00')
        self.rewrite_capture(new,model_version='a'*64)
        self.refresh();summary=summarize(self.read()['rows'])
        self.assertEqual(len([g for g in summary['groups'] if g['axis']=='all']),2)
        self.assertEqual(summary['unique_player_games'],6);self.assertEqual(summary['independent_games'],1)

    def test_classic_capture_is_discovered_with_its_own_format(self):
        self.build_fixture(kind='classic');self.refresh()
        rows=self.read()['rows']
        self.assertEqual(len(rows),9);self.assertEqual({r['kind'] for r in rows},{'classic'})

    def test_recorded_zero_actual_is_retained(self):
        self.second_result(actual=0)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DELETE FROM analysis_salary_pairs WHERE result_hash IN (SELECT hash FROM analysis_sources WHERE import_id=?)',(self.ident,));conn.commit()
        self.refresh();rows=self.read()['rows']
        self.assertEqual(len(rows),6)
        self.assertEqual(next(r['actual'] for r in rows if r['name']=='alpha'),0)

    def test_inventory_and_observation_limits_never_publish_partial_index(self):
        before=self.dump()
        with patch('distribution_build_selection.MAX_DIRECTORY_ENTRIES',0):
            with self.assertRaisesRegex(ValueError,'inventory limit'):self.refresh()
        self.assertEqual(before,self.dump())
        with patch.object(discovery,'MAX_ROWS',5):
            with self.assertRaisesRegex(OverflowError,'observation budget'):self.refresh()
        self.assertEqual(before,self.dump())
        with patch.object(discovery,'MAX_READ_BYTES',1):
            with self.assertRaisesRegex(OverflowError,'read budget'):self.refresh()
        self.assertEqual(before,self.dump())

    def test_source_change_withholds_old_rows_and_cannot_reuse_qualification(self):
        self.refresh()
        with closing(sqlite3.connect(self.db)) as conn:
            source=conn.execute("SELECT snapshot FROM analysis_sources WHERE kind='results'").fetchone()[0]
        path=Path(source);path.write_bytes(path.read_bytes()+b'\n')
        self.assertEqual(self.read()['rows'],[])
        self.refresh();self.assertEqual(self.read()['rows'],[])
        self.assertEqual(self.read()['unavailable'],1)

    def test_removed_pair_withholds_and_removes_derived_rows(self):
        self.refresh()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DELETE FROM analysis_salary_pairs');conn.commit()
        self.assertEqual(self.read()['rows'],[])
        result=self.refresh();self.assertEqual(result['changed'],1)
        self.assertEqual(self.read()['rows'],[])

    def test_changed_and_deleted_capture_withhold_then_refresh_without_reconstructed_ranges(self):
        self.refresh();self.capture_path.write_text('{}',encoding='utf-8')
        self.assertEqual(self.read()['rows'],[])
        self.refresh();self.assertEqual(self.read()['rows'],[])
        self.assertTrue(self.read()['reasons'])
        self.capture_path.unlink();self.refresh()
        self.assertEqual(self.read()['rows'],[])

    def test_postkickoff_capture_is_rejected_and_never_replaces_valid_earlier_evidence(self):
        self.capture(self.snap,finish='2099-09-21T20:15:00-04:00')
        self.refresh();data=self.read()
        self.assertEqual(len(data['rows']),6)
        self.assertEqual({r['captured_at'] for r in data['rows']},{'2099-09-21T18:02:00-04:00'})
        self.assertTrue(any('kickoff' in r for r in data['reasons']))

    def test_cancellation_before_work_and_during_publication_rolls_back(self):
        before=self.dump()
        with self.assertRaises(ImportCancelled):self.refresh(cancelled=lambda:True)
        self.assertEqual(before,self.dump())
        original=discovery._check
        def cancel_on_publication(callback):
            # The inserted derived row is visible only inside its own transaction.
            if publication[0]:raise ImportCancelled()
            return original(callback)
        publication=[False]
        real_json=discovery._json
        def notice_payload(value):
            if isinstance(value,dict) and value.get('status')=='available':publication[0]=True
            return real_json(value)
        with patch.object(discovery,'_json',side_effect=notice_payload),patch.object(discovery,'_check',side_effect=cancel_on_publication):
            with self.assertRaises(ImportCancelled):self.refresh()
        self.assertEqual(before,self.dump())

    def test_evidence_change_during_qualification_does_not_publish_partial_index(self):
        self.refresh();before=self.dump()
        self.capture(self.snap,finish='2099-09-21T18:03:00-04:00')
        real=discovery.catalog
        def changed(*args,**kwargs):
            result=real(*args,**kwargs)
            self.capture_path.write_bytes(self.capture_path.read_bytes()+b'\n')
            return result
        with patch.object(discovery,'catalog',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'inputs changed'):
                self.refresh()
        self.assertEqual(before,self.dump());self.assertEqual(self.read()['rows'],[])

    def test_corrupt_derived_payload_is_withheld_and_rebuilt(self):
        self.refresh()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE calibration_capture_evidence SET payload='{}'");conn.commit()
        self.assertEqual(self.read()['rows'],[])
        self.assertEqual(self.refresh()['reused'],0)
        self.assertEqual(len(self.read()['rows']),6)

    def test_read_only_report_never_initializes_tables(self):
        before=self.dump()
        with closing(sqlite3.connect(Path(self.db).as_uri()+'?mode=ro',uri=True)) as conn:
            text='\n'.join(discovery.report_lines(conn))
        self.assertIn('Not checked yet',text);self.assertEqual(before,self.dump())

    def test_report_shows_independent_evidence_and_preserves_small_sample_limits(self):
        self.refresh()
        with closing(sqlite3.connect(self.db)) as conn:text='\n'.join(discovery.report_lines(conn))
        self.assertIn('Automatic calibration capture discovery',text)
        self.assertIn('1 scheduled games; 6 distinct player-game outcomes',text)
        self.assertIn('saved forecast/build choices',text)
        self.assertIn('small samples do not justify automatic tuning',text)
        self.assertNotIn('alpha',text)


if __name__=='__main__':unittest.main()