from test_environment import install
install()
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from main_window import MainWindow
from csv_loading import CsvLoadController
from entries_first import inspect_entries, choose, load_salary, metadata_key, profile_key
from entries_first_ui import EntriesFirstDialog, KEY, remembered
from test_entries_first import fixture, profile


class EntriesFirstUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=MainWindow();self.w.background_queue.timer.stop();self.w.idle_backup.timer.stop()
        self.w.slate_discovery.timer.stop()
        self.w.csv_load=CsvLoadController(self.w)
        self.addCleanup(self.w.close)
        self.path,self.salary=fixture(self.tmp.name,count=1)

    def wait(self,predicate,seconds=8):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.003)
        self.assertTrue(predicate())

    def dialog(self):
        d=EntriesFirstDialog(self.w,str(self.path));self.w._entries_setup_dialog=d
        self.addCleanup(d.reject)
        self.wait(lambda:d.task is None)
        return d

    def selection(self, strategy='default'):
        plan=inspect_entries(self.path)
        return choose(plan,'77','150-Max',strategy,self.w._load_contest_profiles(),self.path)

    def context(self,players):
        return {'total':len(players),'sleeper_state':'ok','usage_state':'ok'}

    def load(self,selection):
        with patch('csv_loading.apply_auto_nfl_context',side_effect=self.context), \
                patch.object(self.w,'_start_ownership_sim') as own:
            self.assertTrue(self.w.csv_load.start(selection['salary_path'],entry_setup=selection))
            self.wait(lambda:not self.w.csv_load.busy)
        return own

    def test_menu_entry_and_dialog_do_not_infer_settings_from_entry_count(self):
        action=self.w.findChild(QtWidgets.QAction,'buildTheseEntriesAction')
        self.assertIsNotNone(action)
        before=copy.deepcopy(self.w._current_build_recipe())
        d=self.dialog()
        self.assertEqual(d.contest.currentData(),'77')
        self.assertEqual(d.limit.currentData(),'');self.assertEqual(d.strategy.currentData(),'')
        self.assertFalse(d.load.isEnabled())
        self.assertEqual(self.w._current_build_recipe(),before)
        d.limit.setCurrentIndex(d.limit.findData('150-Max'))
        d.strategy.setCurrentIndex(d.strategy.findData('default'))
        self.assertTrue(d.load.isEnabled())
        d.accept_selection();self.assertEqual(d.selection['contest']['count'],1)
        self.assertEqual(self.w._current_build_recipe(),before)

    def test_successful_commit_sets_exact_count_limit_and_default_without_auto_generation(self):
        self.w._store_contest_profiles({'Cash':profile()},'Cash')
        self.w._set_contest_objective('DOUBLE_UP')
        self.w.spin_zero_qb_max.setValue(12.5)
        selection=self.selection();applied=[];original=self.w._apply_csv_payload
        def commit(payload):
            applied.append((QtCore.QThread.currentThread()==self.app.thread(),self.w.csv_load.thread is None))
            original(payload)
        with patch.object(self.w,'_apply_csv_payload',side_effect=commit), \
                patch.object(self.w,'on_build_showdown') as generate,patch.object(self.w,'on_update_entries') as export:
            own=self.load(selection)
        self.assertEqual(applied,[(True,True)])
        self.assertEqual(self.w.spin_sd.value(),1)
        self.assertEqual(self.w.combo_field_preset.currentText(),'150-Max')
        self.assertFalse(self.w.chk_auto_entry_target.isChecked())
        self.assertEqual(self.w._current_contest_objective(),'TOURNAMENT')
        self.assertIsNone(self.w._active_contest_profile())
        self.assertEqual(self.w.spin_zero_qb_max.value(),12.5)
        self.assertIn('captured entry',self.w.lbl_simple_build_summary.text())
        own.assert_called_once();self.assertTrue(own.call_args.kwargs['reuse_prepared'])
        generate.assert_not_called();export.assert_not_called()
        self.assertTrue(remembered(self.w))

    def test_explicit_profile_applies_exact_count_and_existing_deep_route(self):
        self.w._store_contest_profiles({'Cash':profile()},'')
        selection=self.selection('profile:Cash')
        self.assertFalse(self.w.combo_nfl_compute_mode.currentText().startswith('Deep'))
        self.load(selection)
        self.assertEqual(self.w._active_contest_profile()['user_entries'],1)
        self.assertEqual(self.w._current_contest_objective(),'DOUBLE_UP')
        self.assertTrue(self.w.chk_nfl_contest_sim.isChecked())
        self.assertTrue(self.w.combo_nfl_compute_mode.currentText().startswith('Deep'))

    def test_remembered_choices_require_same_contest_metadata_and_unchanged_profile(self):
        self.load(self.selection())
        d=self.dialog();self.assertEqual(d.limit.currentData(),'150-Max')
        self.assertEqual(d.strategy.currentData(),'default');d.reject()
        self.path.write_text(self.path.read_text().replace('Tournament test','Changed name'),encoding='utf-8')
        d=self.dialog();self.assertEqual(d.limit.currentData(),'');d.reject()
        self.w._store_contest_profiles({'Cash':profile()},'')
        self.load(self.selection('profile:Cash'))
        changed=profile();changed['payouts']='1-30=2'
        self.w._store_contest_profiles({'Cash':changed},'')
        d=self.dialog();self.assertEqual(d.strategy.currentData(),'')

    def test_failure_and_cancel_preserve_previous_slate_settings_and_mapping(self):
        self.load(self.selection());before=copy.deepcopy(self.w._current_build_recipe())
        receipt=self.w._entries_build_setup;players=self.w.players;saved=self.w.app_settings.value(KEY)
        selection=self.selection();self.path.write_bytes(self.path.read_bytes()+b'\n')
        with patch('csv_loading.QtWidgets.QMessageBox.critical'):
            self.load(selection)
        self.assertIs(self.w.players,players);self.assertIs(self.w._entries_build_setup,receipt)
        self.assertEqual(self.w._current_build_recipe(),before);self.assertEqual(self.w.app_settings.value(KEY),saved)
        selection=self.selection();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def context(ps):
            entered.set();release.wait(5);return self.context(ps)
        with patch('csv_loading.apply_auto_nfl_context',side_effect=context),patch.object(self.w,'_start_ownership_sim'):
            self.w.csv_load.start(selection['salary_path'],entry_setup=selection)
            self.wait(entered.is_set);self.w.csv_load.cancel();release.set()
            self.wait(lambda:not self.w.csv_load.busy)
        self.assertIs(self.w.players,players);self.assertIs(self.w._entries_build_setup,receipt)
        self.assertEqual(self.w._current_build_recipe(),before)

    def test_late_source_change_during_enrichment_discards_new_load(self):
        original=self.w.players;recipe=self.w._current_build_recipe();selection=self.selection()
        def context(ps):
            self.path.write_bytes(self.path.read_bytes()+b'\n');return self.context(ps)
        with patch('csv_loading.apply_auto_nfl_context',side_effect=context), \
                patch('csv_loading.QtWidgets.QMessageBox.critical') as error, \
                patch.object(self.w,'_start_ownership_sim') as own:
            self.w.csv_load.start(selection['salary_path'],entry_setup=selection)
            self.wait(lambda:not self.w.csv_load.busy)
        self.assertIs(self.w.players,original);self.assertEqual(self.w._current_build_recipe(),recipe)
        error.assert_called_once();own.assert_not_called();self.assertFalse(remembered(self.w))

    def test_inspection_worker_is_detached_responsive_and_waits_for_cancel_retirement(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        real=inspect_entries;threads=[]
        def inspect(path,stop):
            threads.append(QtCore.QThread.currentThread()==self.app.thread())
            entered.set();release.wait(5);return real(path,stop)
        with patch('entries_first_ui.inspect_entries',side_effect=inspect):
            d=EntriesFirstDialog(self.w,str(self.path));self.w._entries_setup_dialog=d
            self.wait(entered.is_set)
            ticks=[];timer=QtCore.QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start()
            until=time.monotonic()+.12
            while time.monotonic()<until:self.app.processEvents();time.sleep(.002)
            timer.stop();self.assertGreaterEqual(len(ticks),5)
            self.assertTrue(self.w._snapshot_busy())
            d.reject();self.assertIsNotNone(d.thread)
            release.set();self.wait(lambda:d.thread is None)
        self.assertEqual(threads,[False]);self.assertIsNone(d.plan);self.assertFalse(remembered(self.w))
        self.assertEqual(d.result(),QtWidgets.QDialog.Rejected)

    def test_inspection_queued_result_cancel_and_owner_close_cannot_apply_selection(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        real=inspect_entries
        def inspect(path,stop):
            entered.set();release.wait(5);return real(path,lambda:False)
        with patch('entries_first_ui.inspect_entries',side_effect=inspect):
            d=EntriesFirstDialog(self.w,str(self.path));self.w._entries_setup_dialog=d
            self.wait(entered.is_set)
            self.w.close();self.assertIsNotNone(d.thread)
            release.set();self.wait(lambda:d.thread is None)
        self.assertIsNone(d.plan);self.assertIsNone(d.selection);self.assertFalse(remembered(self.w))

    def test_regular_load_and_snapshot_replay_clear_entries_receipt(self):
        self.load(self.selection())
        snapshot=self.w._capture_snapshot()
        self.w._restore_snapshot(snapshot)
        self.assertIsNone(self.w._entries_build_setup)
        self.w._entries_build_setup={'previous':'receipt'}
        with patch.object(self.w,'_start_ownership_sim'):
            self.w._apply_csv_payload(dict(path='ordinary.csv',players=copy.deepcopy(self.w.players),sport='NFL',mode='showdown'))
        self.assertIsNone(self.w._entries_build_setup)


    def test_inspection_waits_for_background_retirement_and_can_cancel_queued(self):
        q=self.w.background_queue;stops=[]
        q.request('synthetic','Synthetic maintenance',10,lambda:True,lambda:stops.append(True));q.pump()
        with patch('entries_first_ui.inspect_entries') as read:
            d=EntriesFirstDialog(self.w,str(self.path));self.w._entries_setup_dialog=d
            self.assertEqual(stops,[True]);self.assertIsNone(d.thread)
            d.reject();q.release('synthetic');self.wait(lambda:d.task is None)
        read.assert_not_called();self.assertIsNone(d.selection)

    def test_changes_after_loading_are_labelled_without_rewriting_choices(self):
        self.load(self.selection())
        saved=self.w.app_settings.value(KEY)
        self.w.spin_sd.setValue(2)
        self.assertIn('setup changed',self.w.lbl_simple_build_summary.text())
        self.assertEqual(self.w.app_settings.value(KEY),saved)
        self.w.tabs_lineups.setCurrentIndex(1)
        self.assertNotIn('captured entry',self.w.lbl_simple_build_summary.text())

    def test_selection_is_detached_while_load_runs(self):
        selection=self.selection();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def context(ps):
            entered.set();release.wait(5);return self.context(ps)
        with patch('csv_loading.apply_auto_nfl_context',side_effect=context),patch.object(self.w,'_start_ownership_sim'):
            self.w.csv_load.start(selection['salary_path'],entry_setup=selection)
            self.wait(entered.is_set);selection['contest']['count']=99
            release.set();self.wait(lambda:not self.w.csv_load.busy)
        self.assertEqual(self.w.spin_sd.value(),1)
        self.assertEqual(self.w._entries_build_setup['contest']['count'],1)


    def test_profile_changed_during_load_is_rejected_before_any_slate_commit(self):
        self.w._store_contest_profiles({'Cash':profile()},'')
        selection=self.selection('profile:Cash');players=self.w.players;count=self.w.spin_sd.value()
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def context(ps):
            entered.set();release.wait(5);return self.context(ps)
        with patch('csv_loading.apply_auto_nfl_context',side_effect=context), \
                patch('csv_loading.QtWidgets.QMessageBox.critical') as error, \
                patch.object(self.w,'_start_ownership_sim') as own:
            self.w.csv_load.start(selection['salary_path'],entry_setup=selection)
            self.wait(entered.is_set)
            changed=profile();changed['payouts']='1-20=2'
            self.w._store_contest_profiles({'Cash':changed},'')
            release.set();self.wait(lambda:not self.w.csv_load.busy)
        self.assertIs(self.w.players,players);self.assertEqual(self.w.spin_sd.value(),count)
        self.assertEqual(self.w._load_contest_profiles()['Cash']['payouts'][0]['end'],20)
        self.assertFalse(remembered(self.w));own.assert_not_called();error.assert_called_once()
