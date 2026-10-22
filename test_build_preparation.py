from test_environment import install
install()
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtCore
from build_preparation import discover_libraries, remember_library, remembered_libraries
from showdown_library import prepare
from test_showdown_library import players


class BuildPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.players = players()

    def discover(self, known=(), **kwargs):
        return list(discover_libraries(known, self.root, self.players, 'showdown', 50000, **kwargs))

    def test_sqlite_named_rosters_are_discovered_read_only_without_accepting_partial_or_unrelated_databases(self):
        import sqlite3
        complete=self.root/'prepared.sqlite';partial=self.root/'partial.sqlite';other=self.root/'history.sqlite'
        prepare(complete,self.players);prepare(partial,self.players,max_candidates=2)
        con=sqlite3.connect(other);con.execute('CREATE TABLE history (id INTEGER)');con.commit();con.close()
        before={path:path.read_bytes() for path in (complete,partial,other)}
        self.assertEqual(self.discover(),[str(complete.resolve())])
        self.assertTrue(all(path.read_bytes()==data for path,data in before.items()))

    def test_complete_library_is_discovered_read_only_and_deduplicated(self):
        path = self.root/'complete.sdlib'
        prepare(path, self.players)
        before = path.read_bytes()
        found = self.discover((str(path), str(path)))
        self.assertEqual(found, [str(path.resolve())])
        self.assertEqual(path.read_bytes(), before)

    def test_partial_wrong_slate_and_corrupt_libraries_are_skipped(self):
        partial, wrong, corrupt = (self.root/name for name in ('partial.sdlib', 'wrong.sdlib', 'corrupt.sdlib'))
        prepare(partial, self.players, max_candidates=2)
        other = copy.deepcopy(self.players)
        for p in other: p['GameInfo'] = 'next game'
        prepare(wrong, other)
        corrupt.write_bytes(b'not sqlite')
        messages = []
        self.assertEqual(self.discover(progress=messages.append), [])
        self.assertGreaterEqual(len(messages), 3)

    def test_older_catalog_falls_back_without_creating_lookup_or_mutating_database(self):
        import sqlite3
        root=self.root/'candidates';root.mkdir()
        catalog=self.root/'slate-catalog.sqlite'
        con=sqlite3.connect(catalog)
        try:
            with con:con.execute('CREATE TABLE older (id INTEGER)')
        finally:con.close()
        path=root/'complete.sdlib';prepare(path,self.players)
        before=catalog.read_bytes();events=[]
        found=list(discover_libraries((),root,self.players,'showdown',50000,audit=events.append))
        self.assertEqual(found,[str(path.resolve())])
        self.assertIn('index_unavailable',events)
        self.assertEqual(catalog.read_bytes(),before)

    def test_index_is_exact_slate_and_cannot_redirect_outside_standard_folder(self):
        from candidate_library import slate_id
        from slate_catalog import record_preparation
        root=self.root/'candidates';root.mkdir()
        catalog=self.root/'slate-catalog.sqlite'
        external=self.root/'external.sdlib';prepare(external,self.players)
        record_preparation(catalog,dict(digest='external',state='Prepared candidates',library_saved=True,
            slate_id=slate_id(self.players,'showdown'),library=str(external)))
        inside=root/'other-slate-hint.sdlib';prepare(inside,self.players)
        record_preparation(catalog,dict(digest='other',state='Prepared candidates',library_saved=True,
            slate_id='other-slate',library=str(inside)))
        before=catalog.read_bytes()
        with patch.object(Path,'iterdir',return_value=iter([])):
            self.assertEqual(list(discover_libraries((),root,self.players,'showdown',50000)),[])
        self.assertEqual(catalog.read_bytes(),before)

    def test_cancellation_and_time_budget_prevent_probing(self):
        with patch('showdown_library.validate_library') as validate:
            self.assertEqual(self.discover(cancelled=lambda: True), [])
            self.assertEqual(self.discover(seconds=0), [])
            validate.assert_not_called()

    def test_external_path_is_remembered_and_clearable_without_disk_search(self):
        settings = QtCore.QSettings(str(self.root/'settings.ini'), QtCore.QSettings.IniFormat)
        settings.setValue('build/known_libraries', '{broken')
        for i in range(30): remember_library(settings, self.root/f'{i}.sdlib')
        remember_library(settings, self.root/'29.sdlib')
        rows = remembered_libraries(settings)
        self.assertEqual(len(rows), 24)
        self.assertTrue(rows[0].endswith('29.sdlib'))
        self.assertEqual(len(set(rows)), 24)

    def test_real_completed_library_is_loaded_automatically_without_attachment(self):
        from main_window import LineupBuildWorker
        path=self.root/'complete.sdlib';prepare(path,self.players)
        worker=LineupBuildWorker(copy.deepcopy(self.players),kind='showdown',num_lineups=2,
            salary_cap=50000,salary_strategy='Flexible',sim_enabled=True,compute_mode='Deep',library_folder=str(self.root))
        errors=[];worker.error.connect(errors.append)
        def solve(*args):
            self.assertTrue(worker.library_candidates)
            self.assertEqual(worker.candidate_library,str(path.resolve()))
            return dict(kind='showdown',lineups=[])
        with patch('showdown_simulation.run_deep_showdown',side_effect=solve) as solver:
            worker.run()
        self.assertFalse(errors);solver.assert_called_once()
        self.assertTrue(worker.library_report['automatic_discovery'])

    def test_automatic_load_failure_falls_back_but_explicit_selection_fails(self):
        from main_window import LineupBuildWorker
        def run(explicit):
            worker = LineupBuildWorker(copy.deepcopy(self.players), kind='showdown', num_lineups=1,salary_cap=50000,
                sim_enabled=True, compute_mode='Deep', automatic_libraries=('remembered.sdlib',),
                candidate_library='explicit.sdlib' if explicit else '')
            results, errors, progress = [], [], []
            worker.finished.connect(results.append)
            worker.error.connect(errors.append)
            worker.progress.connect(lambda a,b,text: progress.append(text))
            with patch('build_preparation.discover_libraries', return_value=iter(['remembered.sdlib'])), \
                 patch.object(worker, '_load_candidate_library', side_effect=ValueError('coverage conflict')), \
                 patch('showdown_simulation.run_deep_showdown', return_value=dict(kind='showdown',lineups=[])):
                worker.run()
            return worker, results, errors, progress
        worker, results, errors, progress = run(False)
        self.assertTrue(results)
        self.assertFalse(errors)
        self.assertEqual(worker.candidate_library, '')
        self.assertTrue(any('fresh candidate search' in text for text in progress))
        detail=worker.preparation.rows[2]['detail']
        self.assertIn('Libraries rejected by loader: 1',detail)
        self.assertIn('Coverage conflict: 1',detail)
        from build_diagnostics import create_build_diagnostic,format_build_report
        record=create_build_diagnostic(context={},timing_report=dict(build_preparation=worker.preparation.report()))
        self.assertIn('Coverage conflict: 1',format_build_report(record))
        _, results, errors, _ = run(True)
        self.assertFalse(results)
        self.assertTrue(errors)

    def test_auto_reuse_runs_existing_loader_and_records_provenance(self):
        from main_window import LineupBuildWorker
        worker = LineupBuildWorker(copy.deepcopy(self.players), kind='showdown', num_lineups=1,salary_cap=50000,
            sim_enabled=True, compute_mode='Deep', automatic_libraries=('saved.sdlib',))
        def load(profile):
            worker.library_candidates = [object()]
            worker.library_report = dict(accepted=1)
            return True
        with patch('build_preparation.discover_libraries', return_value=iter(['saved.sdlib'])), \
             patch.object(worker, '_load_candidate_library', side_effect=load) as loader, \
             patch('showdown_simulation.run_deep_showdown', return_value=dict(kind='showdown',lineups=[])):
            worker.run()
        loader.assert_called_once()
        self.assertTrue(worker.library_report['automatic_discovery'])
        self.assertEqual(worker.library_report['library_path'], 'saved.sdlib')

    def test_fast_and_saved_repair_do_not_discover_libraries(self):
        from main_window import LineupBuildWorker
        for options in (dict(compute_mode='Fast'), dict(compute_mode='Deep',repair_source='saved')):
            worker = LineupBuildWorker(copy.deepcopy(self.players),kind='showdown',num_lineups=1,salary_cap=50000,
                sim_enabled=True,automatic_libraries=('saved.sdlib',),**options)
            # Stop immediately after preparation without doing an optimizer run.
            with patch('build_preparation.discover_libraries') as discovery, \
                 patch('showdown_simulation.run_deep_showdown',return_value=dict(kind='showdown',lineups=[])), \
                 patch('optimizers.ShowdownOptimizer.build_lineups',return_value=[]):
                worker.run()
            discovery.assert_not_called()
            self.assertIn('Automatic discovery',worker.preparation.rows[2]['detail'])


