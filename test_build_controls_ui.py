import os
import tempfile
import unittest
from unittest import mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtCore, QtWidgets
from main_window import MainWindow
from compute_settings import DEEP_PROFILES


class BuildControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_profile_picker_preserves_search_rules_and_custom_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=QtCore.QSettings(os.path.join(directory,'settings.ini'),QtCore.QSettings.IniFormat)
            with mock.patch('main_window.QtCore.QSettings',return_value=settings):
                w=MainWindow()
                w.chk_auto_entry_target.setChecked(False)
                w.deep_compute_settings.update(all_styles=True,selection_mode='Individual ranking')
                name=next(k for k in DEEP_PROFILES if k.startswith('Thorough'))
                w.combo_compute_profile.activated[str].emit(name)
                self.assertEqual(w.deep_compute_settings['minutes'],20)
                self.assertEqual(w.spin_nfl_sim_scenarios.value(),10000)
                self.assertTrue(w.chk_nfl_contest_sim.isChecked())
                self.assertFalse(w.combo_build_style.isEnabled())
                self.assertTrue(w.spin_nfl_sim_scenarios.isHidden())
                self.assertEqual(w.combo_compute_profile.currentText(),name)
                self.assertEqual(w.deep_compute_settings['selection_mode'],'Individual ranking')
                errors=[]
                def inspect():
                    try:
                        d=self.app.activeModalWidget()
                        details=d.findChild(QtWidgets.QGroupBox,'deepResourceDetails')
                        self.assertTrue(details.isHidden())
                        d.findChild(QtWidgets.QComboBox,'deepProfile').setCurrentText('Custom')
                        self.assertFalse(details.isHidden())
                        self.assertTrue(d.findChild(QtWidgets.QSpinBox,'deep_minutes').isEnabled())
                        d.reject()
                    except Exception as exc:
                        errors.append(exc);self.app.activeModalWidget().reject()
                QtCore.QTimer.singleShot(0,inspect);w._edit_deep_compute_settings()
                self.assertFalse(errors,errors)
                self.assertEqual(w.deep_compute_settings['minutes'],20)
                def edit_custom():
                    d=self.app.activeModalWidget()
                    d.findChild(QtWidgets.QSpinBox,'deep_minutes').setValue(25)
                    d.accept()
                QtCore.QTimer.singleShot(0,edit_custom)
                w.combo_compute_profile.activated[str].emit('Custom')
                self.assertEqual(w.deep_compute_settings['minutes'],25)
                self.assertEqual(w.combo_compute_profile.currentText(),'Custom')
                w.combo_compute_profile.activated[str].emit('Fast')
                self.assertTrue(w.combo_build_style.isEnabled())
                self.assertTrue(w.combo_nfl_compute_mode.currentText().startswith('Fast'))
                w.combo_sport.setCurrentText('MLB')
                self.assertTrue(w.combo_compute_profile.isHidden())
                w.close()
