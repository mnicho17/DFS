from test_environment import install
install()
import copy
from collections import Counter
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets
from build_snapshots import create_snapshot
from showdown_library import prepare,status,load_bounded
from showdown_library_ui import PreparationDialog
from snapshot_ui import SnapshotActions
from test_showdown_library import players,signatures
from portfolio_rules import player_key


class LibraryWindow(QtWidgets.QWidget,SnapshotActions):
    def __init__(self,rows):
        super().__init__();self.players=rows
        self.lbl_snapshot_data=QtWidgets.QLabel(self);self.status=QtWidgets.QStatusBar(self)
    def _snapshot_busy(self):return False
    def _current_sport(self):return 'NFL'
    def _contest_mode(self):return 'showdown'
    def _current_build_recipe(self):return dict(salary_cap=50000)


class PreparedIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'rosters.sdlib';self.players=players()

    def wait(self,predicate):
        deadline=time.monotonic()+15
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate())

    def locked(self):
        rows=copy.deepcopy(self.players);rows[0]['LockCpt']=rows[1]['LockCpt']=True
        return rows

    def test_scoped_preparation_completes_only_requested_captains_and_resumes(self):
        scope=[player_key(p) for p in self.players[:2]]
        first=prepare(self.path,self.players,captain_keys=scope,max_candidates=4)
        self.assertFalse(first['complete'])
        final=prepare(self.path,list(reversed(self.players)),captain_keys=list(reversed(scope)))
        self.assertTrue(final['complete']);self.assertEqual(final['checked'],42)
        self.assertEqual(final['structural_total'],42)
        with self.assertRaisesRegex(ValueError,'current Captain pool'):
            load_bounded(self.path,self.players,limit=16,salary_strategy='Flexible')

    def test_bounded_sampling_is_repeatable_captain_balanced_and_reads_fresh_inputs(self):
        current=self.locked();scope=[player_key(p) for p in current[:2]]
        prepare(self.path,self.players,captain_keys=scope)
        before=self.path.read_bytes();current[0]['CptProjection']=99
        first,report=load_bounded(self.path,current,limit=10,salary_strategy='Flexible')
        second,_=load_bounded(self.path,list(reversed(current)),limit=10,salary_strategy='Flexible')
        self.assertEqual(signatures(first),signatures(second));self.assertEqual(len(first),10)
        self.assertEqual(Counter(player_key(r['Captain']) for r in first),dict.fromkeys(scope,5))
        self.assertTrue(report['scan_complete']);self.assertEqual(report['valid_seen'],42)
        self.assertEqual(self.path.read_bytes(),before)
        for row in first:
            if player_key(row['Captain'])==scope[0]:self.assertEqual(row['Captain']['CptProjection'],99)

    def test_partial_library_requires_explicit_opt_in_and_cancellation_withholds_rows(self):
        prepare(self.path,self.players,max_candidates=9)
        current=copy.deepcopy(self.players);current[0]['LockCpt']=True
        with self.assertRaisesRegex(ValueError,'incomplete'):
            load_bounded(self.path,current,limit=4,salary_strategy='Flexible')
        rows,report=load_bounded(self.path,current,limit=4,salary_strategy='Flexible',allow_partial=True)
        self.assertEqual(len(rows),4);self.assertFalse(report['preparation_complete'])
        with self.assertRaises(InterruptedError):
            load_bounded(self.path,current,limit=4,salary_strategy='Flexible',allow_partial=True,cancelled=lambda:True)

    def test_unlocked_scan_excludes_setup_and_counts_library_only_twice(self):
        import showdown_library as library
        prepare(self.path,self.players)
        clock=[0.0];counts=[];original=library._read
        def slow_read(con,**kwargs):
            counts.append(kwargs.get('verify_count',True))
            result=original(con,**kwargs)
            clock[0]+=5.0  # Setup exceeds each Captain's scan allowance.
            return result
        with patch.object(library,'_read',side_effect=slow_read),patch.object(library.time,'monotonic',side_effect=lambda:clock[0]):
            rows,report=load_bounded(self.path,self.players,limit=16,seconds=.1,salary_strategy='Flexible')
        self.assertEqual(len(rows),16)
        self.assertTrue(report['scan_complete'])
        self.assertEqual(sum(counts),2)
        self.assertEqual(len(counts),len(self.players)+2)
        self.assertTrue(all(c['sampled']==2 for c in report['captain_sampling']))

    def test_bounded_scan_rejects_changed_saved_count_after_sampling(self):
        import sqlite3
        import showdown_library as library
        prepare(self.path,self.players)
        original=library.iter_candidates;changed=[False]
        def mutate_after_scan(*args,**kwargs):
            yield from original(*args,**kwargs)
            if not changed[0]:
                with sqlite3.connect(self.path) as con:
                    con.execute('DELETE FROM rosters WHERE (captain,flex) IN (SELECT captain,flex FROM rosters LIMIT 1)')
                changed[0]=True
        with patch.object(library,'iter_candidates',side_effect=mutate_after_scan):
            with self.assertRaisesRegex(ValueError,'saved count'):
                load_bounded(self.path,self.players,limit=16,salary_strategy='Flexible')

    def test_dialog_prepares_real_scoped_library_off_gui_thread(self):
        rows=self.locked()
        snapshot=create_snapshot(rows,dict(sport='NFL',contest_kind='showdown',salary_cap=50000),{})
        dialog=PreparationDialog(snapshot);dialog.path.setText(str(self.path))
        seen=[]
        from showdown_library_ui import prepare as actual
        def checked(*args,**kwargs):
            seen.append(threading.get_ident()!=gui);return actual(*args,**kwargs)
        gui=threading.get_ident()
        with patch('showdown_library_ui.prepare',side_effect=checked):
            dialog.begin();self.wait(lambda:dialog.thread is None)
        self.assertEqual(seen,[True]);self.assertTrue(status(self.path)['complete'])
        self.assertEqual(status(self.path)['checked'],42)
        self.assertIn('complete',dialog.message.text());dialog.close();dialog.deleteLater()

    def test_dialog_close_waits_for_worker_retirement(self):
        snapshot=create_snapshot(self.players,dict(sport='NFL',contest_kind='showdown',salary_cap=50000),{})
        dialog=PreparationDialog(snapshot);dialog.path.setText(str(self.path));dialog.show()
        entered=threading.Event();release=threading.Event()
        def slow(path,rows,**kwargs):
            entered.set();release.wait(8);return prepare(path,rows,**kwargs)
        try:
            with patch('showdown_library_ui.prepare',side_effect=slow):
                dialog.begin();self.wait(entered.is_set)
                dialog.close();self.assertIsNotNone(dialog.thread);self.assertTrue(dialog.isVisible())
                release.set();self.wait(lambda:dialog.thread is None and not dialog.isVisible())
        finally:
            release.set();self.wait(lambda:dialog.thread is None);dialog.close();dialog.deleteLater()

    def test_load_action_does_not_materialize_library_and_partial_decline_preserves_selection(self):
        prepare(self.path,self.players,max_candidates=9)
        window=LibraryWindow(self.players);window._candidate_library='previous.dfslib'
        with patch.object(QtWidgets.QFileDialog,'getOpenFileName',return_value=(str(self.path),'')),patch.object(QtWidgets.QMessageBox,'question',return_value=QtWidgets.QMessageBox.No):
            window.on_load_candidate_library()
        self.assertEqual(window._candidate_library,'previous.dfslib')
        with patch.object(QtWidgets.QFileDialog,'getOpenFileName',return_value=(str(self.path),'')),patch.object(QtWidgets.QMessageBox,'question',return_value=QtWidgets.QMessageBox.Yes),patch('showdown_library.load_bounded',side_effect=AssertionError('GUI must not load rows')):
            window.on_load_candidate_library()
        self.assertEqual(window._candidate_library,str(self.path));self.assertTrue(window._candidate_library_allow_partial)
        window._refresh_snapshot_label=lambda:None;window.on_clear_candidate_library()
        self.assertFalse(window._candidate_library_allow_partial);window.deleteLater()

    def test_real_deep_worker_scores_prepared_candidates_without_optimizer_search(self):
        from main_window import LineupBuildWorker
        for i,p in enumerate(self.players):
            p['FlexSalary']=6500+i*200;p['CptSalary']=p['FlexSalary']*1.5
        current=self.locked();prepare(self.path,self.players,captain_keys=[player_key(p) for p in current[:2]])
        worker=LineupBuildWorker(current,kind='showdown',num_lineups=2,salary_cap=50000,
            salary_strategy='Flexible',sim_enabled=True,sim_scenarios=250,compute_mode='Deep',
            deep_time_limit_seconds=20,deep_options=dict(candidates=100,field=200,screening=250,shortlist=100),
            candidate_library=str(self.path))
        results=[];errors=[];worker.finished.connect(results.append);worker.error.connect(errors.append)
        with patch('showdown_simulation.ShowdownOptimizer',side_effect=AssertionError('Prepared builds must skip candidate searches')):
            worker.run()
        self.assertFalse(errors,errors);self.assertEqual(len(results),1)
        output=results[0]['lineups'];self.assertEqual(len(output),2)
        self.assertEqual(Counter(player_key(r['Captain']) for r in output),dict.fromkeys([player_key(p) for p in current[:2]],1))
        self.assertTrue(all(r.sim_metrics.get('sim_scenarios',0)>0 for r in output))
        self.assertEqual(results[0]['sim_report']['candidate_library']['type'],'prepared_showdown')


if __name__=='__main__':unittest.main()
