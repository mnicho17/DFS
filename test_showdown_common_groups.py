from test_environment import install
install()
import copy,unittest,time
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch
from PyQt5 import QtWidgets
import showdown_common_groups as common
from showdown_common_groups_ui import CommonGroupsDialog,ReviewWorker
from portfolio_rules import _group_ok,normalize_rules,player_key


def players():
    result=[]
    for team in ('A','B'):
        for pos in ('QB','K','DST','WR','RB','TE'):
            key=team+pos
            result.append(dict(Name=key,Team=team,Opponent='B' if team=='A' else 'A',Position=pos,FlexID=key,FlexNamePlusID=key,GameInfo='A@B'))
    return result

class CommonGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def test_all_four_are_optional_and_cover_both_slots(self):
        pool=players();before=copy.deepcopy(pool)
        self.assertEqual(common.apply(pool,[],set()),[])
        for rule,keys in [('kickers',{'AK','BK'}),('defenses',{'ADST','BDST'}),('quarterbacks',{'AQB','BQB'}),('qb_dst',{'AQB','BDST'})]:
            groups=normalize_rules({'groups':common.apply(pool,[],{rule})})['groups']
            self.assertFalse(_group_ok(keys,groups))
            self.assertTrue(_group_ok({next(iter(keys)),'AWR','BWR'},groups))
        groups=common.apply(pool,[],{'qb_dst'})
        self.assertTrue(_group_ok({'AQB','ADST','BWR'},groups))
        self.assertFalse(_group_ok({'BQB','ADST','AWR'},groups))
        self.assertEqual(pool,before)
    def test_reapply_roundtrip_and_custom_groups_preserved(self):
        pool=players();custom={'type':'at_least_one','player_keys':['AWR','BWR'],'label':'My group'}
        rules=common.apply(pool,[custom],set(common.RULES))
        self.assertEqual(common.apply(pool,rules,set(common.RULES)),rules)
        restored=normalize_rules({'groups':rules})['groups']
        self.assertEqual(common.selection(pool,restored),set(common.RULES))
        self.assertEqual(common.apply(pool,restored,set()),[custom])
        manual={'type':'never_together','player_keys':['AK','BK'],'label':'My manual kickers'}
        self.assertEqual(common.apply(pool,[manual],{'kickers'}),[manual])
    def test_invalid_and_conflicting_identities_fail_closed(self):
        pool=players();pool[0].pop('FlexID')
        with self.assertRaises(ValueError):common.catalog(pool)
        pool=players();pool.append(dict(pool[0],Team='B'))
        with self.assertRaises(ValueError):common.catalog(pool)
        pool=players();pool[0]['Opponent']='C'
        with self.assertRaises(ValueError):common.catalog(pool)
    def test_main_window_apply_and_stale_slate_guard(self):
        from main_window import MainWindow
        custom={'type':'at_least_one','player_keys':['AWR'],'label':'My rule'}
        owner=SimpleNamespace(players=players(),portfolio_groups=[custom],_current_sport=lambda:'NFL',_contest_mode=lambda:'showdown',status=SimpleNamespace(showMessage=Mock()),lbl_portfolio_groups=QtWidgets.QLabel())
        dialog=SimpleNamespace(exec_=lambda:QtWidgets.QDialog.Accepted,selected=lambda:{'kickers'})
        with patch('showdown_common_groups_ui.CommonGroupsDialog',return_value=dialog):
            MainWindow.open_common_showdown_groups(owner)
        self.assertEqual(common.selection(owner.players,owner.portfolio_groups),{'kickers'})
        self.assertIn(custom,owner.portfolio_groups)
        before=copy.deepcopy(owner.portfolio_groups)
        def changed():
            owner.players[0]['FlexID']='changed';owner.players[0]['FlexNamePlusID']='changed'
            return QtWidgets.QDialog.Accepted
        dialog.exec_=changed
        with patch('showdown_common_groups_ui.CommonGroupsDialog',return_value=dialog):
            MainWindow.open_common_showdown_groups(owner)
        self.assertEqual(before,owner.portfolio_groups)
        self.assertIn('slate changed',owner.status.showMessage.call_args.args[0])
    def test_closing_a_running_review_waits_for_retirement(self):
        dialog=CommonGroupsDialog(players(),[],'unused.sqlite')
        def blocked(path,cancelled,progress,**kwargs):
            while not cancelled():time.sleep(.005)
            return {'partial':True}
        with patch('showdown_group_review.review_history',side_effect=blocked):
            dialog.start();dialog.accept()
            self.assertIsNotNone(dialog._thread)
            dialog.reject();deadline=time.monotonic()+3
            while dialog._thread is not None and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.005)
            self.assertIsNone(dialog._thread)
            self.assertEqual(dialog.report.toPlainText(),'')

    def test_dialog_cancel_does_not_mutate_and_no_automatic_review(self):
        pool=players();groups=[];before=copy.deepcopy((pool,groups))
        dialog=CommonGroupsDialog(pool,groups,'unused.sqlite')
        self.assertEqual(dialog.selected(),set());self.assertIsNone(dialog._thread)
        dialog.checks['kickers'].setChecked(True);dialog.reject()
        self.assertEqual((pool,groups),before)
    def test_cancelled_worker_publishes_no_partial_report(self):
        worker=ReviewWorker('unused','','');worker.stop.set();received=[];done=[]
        worker.result.connect(received.append);worker.done.connect(lambda:done.append(True))
        with patch('showdown_group_review.review_history',return_value={'partial':True}):worker.run()
        self.assertEqual(received,[]);self.assertEqual(done,[True])

    def test_outcome_worker_uses_readonly_outcome_review_without_username_selection(self):
        worker=ReviewWorker('unused','Winner','150-Max',outcomes=True);received=[]
        worker.result.connect(received.append)
        with patch('construction_outcomes.review_outcomes',return_value={'outcomes':True}) as review:
            worker.run()
        self.assertEqual(received,[{'outcomes':True}])
        self.assertEqual(review.call_args.kwargs,{'preset':'150-Max'})
    def test_outcome_menu_is_explicit_and_cancellation_suppresses_result(self):
        dialog=CommonGroupsDialog(players(),[],'unused.sqlite')
        self.assertEqual(len(dialog.review.menu().actions()),2);self.assertIsNone(dialog._thread)
        dialog.close()
        worker=ReviewWorker('unused','','',outcomes=True);worker.stop.set();received=[]
        worker.result.connect(received.append)
        with patch('construction_outcomes.review_outcomes',return_value={'partial':True}):worker.run()
        self.assertEqual(received,[])

    def test_completed_outcome_review_uses_matching_renderer_without_enabling_rules(self):
        dialog=CommonGroupsDialog(players(),[],'unused.sqlite')
        with patch('construction_outcomes.review_outcomes',return_value={'outcomes':True}), patch('construction_outcomes.render',return_value='Matched outcome report') as renderer:
            dialog.start(outcomes=True);deadline=time.monotonic()+3
            while dialog._thread is not None and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.005)
            self.assertIsNone(dialog._thread)
            self.assertEqual(dialog.report.toPlainText(),'Matched outcome report')
            renderer.assert_called_once_with({'outcomes':True})
            self.assertEqual(dialog.selected(),set())
        dialog.close()

if __name__=='__main__':unittest.main()
