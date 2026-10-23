from test_environment import install
install()
import copy
import faulthandler
import threading
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from main_window import MainWindow
from csv_loading import CsvLoadController, CsvLoadWorker, CsvLoadProgressDialog, detect_sport, detect_mode


def players(position='WR'):
    return [dict(Name='Synthetic player', FlexID='123', FlexNamePlusID='Synthetic player (123)',
                 Position=position, Team='SEA', FlexSalary=5000, FlexProjection=10,
                 CptSalary=7500, CptProjection=15)]


class CsvLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        # Diagnose platform-specific Qt waits without extending or weakening tests.
        faulthandler.dump_traceback_later(60, repeat=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)

    def window(self):
        w=MainWindow()
        w.background_queue.timer.stop();w.idle_backup.timer.stop()
        w.players=players();w.last_showdown=[['previous lineup']]
        w.last_sim_report={'previous':'report'}
        w.csv_load=CsvLoadController(w)
        self.addCleanup(w.close)
        return w

    def wait(self, predicate, seconds=8):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.003)
        self.assertTrue(predicate())

    def slow(self, entered, release, threads=None):
        def context(ps):
            if threads is not None:threads.append(QtCore.QThread.currentThread()==self.app.thread())
            entered.set()
            if not release.wait(5):raise AssertionError('test failed to release source')
            ps[0]['FlexProjection']=12
            return {'total':len(ps),'sleeper_state':'ok','usage_state':'ok'}
        return context

    def test_progress_updates_do_not_dispatch_queued_retirement(self):
        w=self.window();dialog=CsvLoadProgressDialog(w)
        self.addCleanup(lambda:dialog.done(QtWidgets.QDialog.Accepted))
        dialog.show();self.app.processEvents()
        dialog.setValue(1)
        dispatched=[]
        QtCore.QTimer.singleShot(0,lambda:dispatched.append('retirement'))
        dialog.setLabelText('Preparing <synthetic> CSV')
        dialog.setValue(50)
        self.assertEqual(dispatched,[])
        self.assertEqual(dialog.bar.value(),50)
        self.assertEqual(dialog.label.textFormat(),QtCore.Qt.PlainText)
        self.assertEqual(dialog.windowModality(),QtCore.Qt.WindowModal)
        self.app.processEvents();self.assertEqual(dispatched,['retirement'])

    def test_progress_cancel_stays_visible_until_controller_retires(self):
        w=self.window();dialog=CsvLoadProgressDialog(w);requests=[]
        self.addCleanup(lambda:dialog.done(QtWidgets.QDialog.Accepted))
        dialog.canceled.connect(lambda:requests.append('cancel'))
        dialog.show();self.app.processEvents()
        dialog.cancel_button.click();self.assertTrue(dialog.isVisible())
        dialog.reject();self.assertTrue(dialog.isVisible())
        dialog.close();self.assertTrue(dialog.isVisible())
        self.assertEqual(requests,['cancel']*3)
        dialog.done(QtWidgets.QDialog.Accepted)
        self.assertFalse(dialog.isVisible());self.assertEqual(len(requests),3)

    def test_successful_new_csv_clears_old_explicit_library_but_failure_retains_it(self):
        w=self.window();w._candidate_library='previous-slate.sdlib';w._candidate_library_allow_partial=True
        with patch.object(w,'_start_ownership_sim'):
            w._apply_csv_payload(dict(path='new.csv',players=players(),sport='NFL',mode='classic'))
        self.assertEqual(w._candidate_library,'');self.assertFalse(w._candidate_library_allow_partial)
        w._candidate_library='current-slate.sdlib';w._candidate_library_allow_partial=True
        with patch('csv_loading.read_players_csv',side_effect=ValueError('invalid CSV')),patch('csv_loading.QtWidgets.QMessageBox.critical'):
            w.csv_load.start('invalid.csv');self.wait(lambda:not w.csv_load.busy)
        self.assertEqual(w._candidate_library,'current-slate.sdlib');self.assertTrue(w._candidate_library_allow_partial)

    def test_real_worker_keeps_ui_responsive_and_commits_on_gui_after_retirement(self):
        w=self.window();old=w.players;entered=threading.Event();release=threading.Event();threads=[]
        self.addCleanup(release.set)
        ticks=[];timer=QtCore.QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start()
        self.addCleanup(timer.stop)
        original=w._apply_csv_payload;applied=[]
        def commit(payload):
            applied.append((QtCore.QThread.currentThread()==self.app.thread(),w.csv_load.thread is None))
            original(payload)
        with patch('csv_loading.read_players_csv',return_value=players()), \
             patch('csv_loading.apply_auto_nfl_context',side_effect=self.slow(entered,release,threads)), \
             patch.object(w,'_start_ownership_sim') as own,patch.object(w,'_apply_csv_payload',side_effect=commit):
            self.assertTrue(w.csv_load.start('synthetic.csv'));self.wait(entered.is_set)
            self.assertIs(w.players,old)
            begin=len(ticks);until=time.monotonic()+.15
            while time.monotonic()<until:self.app.processEvents();time.sleep(.003)
            self.assertGreaterEqual(len(ticks)-begin,10)
            self.assertTrue(w._snapshot_busy())
            release.set();self.wait(lambda:not w.csv_load.busy)
        self.assertEqual(threads,[False]);self.assertEqual(applied,[(True,True)])
        self.assertEqual(w.players[0]['FlexProjection'],12)
        self.assertEqual(w.last_sim_report,{})
        self.assertEqual(w._contest_mode(),'classic');own.assert_called_once()
        self.assertTrue(own.call_args.kwargs['reuse_prepared'])
        self.assertIn('nfl_context',w.last_csv_load_timing)
        self.assertIn('wait',w.last_csv_load_timing)
        self.assertGreaterEqual(w.last_csv_load_timing['total'],w.last_csv_load_timing['preparation'])
        self.assertTrue(all(v>=0 for v in w.last_csv_load_timing.values()))

    def test_cancel_during_source_wait_retains_slate_reports_and_waits_for_retirement(self):
        w=self.window();old=w.players;entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch('csv_loading.read_players_csv',return_value=players()), \
             patch('csv_loading.apply_auto_nfl_context',side_effect=self.slow(entered,release)), \
             patch.object(w,'_apply_csv_payload') as commit:
            w.csv_load.start('synthetic.csv');self.wait(entered.is_set)
            w.csv_load.cancel();self.assertTrue(w.csv_load.busy)
            self.assertIsNotNone(w.csv_load.thread)
            release.set();self.wait(lambda:not w.csv_load.busy)
        commit.assert_not_called();self.assertIs(w.players,old)
        self.assertEqual(w.last_sim_report,{'previous':'report'})
        self.assertEqual(w.last_showdown,[['previous lineup']])

    def test_cancel_queued_completion_suppresses_apply(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch('csv_loading.read_players_csv',return_value=players()), \
             patch('csv_loading.apply_auto_nfl_context',side_effect=self.slow(entered,release)), \
             patch.object(w,'_apply_csv_payload') as commit:
            w.csv_load.start('synthetic.csv');self.wait(entered.is_set)
            w.csv_load.worker.finished.connect(lambda payload:w.csv_load.cancel())
            release.set();self.wait(lambda:not w.csv_load.busy)
        commit.assert_not_called()

    def test_failure_retains_previous_slate(self):
        w=self.window();old=w.players
        with patch('csv_loading.read_players_csv',side_effect=ValueError('invalid CSV')), \
             patch('csv_loading.apply_auto_nfl_context') as source, \
             patch('csv_loading.QtWidgets.QMessageBox.critical') as error:
            w.csv_load.start('invalid.csv');self.wait(lambda:not w.csv_load.busy)
        source.assert_not_called();error.assert_called_once();self.assertIs(w.players,old)
        self.assertEqual(w.last_sim_report,{'previous':'report'})

    def test_current_slate_replacement_discards_stale_load_and_duplicate_request(self):
        w=self.window();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch('csv_loading.read_players_csv',return_value=players()), \
             patch('csv_loading.apply_auto_nfl_context',side_effect=self.slow(entered,release)), \
             patch.object(w,'_apply_csv_payload') as commit:
            w.csv_load.start('one.csv');self.wait(entered.is_set)
            self.assertFalse(w.csv_load.start('two.csv'))
            replacement=players('QB');w.players=replacement
            release.set();self.wait(lambda:not w.csv_load.busy)
        commit.assert_not_called();self.assertIs(w.players,replacement)

    def test_waits_for_background_retirement_and_can_cancel_before_reading(self):
        w=self.window();q=w.background_queue;stops=[]
        q.request('synthetic','Synthetic maintenance',10,lambda:True,lambda:stops.append(True))
        q.pump()
        with patch('csv_loading.read_players_csv') as read:
            w.csv_load.start('unused.csv')
            self.assertEqual(stops,[True]);self.assertIsNone(w.csv_load.thread)
            self.assertTrue(w.csv_load.busy)
            w.csv_load.cancel();q.release('synthetic');w.csv_load.dispatch()
        read.assert_not_called();self.assertFalse(w.csv_load.busy)

    def test_close_during_fetch_waits_and_does_not_apply(self):
        w=self.window();w.show();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch('csv_loading.read_players_csv',return_value=players()), \
             patch('csv_loading.apply_auto_nfl_context',side_effect=self.slow(entered,release)), \
             patch.object(w,'_apply_csv_payload') as commit:
            w.csv_load.start('synthetic.csv');self.wait(entered.is_set)
            w.close();self.assertTrue(w.isVisible());self.assertTrue(w.csv_load.busy)
            release.set();self.wait(lambda:not w.csv_load.busy)
            self.wait(lambda:not w.isVisible())
        commit.assert_not_called()

    def test_source_fallbacks_and_sport_detection_preserve_pipeline(self):
        for pos,sport in [('WR','NFL'),('OF','MLB'),('PG','NBA')]:
            with self.subTest(sport=sport):
                ps=players(pos);worker=CsvLoadWorker('synthetic.csv');results=[];worker.finished.connect(results.append)
                with patch('csv_loading.read_players_csv',return_value=ps), \
                     patch('csv_loading.apply_auto_nfl_context',side_effect=ValueError('source unavailable')) as nfl, \
                     patch('csv_loading.enrich_players_with_injuries') as injury, \
                     patch('csv_loading.apply_auto_mlb_context') as mlb:
                    worker.run()
                self.assertEqual(len(results),1);result=results[0]
                self.assertEqual(result['sport'],sport);self.assertFalse(result.get('failed'))
                self.assertFalse(ps[0]['LockFlex']);self.assertIsNone(ps[0]['MaxPct'])
                if sport=='NFL':nfl.assert_called_once();injury.assert_not_called();mlb.assert_not_called()
                elif sport=='MLB':injury.assert_called_once();mlb.assert_called_once();nfl.assert_not_called()
                else:
                    nfl.assert_not_called();injury.assert_not_called();mlb.assert_not_called()
                    self.assertEqual(ps[0]['InjuryStatus'],'')
        ps=players();self.assertEqual(detect_mode(ps),'classic')
        ps[0]['CptID']='456';self.assertEqual(detect_mode(ps),'showdown')

    def test_load_action_does_not_open_another_file_during_existing_computation(self):
        w=self.window()
        with patch.object(w,'_snapshot_busy',return_value=True), \
             patch('main_window.QtWidgets.QFileDialog.getOpenFileName') as chooser:
            w.on_load_csv()
        chooser.assert_not_called()
