"""Actionable notifications preserve task outcomes, manual controls and queue safety."""
from test_environment import install
install()
import unittest
from unittest.mock import Mock, patch
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtTest import QTest
from task_dashboard import TaskHub, task_panel
from task_attention import needs_attention, guidance
import test_task_dashboard as fixtures


class TaskAttentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def hub(self):
        owner=QtWidgets.QWidget();self.addCleanup(owner.deleteLater)
        owner.tasks=TaskHub(owner)
        return owner,owner.tasks

    def completed(self,hub,result,title='Results and salary history refresh'):
        worker=fixtures.FakeWorker();thread=QtCore.QThread()
        binding=hub.watch(title,worker,thread,worker.stop.set)
        worker.finished.emit(result);thread.finished.emit()
        return binding.key,worker

    def test_review_clears_only_notification_and_never_changes_failure_or_cancels_work(self):
        owner,hub=self.hub();key=hub.start('Screening',Mock())
        hub.finish(key,'Failed','Source changed; checkpoint withheld')
        before=dict(hub.rows[key]);self.assertTrue(needs_attention(before))
        self.assertTrue(hub.mark_reviewed(key));row=hub.rows[key]
        self.assertFalse(needs_attention(row));self.assertEqual(row['state'],'Failed')
        for field in ('detail','ended','started','cancel'):self.assertEqual(row[field],before[field])
        self.assertIn('Source changed; checkpoint withheld',row['log'])
        self.assertFalse(hub.mark_reviewed(key));self.assertFalse(hub.mark_reviewed(-1))

    def test_running_and_queued_tasks_cannot_be_acknowledged(self):
        owner,hub=self.hub();cancel=Mock()
        for state in ('Running','Queued','Stopping safely'):
            key=hub.start('Active work',cancel,state=state)
            self.assertFalse(hub.mark_reviewed(key));self.assertIsNone(hub.rows[key]['ended'])
        cancel.assert_not_called()

    def test_success_with_same_title_does_not_hide_different_slate_failure_and_new_failure_alerts(self):
        owner,hub=self.hub();first=hub.start('Screening');hub.finish(first,'Failed','Slate A')
        success=hub.start('Screening');hub.finish(success,'Completed','Slate B')
        self.assertTrue(needs_attention(hub.rows[first]));hub.mark_reviewed(first)
        later=hub.start('Screening');hub.finish(later,'Failed','Slate A retry')
        self.assertTrue(needs_attention(hub.rows[later]))
        self.assertEqual(hub.rows[first]['detail'],'Slate A')

    def test_history_reasons_and_next_actions_include_pairing_and_actual_error(self):
        owner,hub=self.hub()
        key,worker=self.completed(hub,dict(unpaired=3,errors=['Capture disk unavailable'],changed_sources=['revised.csv']))
        row=hub.rows[key];info=guidance(row)
        self.assertEqual(row['state'],'Needs review');self.assertTrue(needs_attention(row))
        self.assertIn('Capture disk unavailable',info['reason'])
        self.assertIn('salary',info['reason']);self.assertIn('Review Salary Matches',info['next_step'])
        self.assertIn('Needs review association',info['next_step'])
        self.assertEqual(info['target'],'history');self.assertFalse(worker.stop.is_set())

    def test_partial_incomplete_and_unknown_failure_have_safe_guidance(self):
        owner,hub=self.hub()
        for result,title,target in ((dict(complete=False,pause_reason='Time allowance reached'),'Showdown library preparation','preparation'),
                                    (dict(requested=150,lineups=[]),'Build preparation / showdown lineups','readiness'),
                                    (dict(error='Backup drive unavailable'),'Incremental history backup','backup'),
                                    (dict(error='Unknown operation failed'),'Other operation',None)):
            key,_=self.completed(hub,result,title);info=guidance(hub.rows[key])
            self.assertEqual(info['target'],target);self.assertTrue(info['next_step'])
            self.assertTrue(needs_attention(hub.rows[key]))
        key,_=self.completed(hub,dict(no_work=True,next_check='Rechecking in 5 minutes.'))
        self.assertFalse(needs_attention(hub.rows[key]));self.assertEqual(guidance(hub.rows[key])['next_step'],'')

    def test_panel_selects_attention_and_opens_tool_only_on_click_then_marks_reviewed(self):
        owner,hub=self.hub();owner.on_results_learning=Mock()
        key,_=self.completed(hub,dict(unpaired=2));active=hub.start('Current worker')
        panel=task_panel(hub,owner,compact=True);panel.focus_attention()
        table=panel.findChild(QtWidgets.QTableWidget,'taskTable')
        self.assertEqual(table.item(table.currentRow(),0).data(QtCore.Qt.UserRole),key)
        next_step=panel.findChild(QtWidgets.QLabel,'taskNextAction')
        self.assertIn('2 result files',next_step.text());self.assertIn('Next:',next_step.text())
        owner.on_results_learning.assert_not_called()
        panel.findChild(QtWidgets.QPushButton,'taskOpenReview').click()
        owner.on_results_learning.assert_called_once_with()
        mark=panel.findChild(QtWidgets.QPushButton,'taskMarkReviewed');self.assertTrue(mark.isEnabled());mark.click()
        QTest.qWait(150)
        self.assertFalse(mark.isEnabled());self.assertIn('Notification reviewed',next_step.text())
        self.assertEqual(hub.rows[key]['state'],'Needs review');self.assertIsNone(hub.rows[active]['ended'])
        self.assertIn('reviewed',table.item(table.currentRow(),0).text())

    def test_status_count_tooltip_and_dashboard_button_explain_flagged_task(self):
        from main_window import MainWindow
        window=MainWindow();self.addCleanup(window.close)
        window.background_queue.timer.stop();window.idle_backup.timer.stop()
        active=window.tasks.start('Current screening')
        failed=window.tasks.start('Incremental history backup')
        window.tasks.finish(failed,'Failed','Destination drive unavailable')
        label=window.findChild(QtWidgets.QLabel,'taskSummary')
        self.assertIn('1 underway',label.text());self.assertIn('1 need attention',label.text())
        self.assertIn('Incremental history backup',label.toolTip());self.assertIn('Next:',label.toolTip())
        window.show();window.findChild(QtWidgets.QPushButton,'taskDashboardButton').click()
        table=window.task_dashboard.findChild(QtWidgets.QTableWidget,'taskTable')
        self.assertEqual(table.item(table.currentRow(),0).data(QtCore.Qt.UserRole),failed)
        window.tasks.mark_reviewed(failed)
        self.assertIn('0 need attention',label.text());self.assertIn('1 underway',label.text())
        self.assertEqual(window.tasks.rows[failed]['state'],'Failed')
        self.assertIsNone(window.tasks.rows[active]['ended'])

    def test_generic_error_list_keeps_full_messages_below_compact_guidance(self):
        owner,hub=self.hub();message='Source validation failed: '+'evidence '*400
        key,_=self.completed(hub,dict(errors=[message]))
        self.assertIn(message,'\n'.join(hub.rows[key]['log']))
        self.assertLess(len(guidance(hub.rows[key])['reason']),280)
        self.assertTrue(needs_attention(hub.rows[key]))

    def test_guidance_is_bounded_and_original_error_is_preserved(self):
        owner,hub=self.hub();message='Disk unavailable\n'+'Long detail '*1000
        key,_=self.completed(hub,dict(error=message),'Incremental history backup')
        self.assertLess(len(guidance(hub.rows[key])['reason']),280)
        self.assertIn(message,hub.rows[key]['detail'])


if __name__=='__main__':unittest.main()
