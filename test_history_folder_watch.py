from test_environment import install
install()
import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtTest import QTest
from history_refresh_ui import HistoryRefreshController
from history_folder_watch import QUIET_MS
from task_dashboard import TaskHub
from test_hindsight_evidence import SourceFixture


class HistoryFolderWatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def controller(self, folder=None):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root=Path(temp.name); source=Path(folder) if folder else root/'sources'; source.mkdir(exist_ok=True)
        settings=QtCore.QSettings(str(root/'settings.ini'),QtCore.QSettings.IniFormat)
        settings.setValue('learning/results_folder',str(source))
        owner=QtCore.QObject(); owner.tasks=TaskHub(owner)
        controller=HistoryRefreshController(settings,root/'history.sqlite',owner)
        self.addCleanup(lambda: controller.watch.shutdown())
        controller._test_owner=owner
        return controller,source

    def wait(self, predicate, seconds=10):
        deadline=time.monotonic()+seconds
        while not predicate() and time.monotonic()<deadline: QTest.qWait(10)
        self.assertTrue(predicate())

    def test_events_coalesce_and_queue_is_visible_before_existing_import_starts(self):
        controller,_=self.controller(); watch=controller.watch; watch.arm()
        with patch.object(controller,'start',return_value=True) as start:
            watch.schedule(); key=watch.task; watch.schedule()
            self.assertEqual(watch.task,key)
            self.assertEqual(watch.hub.rows[key]['state'],'Queued')
            self.assertEqual(watch.timer.interval(),QUIET_MS)
            self.assertFalse(start.called)
            watch.dispatch()
            start.assert_called_once_with(automatic=True)
            self.assertFalse(watch.pending)
            self.assertEqual(watch.hub.rows[key]['state'],'Dispatched')

    def test_disabled_auto_or_watch_preference_never_starts_or_writes_db(self):
        controller,_=self.controller(); watch=controller.watch
        for key in ('learning/auto_history_refresh','learning/watch_history_changes'):
            controller.settings.setValue(key,False)
            with patch.object(controller,'start') as start:
                watch.arm(); watch.schedule(); watch.dispatch()
                start.assert_not_called()
            self.assertFalse(watch.pending)
            self.assertFalse(watch.watcher.files()+watch.watcher.directories())
            controller.settings.setValue(key,True)
        self.assertFalse(Path(controller.db_path).exists())

    def test_reports_and_other_app_tasks_defer_until_retirement(self):
        controller,_=self.controller(); watch=controller.watch; watch.schedule()
        controller.report_started()
        with patch.object(controller,'start',return_value=True) as start:
            watch.dispatch(); start.assert_not_called()
            controller.report_finished()
            other=watch.hub.start('Lineup generation')
            watch.dispatch(); start.assert_not_called()
            watch.hub.finish(other,'Completed')
            watch.dispatch(); start.assert_called_once_with(automatic=True)

    def test_errors_and_cancellation_pause_until_explicit_resume(self):
        controller,_=self.controller(); watch=controller.watch
        for result in (dict(cancelled=True),dict(failed=True),dict(errors=['Drive unavailable'])):
            watch.resume(); watch.schedule(); watch.accept_result(result)
            self.assertTrue(watch.paused)
            self.assertFalse(watch.pending)
            watch.schedule(); self.assertFalse(watch.pending)
        watch.resume(); watch.schedule(); watch.hub.cancel(watch.task)
        self.assertTrue(watch.paused)
        self.assertFalse(watch.pending)

    def test_shutdown_and_disabling_cancel_pending_and_prevent_restart(self):
        controller,_=self.controller(); watch=controller.watch; watch.schedule()
        controller.set_enabled(False)
        self.assertFalse(watch.pending)
        controller.set_enabled(True); watch.schedule(); watch.shutdown()
        self.assertFalse(watch.pending)
        self.assertFalse(watch.watcher.files()+watch.watcher.directories())
        self.assertFalse(controller.start())
        watch.schedule(); self.assertFalse(watch.pending)

    def test_owned_archives_and_external_paths_are_excluded_and_failures_reported(self):
        controller,root=self.controller(); watch=controller.watch
        outside=Path(controller.db_path).parent/'unrelated.csv'
        # Put the owned snapshot folder below the configured root for this test.
        controller.db_path=str(root/'history.sqlite')
        owned=root/'analysis_sources'/'archived.csv'
        with patch.object(watch.watcher,'addPaths',return_value=[str(root)]) as add:
            watch.arm([str(owned),str(outside)])
        self.assertEqual(add.call_args.args[0],[str(root.resolve())])
        self.assertIn('full coverage',watch.message)

    def test_real_file_notification_schedules_without_scanning_contents(self):
        controller,root=self.controller(); path=root/'existing.csv'; path.write_text('a,b\n1,2\n')
        watch=controller.watch; watch.arm([str(path)])
        with patch.object(controller,'start') as start:
            path.write_text('a,b\n3,4\n')
            self.wait(lambda:watch.pending,3)
            start.assert_not_called()
            self.assertEqual(watch.timer.interval(),QUIET_MS)

    def test_new_contest_event_flows_through_real_import_and_keeps_saved_pair(self):
        fixture=SourceFixture(); self.addCleanup(fixture.close)
        controller,_=self.controller(fixture.results)
        controller.db_path=str(fixture.db)
        controller.settings.setValue('learning/salary_folder',str(fixture.salaries))
        controller.settings.setValue('learning/dk_username','Synthetic_User')
        controller.start(); self.wait(lambda:not controller.busy)
        self.assertFalse(controller.watch.paused,controller.text)
        saved_pairs=fixture.query('SELECT * FROM analysis_salary_pairs ORDER BY result_hash')
        source_bytes=(fixture.raw_result.read_bytes(),fixture.raw_salary.read_bytes())
        with fixture.raw_result.open(newline='',encoding='utf-8-sig') as handle: rows=list(csv.reader(handle))
        rows[1][1]='2000'; rows[1][5]='watch-contest'
        new=fixture.results/'watch-new.csv'
        with new.open('w',newline='',encoding='utf-8-sig') as handle: csv.writer(handle).writerows(rows)
        self.wait(lambda:controller.watch.pending,3)
        controller.watch.dispatch(); self.wait(lambda:not controller.busy)
        self.assertIn('1 new results',controller.text)
        self.assertFalse(controller.watch.paused,controller.text)
        self.assertIn(str(new.resolve()),controller.watch.watcher.files())
        after={row[0]:row for row in fixture.query('SELECT * FROM analysis_salary_pairs')}
        self.assertTrue(saved_pairs)
        for row in saved_pairs: self.assertEqual(after[row[0]],row)
        self.assertEqual((fixture.raw_result.read_bytes(),fixture.raw_salary.read_bytes()),source_bytes)

    def test_queued_refresh_is_visible_in_preparation_and_status_without_old_success_claim(self):
        from main_window import MainWindow
        from preparation_status_ui import preparation_rows
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        w=MainWindow(); self.addCleanup(w.close)
        w.history_refresh.settings.setValue('learning/results_folder',temp.name)
        w.history_refresh.text='History refresh complete.'
        w.history_refresh.watch.schedule()
        rows={row['key']:row for row in preparation_rows(w)}
        self.assertEqual(rows['history']['state'],'Refresh queued')
        self.assertIn('queued',w.history_refresh.status_line())
        self.assertNotIn('complete',w.history_refresh.status_line())
        self.assertTrue(any(row['state']=='Queued' for row in w.tasks.rows.values()))

    def test_ownerless_controller_does_not_leave_an_automatic_monitor_running(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        settings=QtCore.QSettings(str(Path(temp.name)/'settings.ini'),QtCore.QSettings.IniFormat)
        settings.setValue('learning/results_folder',temp.name)
        controller=HistoryRefreshController(settings,Path(temp.name)/'history.sqlite')
        self.addCleanup(lambda: controller.watch.shutdown())
        self.assertTrue(controller.enabled())
        controller.watch.arm(); controller.watch.schedule()
        self.assertFalse(controller.watch.pending)
        self.assertFalse(controller.watch.watcher.files()+controller.watch.watcher.directories())
        self.assertFalse(Path(controller.db_path).exists())
