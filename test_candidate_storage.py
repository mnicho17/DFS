from test_environment import install
install()
import unittest
from PyQt5 import QtCore, QtWidgets
from candidate_storage import KEY, budget_bytes, open_storage
from unittest.mock import patch

class CandidateStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.settings=QtCore.QSettings('DFS Optimizer','DFS Optimizer')
        mock=patch('candidate_storage.QtCore.QSettings',return_value=self.settings)
        mock.start();self.addCleanup(mock.stop)
        self.settings.remove(KEY)
        self.addCleanup(self.settings.remove,KEY)

    def test_default_and_invalid_values(self):
        self.assertEqual(budget_bytes(),64*1024**3)
        for value in ('bad',0,-1,4097):
            self.settings.setValue(KEY,value)
            self.assertEqual(budget_bytes(),64*1024**3)

    def test_saved_allocation_is_used(self):
        self.settings.setValue(KEY,256)
        self.assertEqual(budget_bytes(),256*1024**3)

    def test_dialog_save_and_cancel(self):
        def save(dialog):
            dialog.findChild(QtWidgets.QSpinBox).setValue(128)
            return QtWidgets.QDialog.Accepted
        with patch.object(QtWidgets.QDialog,'exec_',save):open_storage(None)
        self.assertEqual(budget_bytes(),128*1024**3)
        with patch.object(QtWidgets.QDialog,'exec_',return_value=QtWidgets.QDialog.Rejected):open_storage(None)
        self.assertEqual(budget_bytes(),128*1024**3)
