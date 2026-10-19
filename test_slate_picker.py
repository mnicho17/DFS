from test_environment import install
install()
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from slate_picker import SlateDiscoveryController, SlatePicker
from slate_catalog import scan


class SlatePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from task_dashboard import TaskHub
        from background_queue import BackgroundQueue
        from types import SimpleNamespace
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.owner=QtWidgets.QWidget()
        self.owner.tasks=TaskHub(self.owner)
        self.owner.app_settings=QtCore.QSettings(str(Path(self.tmp.name)/'settings.ini'),QtCore.QSettings.IniFormat)
        self.owner.background_queue=BackgroundQueue(self.owner)
        self.owner.background_queue.timer.stop()
        self.owner.idle_preparation=SimpleNamespace(ready=lambda:False)
        self.owner.players=[{'Name':'Current slate'}]
        self.controller=SlateDiscoveryController(self.owner,Path(self.tmp.name)/'catalog.sqlite')
        self.controller.timer.stop()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.shutdown();self.owner.background_queue.shutdown()
        end=time.monotonic()+3
        while self.controller.thread and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.owner.deleteLater();self.app.processEvents()

    def wait(self,predicate):
        end=time.monotonic()+5
        while not predicate() and time.monotonic()<end:
            self.app.processEvents();time.sleep(.002)
        self.assertTrue(predicate())

    def test_picker_browse_without_configured_folder(self):
        dialog=SlatePicker(self.controller,self.owner)
        self.addCleanup(dialog.deleteLater)
        self.assertIn('Browse\u2026',[button.text() for button in dialog.findChildren(QtWidgets.QPushButton)])
        with patch('slate_picker.QtWidgets.QFileDialog.getOpenFileName',return_value=('chosen.csv','')):
            dialog.browse()
        self.assertEqual(dialog.path,'chosen.csv')
        self.assertEqual(dialog.result(),QtWidgets.QDialog.Accepted)

    def test_discovery_waits_for_idle_manual_refresh_runs_detached_and_preserves_slate(self):
        folder=Path(self.tmp.name)/'salaries';folder.mkdir()
        self.owner.app_settings.setValue('learning/salary_folder',str(folder))
        source=self.owner.players;threads=[];entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def work(*args):
            threads.append(QtCore.QThread.currentThread()==self.app.thread())
            entered.set();release.wait(3)
            return dict(checked=0,reused=0)
        with patch('slate_picker.scan',side_effect=work):
            self.controller.request();self.owner.background_queue.pump()
            self.assertIsNone(self.controller.thread)
            self.controller.request(manual=True);self.owner.background_queue.pump()
            self.wait(entered.is_set)
            self.assertIsNotNone(self.owner.background_queue.active)
            release.set();self.wait(lambda:self.controller.thread is None)
        self.assertEqual(threads,[False])
        self.assertIs(self.owner.players,source)
        self.assertIsNone(self.owner.background_queue.active)

    def test_close_keeps_worker_owned_until_actual_retirement(self):
        folder=Path(self.tmp.name)/'salaries';folder.mkdir()
        self.owner.app_settings.setValue('learning/salary_folder',str(folder))
        entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def work(*args):
            entered.set();release.wait(3)
            return dict(cancelled=True)
        with patch('slate_picker.scan',side_effect=work):
            self.controller.request(manual=True);self.owner.background_queue.pump();self.wait(entered.is_set)
            self.assertTrue(self.controller.shutdown())
            self.assertIsNotNone(self.controller.thread)
            release.set();self.wait(lambda:self.controller.thread is None)
        self.assertTrue(self.controller.closed)


if __name__=='__main__':unittest.main()
