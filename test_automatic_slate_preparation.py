from test_environment import install
install()
import datetime as dt
import unittest
from unittest.mock import Mock, patch
from PyQt5 import QtWidgets
from main_window import MainWindow
from test_showdown_library import players


class AutomaticSlatePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w=MainWindow();self.addCleanup(w.close)
        w.background_queue.timer.stop();w.idle_backup.timer.stop();w.slate_discovery.timer.stop()
        w.players=players()
        date=(dt.date.today()+dt.timedelta(days=1)).strftime('%m/%d/%Y')
        for p in w.players:p['GameInfo']='A@B '+date+' 08:15PM ET'
        w._snapshot_busy=Mock(return_value=False)
        w._capture_snapshot=Mock(return_value={'captured':True})
        w.app_settings.setValue('build/smart_preparation',True)
        w.idle_preparation.prepare_slate=Mock(return_value=True)
        return w,w.idle_preparation

    def test_load_waits_for_ownership_then_captures_once_without_prompt(self):
        w,c=self.window();w._snapshot_busy.return_value=True
        c.loaded_slate();c.capture_loaded_slate();c.prepare_slate.assert_not_called()
        w._snapshot_busy.return_value=False
        c.capture_loaded_slate();c.prepare_slate.assert_called_once()
        self.assertEqual(c.prepare_slate.call_args.args[0],{'captured':True})
        self.assertFalse(c.auto_timer.isActive())

    def test_slate_replaced_or_replayed_before_capture_is_discarded(self):
        for replay in (False,True):
            w,c=self.window();c.loaded_slate()
            if replay:w._snapshot_replay=True
            else:w.players=list(w.players)
            c.capture_loaded_slate();c.prepare_slate.assert_not_called()
            self.assertFalse(c.auto_timer.isActive())

    def test_disabled_paused_historical_unknown_and_classic_skip(self):
        for case in ('disabled','paused','historical','unknown','classic'):
            w,c=self.window()
            if case=='disabled':w.app_settings.setValue('build/smart_preparation',False)
            elif case=='paused':c.paused=True
            elif case=='historical':
                for p in w.players:p['GameInfo']='A@B 01/01/2000 08:15PM ET'
            elif case=='unknown':
                for p in w.players:p['GameInfo']=''
            else:
                for p in w.players:p.pop('CptID',None)
            c.loaded_slate();c.capture_loaded_slate();c.prepare_slate.assert_not_called()
            self.assertFalse(c.auto_timer.isActive())

    def test_existing_explicit_job_is_retained_until_retirement(self):
        w,c=self.window();c.request_data={'existing':True};c.loaded_slate()
        c.capture_loaded_slate();c.prepare_slate.assert_not_called()
        self.assertEqual(c.request_data,{'existing':True})
        c.request_data=None;c.capture_loaded_slate();c.prepare_slate.assert_called_once()

    def test_capture_error_reports_task_without_modal_and_does_not_retry(self):
        w,c=self.window();w._capture_snapshot.side_effect=ValueError('Invalid captured rules')
        c.loaded_slate()
        with patch('PyQt5.QtWidgets.QMessageBox.warning') as warning:
            c.capture_loaded_slate();warning.assert_not_called()
        self.assertTrue(any(r['state']=='Needs review' for r in w.tasks.rows.values()))
        self.assertFalse(c.auto_timer.isActive());c.prepare_slate.assert_not_called()

    def test_cancel_discards_automatic_request(self):
        w,c=self.window();c.loaded_slate();c.cancel()
        self.assertIsNone(c.auto_players);self.assertFalse(c.auto_timer.isActive())
