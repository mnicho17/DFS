from test_environment import install
install()
import copy
import os
from contextlib import closing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtWidgets
from build_snapshots import create_snapshot
from slate_preparation import plan_preparation,screening_request
from showdown_library import prepare,status
from showdown_screening import prepare_screening,target
from showdown_library_ui import SlatePreparationWorker
from test_showdown_library import players
from portfolio_rules import player_key
from main_window import MainWindow


class SlatePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        env=patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':str(self.root/'data')});env.start();self.addCleanup(env.stop)
        self.folder=self.root/'libraries';self.library=self.root/'existing.sdlib'
        self.players=players()
        for i,p in enumerate(self.players):p['FlexSalary']=6500+i*200;p['CptSalary']=p['FlexSalary']*1.5
        self.snapshot=self.capture()

    def capture(self,rows=None,**recipe):
        return create_snapshot(rows or self.players,dict(sport='NFL',contest_kind='showdown',salary_cap=50000,
            salary_strategy='Flexible',requested_lineups=2,nfl_sim_scenarios=250,
            deep_compute=dict(candidates=16,field=20,screening=250),**recipe),{})

    def plan(self,known=None,**kw):
        return plan_preparation(self.snapshot,[str(self.library)] if known is None else known,self.folder,**kw)

    def screen(self,**kw):
        rows,request=screening_request(self.snapshot)
        return prepare_screening(self.library,rows,**request,**kw)

    def worker(self,known=None):
        worker=SlatePreparationWorker(self.snapshot,[str(self.library)] if known is None else known,self.folder,60,8*1024**3)
        results=[];errors=[];plans=[]
        worker.finished.connect(results.append);worker.error.connect(errors.append);worker.planned.connect(plans.append)
        worker.run()
        return results,errors,plans

    def test_missing_library_plan_is_stable_and_readonly(self):
        before=copy.deepcopy(self.snapshot)
        first=self.plan([]);second=self.plan([])
        self.assertEqual(first,second);self.assertEqual(first['stage'],'rosters')
        self.assertTrue(first['path'].endswith('.sdfull'));self.assertFalse(self.folder.exists())
        self.assertEqual(self.snapshot,before)

    def test_partial_full_library_is_resumed_and_all_captains_are_screened(self):
        from showdown_full_library import prepare as full_prepare
        first=self.plan([]);path=Path(first['path']);path.parent.mkdir()
        stop=[False]
        full_prepare(path,self.players,cancelled=lambda:stop[0],progress=lambda value:stop.__setitem__(0,True))
        self.assertFalse(status(path)['complete'])
        plan=self.plan([]);self.assertEqual(plan['stage'],'rosters');self.assertEqual(plan['path'],str(path))
        results,errors,plans=self.worker([])
        self.assertFalse(errors);self.assertTrue(results[0]['screening_complete'])
        self.assertTrue(status(path)['complete']);self.assertEqual(set(status(path)['captains']),{player_key(p) for p in self.players})

    def test_partial_single_library_resume_keeps_identity_and_adds_only_missing_rosters(self):
        first=prepare(self.library,self.players,max_candidates=4)
        plan=self.plan();self.assertEqual(plan['stage'],'rosters');self.assertEqual(plan['path'],str(self.library))
        results,errors,plans=self.worker()
        self.assertFalse(errors);self.assertEqual(status(self.library)['identity'],first['identity'])
        self.assertTrue(results[0]['screening_complete']);self.assertEqual(status(self.library)['saved'],168)

    def test_complete_rosters_only_queue_screening_without_repreparing(self):
        prepare(self.library,self.players);plan=self.plan();self.assertEqual(plan['stage'],'screening')
        with patch('showdown_library_ui.prepare',side_effect=AssertionError('rosters must be reused')):
            results,errors,plans=self.worker()
        self.assertFalse(errors);self.assertTrue(results[0]['screening_complete'])

    def test_valid_exact_screening_is_reused_without_any_preparation_or_sim(self):
        prepare(self.library,self.players);self.screen()
        plan=self.plan();self.assertEqual(plan['stage'],'ready');self.assertEqual(plan['screening_reused'],16)
        with patch('showdown_library_ui.prepare',side_effect=AssertionError('no roster rebuild')),patch('showdown_simulation.simulate_showdown',side_effect=AssertionError('no rescoring')):
            results,errors,plans=self.worker()
        self.assertFalse(errors);self.assertTrue(results[0]['screening_complete']);self.assertEqual(results[0]['screening_reused'],16)

    def test_partial_screening_is_resumed_through_existing_cache(self):
        prepare(self.library,self.players);stop=[False]
        self.screen(batch_size=5,cancelled=lambda:stop[0],progress=lambda value:stop.__setitem__(0,True))
        self.assertEqual(self.plan()['stage'],'screening')
        results,errors,plans=self.worker()
        self.assertFalse(errors);self.assertTrue(results[0]['screening_complete']);self.assertEqual(self.plan()['stage'],'ready')

    def test_projection_change_preserves_rosters_and_old_cache_but_requires_new_screening(self):
        prepare(self.library,self.players);self.screen();rows,kw=screening_request(self.snapshot)
        old_path,_=target(self.library,rows,**kw);old_bytes=old_path.read_bytes()
        self.players[0]['FlexProjection']+=2;self.snapshot=self.capture()
        self.assertEqual(self.plan()['stage'],'screening');self.assertEqual(old_path.read_bytes(),old_bytes)
        self.assertTrue(status(self.library)['complete'])

    def test_incompatible_salaries_and_dates_are_never_overwritten(self):
        prepare(self.library,self.players);before=self.library.read_bytes()
        for field,value in [('FlexSalary',9999),('GameInfo','A@B 10/12/2026 08:15PM ET')]:
            rows=copy.deepcopy(self.players)
            for row in rows:row[field]=value
            snap=self.capture(rows)
            plan=plan_preparation(snap,[str(self.library)],self.folder)
            self.assertNotEqual(plan['path'],str(self.library));self.assertEqual(plan['stage'],'rosters')
        self.assertEqual(self.library.read_bytes(),before)

    def test_missing_captain_coverage_does_not_enable_partial_use_or_change_locks(self):
        prepare(self.library,self.players,captain_keys=[player_key(self.players[0])])
        self.assertNotEqual(self.plan()['path'],str(self.library))
        rows=copy.deepcopy(self.players);rows[0]['LockCpt']=True;snap=self.capture(rows)
        plan=plan_preparation(snap,[str(self.library)],self.folder)
        self.assertEqual(plan['path'],str(self.library));self.assertEqual(plan['stage'],'screening')
        self.assertTrue(snap['inputs']['players'][0]['LockCpt'])

    def test_complete_screened_library_wins_over_partial_and_unscreened_choices(self):
        partial=self.root/'partial.sdlib';unscreened=self.root/'unscreened.sdlib'
        prepare(partial,self.players,max_candidates=2);prepare(unscreened,self.players)
        self.players[0]['LockCpt']=True;self.snapshot=self.capture()
        prepare(self.library,self.players,captain_keys=[player_key(self.players[0])]);self.screen()
        plan=self.plan([str(partial),str(unscreened),str(self.library)])
        self.assertEqual(plan['stage'],'ready');self.assertEqual(plan['path'],str(self.library))

    def test_corrupt_screening_stops_without_overwriting_evidence(self):
        prepare(self.library,self.players);self.screen();rows,kw=screening_request(self.snapshot)
        path,_=target(self.library,rows,**kw)
        with closing(sqlite3.connect(path)) as con:
            con.execute("UPDATE scores SET digest='damaged' WHERE id=0");con.commit()
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'damaged'):self.plan()
        results,errors,plans=self.worker()
        self.assertFalse(results);self.assertTrue(errors);self.assertEqual(path.read_bytes(),before)

    def test_cancelled_coverage_check_and_bad_automatic_target_create_nothing(self):
        with self.assertRaises(InterruptedError):self.plan([],cancelled=lambda:True)
        self.assertFalse(self.folder.exists())
        path=Path(self.plan([])['path']);path.parent.mkdir();path.write_text('damaged')
        with self.assertRaisesRegex(ValueError,'needs review'):self.plan([])
        self.assertEqual(path.read_text(),'damaged')

    def test_historical_profile_revalidation_blocks_ready_claim(self):
        prepare(self.library,self.players);self.screen()
        snap=create_snapshot(self.players,self.snapshot['inputs']['recipe'],{},dict(showdown_field_profile={'invalid':True}))
        with patch('showdown_history_field.revalidate_profile',side_effect=ValueError('historical evidence changed')) as validate:
            with self.assertRaisesRegex(ValueError,'historical evidence changed'):
                plan_preparation(snap,[str(self.library)],self.folder)
        validate.assert_called_once()

    def test_completed_higher_cap_library_is_reused_for_lower_cap_screening(self):
        prepare(self.library,self.players,salary_cap=60000)
        self.assertEqual(self.plan()['stage'],'screening')
        with patch('showdown_library_ui.prepare',side_effect=AssertionError('do not reprepare at a different cap')):
            results,errors,plans=self.worker()
        self.assertFalse(errors);self.assertTrue(results[0]['screening_complete'])

    def test_partial_different_cap_library_is_left_for_explicit_resume(self):
        prepare(self.library,self.players,salary_cap=60000,max_candidates=4)
        before=self.library.read_bytes();plan=self.plan()
        self.assertNotEqual(plan['path'],str(self.library));self.assertEqual(self.library.read_bytes(),before)



class PrepareSlateActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        env=patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':str(self.root/'data')});env.start();self.addCleanup(env.stop)
        self.w=MainWindow();self.addCleanup(self.w.close)
        self.w.idle_backup.timer.stop();self.w.background_queue.timer.stop()
        self.clock=[0];self.w.idle_backup.clock=lambda:self.clock[0];self.w.idle_backup.last_input=0
        rows=players()
        for i,p in enumerate(rows):p['FlexSalary']=6500+i*200;p['CptSalary']=p['FlexSalary']*1.5
        self.w.players=copy.deepcopy(rows)
        from simple_build_ui import sync_simple_build_panel
        sync_simple_build_panel(self.w)
        self.snapshot=create_snapshot(rows,dict(sport='NFL',contest_kind='showdown',salary_cap=50000,salary_strategy='Flexible',
            requested_lineups=2,nfl_sim_scenarios=250,deep_compute=dict(candidates=16,field=20,screening=250)),{})

    def wait(self):
        deadline=time.monotonic()+15
        while self.w.idle_preparation.thread is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(self.w.idle_preparation.thread)

    def test_action_captures_once_and_does_no_scanning_on_gui_thread(self):
        original=copy.deepcopy(self.snapshot);selected=getattr(self.w,'_candidate_library','')
        with patch.object(self.w,'_capture_snapshot',return_value=self.snapshot),patch('slate_preparation.plan_preparation',side_effect=AssertionError('no GUI-thread scan')):
            self.w.btn_prepare_slate.click();first=self.w.idle_preparation.request_data
            self.w.btn_prepare_slate.click();self.assertIs(self.w.idle_preparation.request_data,first)
        self.snapshot['inputs']['players'][0]['FlexSalary']=9999
        self.assertEqual(first['snapshot'],original);self.assertEqual(getattr(self.w,'_candidate_library',''),selected)
        self.assertIsNone(self.w.idle_preparation.thread)

    def test_real_automatic_worker_creates_missing_coverage_and_screening_then_second_request_reuses(self):
        with patch.object(self.w,'_capture_snapshot',return_value=self.snapshot),patch('data_paths.data_root',return_value=self.root):
            self.w.on_prepare_this_slate();self.clock[0]=61;self.w.background_queue.pump();self.wait()
            self.assertEqual(self.w.idle_preparation.state,'Completed',self.w.idle_preparation.message)
            paths=list((self.root/'history'/'candidates').glob('*.sdfull'));self.assertEqual(len(paths),1)
            self.assertTrue(status(paths[0])['complete'])
            with patch('showdown_full_library.prepare',side_effect=AssertionError('reuse completed coverage')),patch('showdown_simulation.simulate_showdown',side_effect=AssertionError('reuse completed screening')):
                self.w.on_prepare_this_slate();self.w.background_queue.pump();self.wait()
                self.assertEqual(self.w.idle_preparation.state,'Completed',self.w.idle_preparation.message)
                self.assertIn('reused',self.w.idle_preparation.message)
        self.assertEqual(self.w.history_refresh._reports,0)

    def test_unsupported_format_is_visible_without_queuing(self):
        self.w.tabs_lineups.setCurrentIndex(1)
        self.assertTrue(self.w.btn_prepare_slate.isHidden())
        with patch('PyQt5.QtWidgets.QMessageBox.warning') as warning:self.w.on_prepare_this_slate()
        warning.assert_called_once();self.assertIsNone(self.w.idle_preparation.request_data)
