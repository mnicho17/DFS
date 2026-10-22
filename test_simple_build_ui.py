from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtGui, QtWidgets
from main_window import MainWindow
from compute_settings import DEEP_PROFILES
from simple_build_ui import sync_simple_build_panel


class SimpleBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w = MainWindow()
        self.addCleanup(w.close)
        return w

    def choose(self, w, name):
        index = w.combo_simple_time.findData(name)
        self.assertGreaterEqual(index, 0)
        w.combo_simple_time.setCurrentIndex(index)
        w.combo_simple_time.activated[int].emit(index)

    def test_automatic_library_route_ignores_old_off_preference_and_has_no_attachment_menu(self):
        from build_preparation import automatic_library_inputs,remember_library
        w=self.window();w.app_settings.setValue('build/auto_library',False)
        remember_library(w.app_settings,'known.dfslib')
        route=automatic_library_inputs(w)
        self.assertTrue(route['library_folder']);self.assertTrue(route['automatic_libraries'])
        actions=[action.text() for menu in w.findChildren(QtWidgets.QMenu) for action in menu.actions()]
        self.assertNotIn('Load Candidate Library...',actions)
        self.assertNotIn('Clear Candidate Library',actions)
        self.assertNotIn('Automatically reuse compatible libraries',actions)
        w._snapshot_replay=True
        self.assertEqual(automatic_library_inputs(w),dict(automatic_libraries=(),library_folder=''))

    def test_default_panel_uses_original_count_and_build_controls(self):
        w = self.window()
        self.assertIs(w.simple_count_stack.currentWidget(), w.spin_sd)
        self.assertTrue(w.simple_build_panel.isAncestorOf(w.spin_sd))
        self.assertTrue(w.simple_build_panel.isAncestorOf(w.btn_primary_build))
        self.assertTrue(w.tabs_workspace_controls.isHidden())
        self.assertFalse(w.btn_build_advanced.isChecked())
        self.assertFalse(w.simple_build_panel.isHidden())
        self.assertIs(w.advanced_cap_stack.currentWidget(), w.edit_sd_cap)
        self.assertIn('Auto entry target:', w.lbl_simple_build_summary.text())
        with patch.object(w, 'on_build_showdown') as build:
            w.btn_primary_build.click()
        build.assert_called_once()

    def test_format_switch_keeps_independent_counts_caps_and_correct_target(self):
        w = self.window()
        w.spin_sd.setValue(20)
        w.edit_sd_cap.setText('49000')
        w.spin_cl.setValue(150)
        w.edit_cl_cap.setText('48000')
        for index, spin, cap, target in ((1,w.spin_cl,w.edit_cl_cap,'150-Max'),(0,w.spin_sd,w.edit_sd_cap,'20-Max')):
            w.tabs_lineups.setCurrentIndex(index)
            self.assertIs(w.simple_count_stack.currentWidget(), spin)
            self.assertIs(w.advanced_cap_stack.currentWidget(), cap)
            self.assertIn(target, w.lbl_simple_build_summary.text())
            self.assertIn(cap.text(), w.lbl_simple_build_summary.text())
        self.assertEqual((w.spin_sd.value(),w.spin_cl.value()), (20,150))
        w.tabs_lineups.setCurrentIndex(1)
        with patch.object(w, 'on_build_classic') as build:
            w.btn_primary_build.click()
        build.assert_called_once()

    def test_advanced_button_and_menu_share_state_without_recipe_mutation(self):
        w = self.window()
        w.chk_auto_entry_target.setChecked(False)
        w.combo_field_preset.setCurrentText('3-Max')
        w.spin_zero_qb_max.setValue(12.5)
        before = copy.deepcopy(w._current_build_recipe())
        w.btn_build_advanced.click()
        self.assertTrue(w.action_show_build_controls.isChecked())
        self.assertFalse(w.tabs_workspace_controls.isHidden())
        w.action_show_build_controls.setChecked(False)
        self.assertFalse(w.btn_build_advanced.isChecked())
        self.assertTrue(w.tabs_workspace_controls.isHidden())
        w._set_build_controls_visible(True)
        self.assertTrue(w.btn_build_advanced.isChecked())
        self.assertEqual(w._current_build_recipe(), before)

    def test_time_budget_reuses_existing_profile_behavior_exactly(self):
        simple, advanced = self.window(), self.window()
        for w in (simple, advanced):
            w.chk_auto_entry_target.setChecked(False)
            w.deep_compute_settings.update(all_styles=True, selection_mode='Individual ranking')
        for name in list(DEEP_PROFILES) + ['Fast']:
            self.choose(simple, name)
            advanced.combo_compute_profile.setCurrentText(name)
            advanced.combo_compute_profile.activated[str].emit(name)
            self.assertEqual(simple._current_build_recipe(), advanced._current_build_recipe())
            self.assertEqual(simple.combo_simple_time.currentData(), name)

    def test_custom_route_only_reveals_existing_settings(self):
        w = self.window()
        w.chk_nfl_contest_sim.setChecked(False)
        w.deep_compute_settings.update(minutes=17, candidates=1700)
        before = copy.deepcopy(w._current_build_recipe())
        self.choose(w, 'Custom')
        self.assertTrue(w.action_show_build_controls.isChecked())
        self.assertEqual(w.tabs_workspace_controls.currentIndex(), 0)
        self.assertEqual(w._current_build_recipe(), before)
        self.assertFalse(w.chk_nfl_contest_sim.isChecked())

    def test_recipe_custom_budget_manual_target_and_history_remain_visible(self):
        w = self.window()
        recipe = w._current_build_recipe()
        recipe.update(auto_entry_target=False, nfl_field_preset='3-Max',
                      nfl_compute_mode='Deep (custom budget)', nfl_sim_enabled=True,
                      showdown_history_field=True)
        recipe['deep_compute'].update(minutes=17,candidates=1700)
        w._apply_build_recipe('My custom setup', recipe)
        self.assertEqual(w.combo_simple_time.currentData(), 'Custom')
        self.assertIn('17 min', w.combo_simple_time.currentText())
        text = w.lbl_simple_build_summary.text()
        for expected in ('Manual entry target: 3-Max','Custom compute settings','Historical field requested','My custom setup'):
            self.assertIn(expected,text)
        self.assertTrue(w.tabs_workspace_controls.isHidden())
        self.assertFalse(w.chk_auto_entry_target.isChecked())

    def test_other_sports_hide_nfl_time_without_changing_stored_compute(self):
        w = self.window()
        name = next(iter(DEEP_PROFILES))
        self.choose(w,name)
        options = copy.deepcopy(w.deep_compute_settings)
        w.combo_sport.setCurrentText('MLB')
        self.assertTrue(w.combo_simple_time.isHidden())
        self.assertEqual(w.deep_compute_settings,options)
        w.tabs_lineups.setCurrentIndex(2)
        self.assertTrue(w.simple_count_stack.isHidden())
        self.assertEqual(w.btn_primary_build.text(),'Refresh Stacks')
        w.combo_sport.setCurrentText('NFL')
        w.tabs_lineups.setCurrentIndex(0)
        self.assertFalse(w.combo_simple_time.isHidden())
        self.assertEqual(w.combo_simple_time.currentData(),name)
        self.assertEqual(w.deep_compute_settings,options)

    def test_simple_panel_wraps_and_advanced_scrolls_at_larger_fonts(self):
        w = self.window()
        for points in (10,14,18):
            font = QtGui.QFont(w.font()); font.setPointSize(points); w.setFont(font)
            w.resize(1280,900); w.show(); self.app.processEvents()
            label = w.lbl_simple_build_summary
            self.assertGreaterEqual(label.height(),label.heightForWidth(label.width()))
            self.assertTrue(w.simple_build_panel.rect().contains(w.btn_primary_build.geometry()))
            self.assertTrue(w.tabs_workspace_controls.isHidden())
        w.btn_build_advanced.click(); self.app.processEvents()
        scroll = w.findChild(QtWidgets.QScrollArea,'advancedBuildScroll')
        self.assertTrue(scroll.widgetResizable())
        label = w.lbl_compute_summary
        self.assertGreaterEqual(label.height(),label.heightForWidth(label.width()))
    def test_plan_describes_automatic_selected_and_replay_routes_without_mutation(self):
        from build_workflow import preparation_plan
        from test_showdown_library import players
        w=self.window(); w.players=copy.deepcopy(players())
        w.combo_compute_profile.activated[str].emit('Baseline — 5 min cap')
        before=copy.deepcopy(w._current_build_recipe())
        with patch('build_preparation.discover_libraries', side_effect=AssertionError('No scan')):
            self.assertIn('find a compatible library', preparation_plan(w))
            w._candidate_library='explicit.sdlib'
            self.assertIn('validate selected library', preparation_plan(w))
            w._candidate_library=''; w._snapshot_replay=True
            self.assertIn('search fresh candidates', preparation_plan(w))
        self.assertEqual(w._current_build_recipe(),before)
