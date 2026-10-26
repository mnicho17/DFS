"""Explicit export consent may waive portfolio preferences, never roster legality."""
from test_environment import install
install()
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from PyQt5 import QtWidgets
from entry_safety import portfolio_override_allowed
from main_window import EntrySafetyDialog,MainWindow
import test_entry_safety as fixtures

class ExportOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def report(self):
        return fixtures.EntrySafetyTests()._report([fixtures._nfl_lineup()],portfolio_report={'warnings':['Captain exposure exceeds maximum.']})

    def test_portfolio_report_stays_blocked_and_unchanged(self):
        report=self.report();before=copy.deepcopy(report)
        self.assertEqual(report['status'],'blocked')
        self.assertTrue(portfolio_override_allowed(report))
        self.assertEqual(report,before)

    def test_checkbox_is_required_for_each_dialog(self):
        for _ in range(2):
            dialog=EntrySafetyDialog(self.report())
            button=dialog.findChild(QtWidgets.QPushButton,'confirmSafeExport')
            checkbox=dialog.findChild(QtWidgets.QCheckBox,'acknowledgePortfolioExport')
            self.assertFalse(button.isEnabled());self.assertFalse(checkbox.isChecked())
            checkbox.setChecked(True)
            self.assertTrue(button.isEnabled());self.assertTrue(dialog.portfolio_override_confirmed)
            checkbox.setChecked(False);self.assertFalse(button.isEnabled())
            checkbox.setChecked(True);button.click()
            self.assertEqual(dialog.result(),QtWidgets.QDialog.Accepted)
            dialog.close()

    def test_any_additional_or_unknown_blocker_cannot_be_overridden(self):
        for key in ('roster','slot_ids','salary_cap','unavailable','unknown_future_check'):
            report=self.report();report['checks'].append({'key':key,'status':'block'})
            report['blockers']+=1
            self.assertFalse(portfolio_override_allowed(report))
            dialog=EntrySafetyDialog(report)
            self.assertIsNone(dialog.findChild(QtWidgets.QCheckBox,'acknowledgePortfolioExport'))
            self.assertFalse(dialog.findChild(QtWidgets.QPushButton,'confirmSafeExport').isEnabled())
            dialog.close()
        report=self.report();report['blockers']+=1
        self.assertFalse(portfolio_override_allowed(report))

    def gate(self,report,confirmed=True,result=QtWidgets.QDialog.Accepted,stale=False):
        token=iter(['before','after'] if stale else ['same','same'])
        parent=SimpleNamespace(_entry_safety_report=lambda *a:report,
            _saved_source=lambda *a:next(token),_repair_saved_lineups=Mock())
        dialog=SimpleNamespace(exec_=lambda:result,repair_requested=False,portfolio_override_confirmed=confirmed)
        with patch('main_window.EntrySafetyDialog',return_value=dialog):
            return MainWindow._confirm_entry_safety_export(parent,'showdown',[],[],50000)

    def test_export_gate_requires_consent_and_acceptance(self):
        self.assertTrue(self.gate(self.report()))
        self.assertFalse(self.gate(self.report(),confirmed=False))
        self.assertFalse(self.gate(self.report(),result=QtWidgets.QDialog.Rejected))
        self.assertFalse(self.gate(self.report(),stale=True))

    def test_programmatic_accept_cannot_bypass_hard_blocks(self):
        report=self.report()
        report['checks'].append({'key':'salary_cap','status':'block'})
        report['blockers']+=1
        self.assertFalse(self.gate(report))

    def test_review_and_ready_exports_keep_existing_path(self):
        report=fixtures.EntrySafetyTests()._report([fixtures._nfl_lineup()])
        self.assertTrue(self.gate(report,confirmed=False))
        report['status']='review'
        self.assertTrue(self.gate(report,confirmed=False))

if __name__=='__main__':unittest.main()
