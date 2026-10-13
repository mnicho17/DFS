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

    def test_build_summary_and_history_row_fit_larger_fonts(self):
        from PyQt5 import QtGui
        w=MainWindow();self.addCleanup(w.close)
        w.combo_sport.setCurrentText('NFL');w.tabs_lineups.setCurrentIndex(0)
        w._set_build_controls_visible(True)
        w.chk_showdown_history.setChecked(True)
        for points in (10,14,18):
            font=QtGui.QFont(w.font());font.setPointSize(points);w.setFont(font)
            w.resize(1280,900);w.show();self.app.processEvents()
            label=w.lbl_compute_summary
            self.assertGreaterEqual(label.height(),label.heightForWidth(label.width()))
            self.assertLess(label.geometry().bottom(),w.chk_showdown_history.geometry().top())
            panel=w.chk_showdown_history.parentWidget()
            self.assertTrue(panel.rect().contains(w.btn_showdown_history.geometry()))
            self.assertTrue(w.btn_showdown_history.isVisible())
        self.assertGreater(w.tabs_workspace_controls.height(),230)

    def test_history_summary_updates_and_distinguishes_fast_from_deep(self):
        w=MainWindow();self.addCleanup(w.close)
        w.combo_sport.setCurrentText('NFL');w.tabs_lineups.setCurrentIndex(0)
        w.chk_showdown_history.setChecked(True)
        self.assertIn('choose a Deep profile',w.lbl_compute_summary.text())
        self.assertNotIn('remains generic',w.lbl_compute_summary.text())
        w.combo_nfl_compute_mode.setCurrentText('Deep (custom budget)');w.chk_nfl_contest_sim.setChecked(True)
        self.assertIn('application or fallback',w.lbl_compute_summary.text())
        w.chk_showdown_history.setChecked(False)
        self.assertIn('remains generic',w.lbl_compute_summary.text())

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
