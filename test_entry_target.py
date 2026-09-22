import os
import tempfile
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtWidgets, QtCore
from entry_target import entry_target, sync_entry_target
from main_window import MainWindow


class EntryTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_boundaries(self):
        for count,preset in ((1,'Single Entry'),(2,'3-Max'),(3,'3-Max'),(4,'20-Max'),(20,'20-Max'),(21,'150-Max'),(150,'150-Max'),(450,'150-Max')):
            self.assertEqual(entry_target(count)['preset'],preset)
            self.assertEqual(entry_target(count)['selection_mode'],'Individual ranking' if count==1 or count>150 else 'Portfolio selection')

    def test_report_keeps_target_and_actual_contest_details(self):
        from build_diagnostics import create_build_diagnostic, format_build_report
        record=create_build_diagnostic(context={'settings':{'entry_target':'20-Max','auto_entry_target':True,
            'contest_profile':{'name':'Actual contest','field_size':1234,'user_entries':20}}},timing_report={})
        self.assertEqual(record['settings']['contest_field_size'],1234)
        self.assertIn('Entry target: 20-Max; automatic from requested count',format_build_report(record))

    def test_controls_both_formats_override_and_recipe_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=QtCore.QSettings(folder+'/settings.ini',QtCore.QSettings.IniFormat)
            with patch('main_window.QtCore.QSettings',return_value=settings):w=MainWindow()
            try:
                for kind in ('classic','showdown'):
                    with patch.object(w,'_contest_mode',return_value=kind):
                        spin=w.spin_sd if kind=='showdown' else w.spin_cl
                        for count in (1,20,150,450):
                            spin.setValue(count);sync_entry_target(w)
                            target=entry_target(count)
                            self.assertEqual(w.combo_field_preset.currentText(),target['preset'])
                            self.assertEqual(w.deep_compute_settings['selection_mode'],target['selection_mode'])
                            self.assertFalse(w.combo_field_preset.isEnabled())
                            self.assertIn(target['label'],w.lbl_compute_summary.text())
                w.chk_auto_entry_target.setChecked(False)
                w.combo_field_preset.setCurrentText('3-Max');w.deep_compute_settings['selection_mode']='Individual ranking'
                w.spin_cl.setValue(150);sync_entry_target(w)
                self.assertEqual(w.combo_field_preset.currentText(),'3-Max')
                self.assertEqual(w.deep_compute_settings['selection_mode'],'Individual ranking')
                self.assertTrue(w.combo_field_preset.isEnabled())
                recipe=w._current_build_recipe();self.assertFalse(recipe['auto_entry_target'])
                w.chk_auto_entry_target.setChecked(True);w._apply_build_recipe('manual',recipe)
                self.assertFalse(w.chk_auto_entry_target.isChecked())
                self.assertEqual(w.combo_field_preset.currentText(),'3-Max')
                self.assertFalse(settings.value('build/auto_entry_target',True,type=bool))
                recipe.pop('auto_entry_target');w.chk_auto_entry_target.setChecked(True);w._apply_build_recipe('legacy',recipe)
                self.assertFalse(w.chk_auto_entry_target.isChecked())
            finally:w.close()