class UnifiedPreparationTests(unittest.TestCase):
    def worker(self, **options):
        from main_window import LineupBuildWorker
        return LineupBuildWorker(copy.deepcopy(players()), kind='showdown', num_lineups=1,
            salary_cap=50000, sim_enabled=True, compute_mode='Deep', **options)

    def test_preparation_order_and_receipt_before_existing_deep_solver(self):
        worker = self.worker()
        results, progress = [], []
        worker.finished.connect(results.append)
        worker.progress.connect(lambda a,b,text: progress.append(text))
        def solve(*args):
            self.assertEqual([r['stage'] for r in worker.preparation.rows],
                             ['captain','history','library','eligibility'])
            self.assertTrue(all(r['state']=='Completed' for r in worker.preparation.rows))
            return dict(kind='showdown',lineups=[])
        with patch('showdown_simulation.run_deep_showdown', side_effect=solve) as solver:
            worker.run()
        solver.assert_called_once()
        self.assertEqual(len(results),1)
        self.assertEqual(len(results[0]['timing_report']['build_preparation']),4)
        self.assertEqual(len([t for t in progress if t.startswith('Preparing ') and '/4' in t]),4)

    def test_cancel_before_start_never_enters_search_or_discovery(self):
        worker = self.worker(automatic_libraries=('saved.sdlib',))
        results = []
        worker.finished.connect(results.append)
        worker.request_cancel()
        with patch('showdown_simulation.run_deep_showdown') as solver, \
             patch('build_preparation.discover_libraries') as discovery:
            worker.run()
        solver.assert_not_called(); discovery.assert_not_called()
        self.assertEqual(len(results),1)
        self.assertTrue(results[0]['cancelled'])
        self.assertEqual(results[0]['lineups'],[])

    def test_cancel_after_library_validation_stops_before_eligibility_and_solver(self):
        worker = self.worker(candidate_library='selected.sdlib')
        results=[]
        worker.finished.connect(results.append)
        def load(profile):
            worker.request_cancel()
            return True
        with patch.object(worker,'_load_candidate_library',side_effect=load), \
             patch('showdown_simulation.run_deep_showdown') as solver, \
             patch('nfl_eligibility.eligible_players') as eligibility:
            worker.run()
        solver.assert_not_called(); eligibility.assert_not_called()
        self.assertEqual(len(results),1)
        self.assertTrue(results[0]['cancelled'])
        self.assertEqual([r['stage'] for r in results[0]['timing_report']['build_preparation']],
                         ['captain','history','library'])

    def test_history_failure_stops_before_library_work(self):
        worker = self.worker(field_calibration={'showdown_field_profile':{'requested':True}},
                             automatic_libraries=('saved.sdlib',))
        errors=[]
        worker.error.connect(errors.append)
        with patch('showdown_history_field.revalidate_profile',side_effect=ValueError('changed evidence')), \
             patch('build_preparation.discover_libraries') as discovery, \
             patch('showdown_simulation.run_deep_showdown') as solver:
            worker.run()
        discovery.assert_not_called(); solver.assert_not_called()
        self.assertEqual(len(errors),1)
        self.assertIn('changed evidence', errors[0])

    def test_fast_without_sim_keeps_preparation_out_of_sim_report(self):
        from main_window import LineupBuildWorker
        worker=LineupBuildWorker(copy.deepcopy(players()),kind='showdown',num_lineups=1,
                                 salary_cap=50000,sim_enabled=False,compute_mode='Fast')
        results=[];worker.finished.connect(results.append)
        with patch('optimizers.ShowdownOptimizer.build_lineups',return_value=[]):
            worker.run()
        self.assertEqual(len(results),1)
        self.assertNotIn('build_preparation',results[0].get('sim_report', {}))
        self.assertEqual(len(results[0]['timing_report']['build_preparation']),4)
