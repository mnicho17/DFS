from test_environment import install
install()
import threading
import unittest
from PyQt5 import QtCore, QtWidgets
from task_dashboard import TaskHub, task_panel


class FakeWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)
    def __init__(self):
        super().__init__()
        self.stop = threading.Event()


class TaskDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.time = [100.0]
        self.hub = TaskHub(clock=lambda: self.time[0])

    def watch(self):
        worker, thread = FakeWorker(), QtCore.QThread()
        binding = self.hub.watch('Test work', worker, thread, worker.stop.set)
        return worker, thread, binding

    def test_concurrent_tasks_and_recent_outcomes_remain_visible(self):
        one, two = self.hub.start('Build'), self.hub.start('History')
        self.hub.finish(one, 'Completed', '150 lineups')
        self.assertIsNone(self.hub.rows[two]['ended'])
        self.assertEqual(self.hub.rows[one]['detail'], '150 lineups')
        self.assertEqual(len(self.hub.rows), 2)
        self.assertIsNone(self.hub.rows[two]['cancel'])

    def test_finish_waits_for_retirement_and_cancel_is_idempotent(self):
        worker, thread, binding = self.watch()
        worker.progress.emit(1, 10, 'Scanning')
        self.hub.cancel(binding.key)
        self.hub.cancel(binding.key)
        self.assertTrue(worker.stop.is_set())
        worker.finished.emit(dict(cancelled=True))
        self.assertIsNone(self.hub.rows[binding.key]['ended'])
        thread.finished.emit()
        self.assertEqual(self.hub.rows[binding.key]['state'], 'Cancelled')
        self.assertFalse(self.hub.bindings)

    def test_failure_partial_and_incomplete_are_not_reported_as_success(self):
        for result, expected in ((dict(complete=False), 'Paused / partial'),
                                 (dict(requested=50,lineups=[]), 'Incomplete'),
                                 (dict(errors=['identity conflict']), 'Needs review')):
            worker, thread, binding = self.watch()
            worker.finished.emit(result)
            thread.finished.emit()
            self.assertEqual(self.hub.rows[binding.key]['state'], expected)
        worker, thread, binding = self.watch()
        worker.error.emit('Disk full')
        thread.finished.emit()
        self.assertEqual(self.hub.rows[binding.key]['state'], 'Failed')
        self.assertIn('Disk full', self.hub.rows[binding.key]['detail'])

    def test_external_pause_and_missing_completion_are_visible(self):
        worker, thread, binding = self.watch()
        worker.stop.set()
        worker.finished.emit({})
        thread.finished.emit()
        self.assertEqual(self.hub.rows[binding.key]['state'], 'Stopped')
        worker, thread, binding = self.watch()
        thread.finished.emit()
        self.assertEqual(self.hub.rows[binding.key]['state'], 'Failed')

    def test_stale_progress_does_not_guess_task_failure(self):
        panel = task_panel(self.hub, None)
        self.addCleanup(panel.deleteLater)
        key = self.hub.start('Long screening')
        self.time[0] += 70
        self.hub.changed.emit()
        from PyQt5.QtTest import QTest
        QTest.qWait(150)
        table = panel.findChild(QtWidgets.QTableWidget, 'taskTable')
        self.assertIn('no recent update', table.item(0,3).text())
        self.assertEqual(table.item(0,1).text(), 'Running')
        self.assertIsNone(self.hub.rows[key]['ended'])

    def test_closed_dashboard_disconnects_before_new_progress(self):
        panel = task_panel(self.hub, None)
        panel.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.hub.start('Still running after closing dashboard')
        self.hub.changed.emit()

    def test_history_is_bounded_without_dropping_active_work(self):
        active = self.hub.start('Active')
        for i in range(80):
            key = self.hub.start(str(i))
            self.hub.finish(key, 'Completed')
        self.assertIn(active, self.hub.rows)
        self.assertLessEqual(len(self.hub.rows), 32)

    def test_main_window_has_live_summary_and_reachable_dashboard(self):
        from main_window import MainWindow
        window = MainWindow()
        self.addCleanup(window.close)
        key = window.tasks.start('Screening')
        summary = window.findChild(QtWidgets.QLabel, 'taskSummary')
        self.assertIn('1 underway', summary.text())
        button = window.findChild(QtWidgets.QPushButton, 'taskDashboardButton')
        window.show()
        button.click()
        self.app.processEvents()
        self.assertTrue(window.task_dashboard.isVisible())
        window.tasks.finish(key, 'Completed', 'Screening saved')
        self.assertIn('0 underway', summary.text())
        window.on_clear_candidate_library()
        self.assertFalse(window.action_auto_library.isChecked())

    def test_real_worker_delivers_updates_on_gui_thread_and_retires(self):
        from PyQt5.QtTest import QTest
        class RunningWorker(FakeWorker):
            @QtCore.pyqtSlot()
            def run(self):
                self.progress.emit(5, 10, 'Working in background')
                self.finished.emit({})
        worker, thread = RunningWorker(), QtCore.QThread()
        worker.moveToThread(thread)
        binding = self.hub.watch('Background task', worker, thread, worker.stop.set)
        observed = []
        self.hub.changed.connect(lambda: observed.append(QtCore.QThread.currentThread()))
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.start()
        for _ in range(200):
            QTest.qWait(10)
            if self.hub.rows[binding.key]['ended'] is not None:
                break
        thread.quit()
        self.assertTrue(thread.wait(2000))
        self.assertEqual(self.hub.rows[binding.key]['state'], 'Completed')
        self.assertTrue(observed)
        self.assertTrue(all(t == self.app.thread() for t in observed))
