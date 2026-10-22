from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from build_recovery_ui import guidance, open_controls
from main_window import MainWindow, BuildReceipt
from selection_shortage import TITLE, describe
from build_review_ui import report_text, review_views
from error_dialog import show_build_error


class RecoveryGuidanceTests(unittest.TestCase):
    def context(self, **options):
        return dict(sport='NFL', settings=dict(compute_mode='Deep', deep_compute=dict(minutes=5,candidates=4500,shortlist=900,**options)))

    def test_deep_guidance_uses_captured_settings_without_mutation(self):
        c=self.context();before=copy.deepcopy(c);text=guidance(c)
        self.assertIn('4,500 -> 9,000',text);self.assertIn('900 -> 1,800',text)
        self.assertIn('Recorded Deep search cap: 5 minutes',text)
        self.assertIn('not applied changes',text);self.assertEqual(c,before)

    def test_maximums_are_not_widened_and_automatic_is_explicit(self):
        c=self.context();c['settings']['deep_compute'].update(candidates=20000,shortlist=2000)
        self.assertIn('Both breadth controls are at their supported maximum',guidance(c))
        c['settings']['deep_compute'].update(candidates=0,shortlist=0)
        text=guidance(c);self.assertIn('explicit ceiling 20,000',text);self.assertIn('explicit ceiling 2,000',text)

    def test_missing_budgets_never_invent_defaults(self):
        self.assertIn('not recorded',guidance({}))
        c=self.context();c['settings'].pop('deep_compute')
        self.assertIn('budgets were not recorded',guidance(c))
        c['settings']['deep_compute']={'candidates':'4500','shortlist':True}
        text=guidance(c);self.assertNotIn('4,500',text);self.assertNotIn('->',text)

    def test_fast_other_sport_and_repair_routes_are_distinct(self):
        c=self.context();c['settings']['compute_mode']='Fast'
        self.assertIn('used Fast search',guidance(c))
        c['sport']='MLB';self.assertIn('NFL Deep budgets do not apply',guidance(c))
        c['repair_source']='saved';self.assertIn('saved portfolio',guidance(c))

    def test_flex_cap_and_minimum_deficits_are_separate_and_deterministic(self):
        from collections import Counter
        lu={};m={id(lu):dict(keys={'a','b'},captain_key='b',teams={'A'},games={'g'},specialist_captain=False)}
        limits=dict(total={},captain={},team=None,game=None,specialist=None,flex={'a':1},minimum_total={'a':3},minimum_captain={'b':2})
        counts=dict(total=Counter(a=1),captain=Counter(),flex=Counter(a=1),team=Counter(),game=Counter(),specialist=0)
        text=describe(4,[],[lu],m,{},limits,counts,{'a':{'Name':'Alpha'},'b':{'Name':'Beta'}},2,set(),set(),lambda keys:True)
        self.assertIn('Alpha: FLEX cap 1/4 (explicit)',text)
        self.assertIn('Unfilled minimum exposure targets',text)
        self.assertIn('Alpha: total minimum 1/3',text)
        self.assertIn('not proof',text)


class RecoveryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w=MainWindow();self.addCleanup(w.close);return w

    def test_actions_reveal_controls_without_changing_inputs_or_starting_work(self):
        w=self.window();before=copy.deepcopy((w._current_build_recipe(),w._portfolio_rules(),w.players))
        with patch.object(w,'_start_lineup_build') as build:
            open_controls(w,'search');self.assertEqual(w.tabs_workspace_controls.currentIndex(),0)
            open_controls(w,'limits');self.assertEqual(w.tabs_workspace_controls.currentIndex(),1)
        build.assert_not_called();self.assertTrue(w.action_show_build_controls.isChecked())
        self.assertEqual((w._current_build_recipe(),w._portfolio_rules(),w.players),before)

    def test_unknown_close_and_shutdown_do_not_open_controls(self):
        w=self.window();before=w.action_show_build_controls.isChecked()
        for token in (None,'other'):open_controls(w,token)
        w._accept_build_results=False;open_controls(w,'search')
        self.assertEqual(w.action_show_build_controls.isChecked(),before)

    def test_failed_receipt_captures_guidance_and_previous_results_survive(self):
        w=self.window();w.last_showdown=[{'previous':True}];before=copy.deepcopy(w.last_showdown)
        c=dict(sport='NFL',kind='showdown',requested_count=150,input_id='old',settings=dict(compute_mode='Deep',deep_compute=dict(minutes=5,candidates=4500,shortlist=900)))
        receipt=BuildReceipt(kind='showdown',sport='NFL',requested=150,context=c);w._build_receipt=receipt
        with patch('main_window.show_build_error',return_value='search') as show:
            w._on_lineup_build_error(TITLE+'\nStopped',receipt=receipt)
        self.assertIn('4,500 -> 9,000',show.call_args.args[2])
        failure=copy.deepcopy(w._last_build_failure)
        w.deep_compute_settings['candidates']=20000
        self.assertIn('4,500 -> 9,000',report_text(failure))
        self.assertIn('4,500 -> 9,000',review_views(failure)['limits'])
        self.assertEqual(w.last_showdown,before)
        self.assertEqual(w.tabs_workspace_controls.currentIndex(),0)

    def test_stale_duplicate_cancelled_and_shutdown_receipts_cannot_offer_actions(self):
        w=self.window();receipt=BuildReceipt(kind='showdown',sport='NFL',requested=1,context={})
        with patch('main_window.show_build_error') as show:
            w._on_lineup_build_error(TITLE+'\nStale',receipt=receipt)
            w._build_receipt=receipt
            w._on_lineup_build_error(TITLE+'\nDuplicate',receipt=receipt)
            cancelled=BuildReceipt(kind='showdown',sport='NFL',requested=1,context={});cancelled.cancelled=True;w._build_receipt=cancelled
            w._on_lineup_build_error(TITLE+'\nCancelled',receipt=cancelled)
        show.assert_not_called();self.assertFalse(getattr(w,'_last_build_failure',None))

    def test_scrollable_stop_dialog_copy_and_actions(self):
        message=TITLE+'\n'+'many details\n'*80
        for action in ('Review search...','Review limits...','Close'):
            def interact():
                d=QtWidgets.QApplication.activeModalWidget()
                details=d.findChild(QtWidgets.QPlainTextEdit,'buildStopDetails')
                self.assertTrue(details.isReadOnly());self.assertEqual(details.toPlainText(),message)
                buttons={b.text():b for b in d.findChildren(QtWidgets.QPushButton)}
                buttons['Copy Error'].click()
                self.assertEqual(QtWidgets.QApplication.clipboard().text(),message)
                self.assertTrue(d.isVisible())
                buttons[action].click()
            QtCore.QTimer.singleShot(0,interact)
            selected=show_build_error(None,'Lineup limits',message,warning=True)
            self.assertEqual(selected,{'Review search...':'search','Review limits...':'limits','Close':None}[action])

    def test_receipt_replaced_while_dialog_is_open_cannot_navigate(self):
        w=self.window();receipt=BuildReceipt(kind='showdown',sport='NFL',requested=1,context={});w._build_receipt=receipt
        def replace(*args,**kwargs):
            w._build_receipt=BuildReceipt(kind='classic',sport='NFL',requested=2,context={})
            return 'limits'
        with patch('main_window.show_build_error',side_effect=replace),patch('build_recovery_ui.open_controls') as navigate:
            w._on_lineup_build_error(TITLE+'\nStopped',receipt=receipt)
        navigate.assert_not_called()

    def test_generic_error_retains_original_copy_without_recovery_actions(self):
        message='Traceback\nOriginal details'
        def interact(d):
            self.assertIsInstance(d,QtWidgets.QMessageBox)
            buttons={b.text():b for b in d.buttons()}
            self.assertNotIn('Review search...',buttons)
            buttons['Copy Error'].click()
            self.assertEqual(QtWidgets.QApplication.clipboard().text(),message)
            return 0
        with patch.object(QtWidgets.QMessageBox,'exec_',new=interact):
            self.assertIsNone(show_build_error(None,'Optimization Error',message))
