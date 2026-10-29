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
    def test_captain_dst_is_role_specific_optional_and_symmetric(self):
        pool=players();groups=normalize_rules({'groups':common.apply(pool,[],{'captain_dst'})})['groups']
        for captain,opposing,same in [('AWR','BDST','ADST'),('BTE','ADST','BDST'),('AK','BDST','ADST'),('ADST','BDST','ADST')]:
            self.assertFalse(_group_ok({captain,opposing},groups,captain))
            self.assertTrue(_group_ok({captain,same},groups,captain))
        self.assertTrue(_group_ok({'AWR','BDST','BTE'},groups,'BTE'))
        self.assertEqual(common.selection(pool,groups),{'captain_dst'})
        self.assertEqual(common.apply(pool,groups,set()),[])
        dialog=CommonGroupsDialog(pool,[],'unused.sqlite')
        self.assertFalse(dialog.checks['captain_dst'].isChecked())
        dialog.reject()

    def test_captain_dst_selection_saved_audit_and_snapshot_roundtrip(self):
        from test_showdown_library import players as slate
        from portfolio_rules import select_portfolio,portfolio_report
        from build_snapshots import create_snapshot,validate_snapshot
        pool=slate();pool[4]['Position']='DST';pool[0]['CptProjection']=1000
        rules={'groups':common.apply(pool,[],{'captain_dst'})}
        bad={'Captain':pool[0],'Flex':[pool[i] for i in (1,2,4,5,6)]}
        good={'Captain':pool[5],'Flex':[pool[i] for i in (0,1,2,4,6)]}
        selected=select_portfolio([bad,good],1,rules=rules,kind='showdown')
        self.assertEqual(selected['lineups'],[good])
        warnings=portfolio_report([bad],rules,kind='showdown')['warnings']
        self.assertTrue(any('player-group' in warning for warning in warnings))
        self.assertFalse(any('player-group' in warning for warning in portfolio_report([good],rules,kind='showdown')['warnings']))
        snapshot=create_snapshot(pool,{'contest_kind':'showdown','sport':'NFL'},rules)
        self.assertEqual(validate_snapshot(snapshot)['inputs']['rules']['groups'],rules['groups'])

    def test_candidate_library_filters_role_rule_without_rewriting_saved_candidates(self):
        import json,tempfile
        from pathlib import Path
        from test_showdown_library import players as slate
        from candidate_library import initialize,connect,load_candidates,roster_keys
        from build_snapshots import create_snapshot
        pool=slate();pool[4]['Position']='DST'
        bad={'Captain':pool[0],'Flex':[pool[i] for i in (1,2,4,5,6)]}
        good={'Captain':pool[5],'Flex':[pool[i] for i in (0,1,2,4,6)]}
        rules={'groups':common.apply(pool,[],{'captain_dst'})}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'candidates.dfslib'
            initialize(path,create_snapshot(pool,{'contest_kind':'showdown','sport':'NFL'},{}))
            with connect(path) as con:
                for index,row in enumerate((bad,good)):
                    encoded=json.dumps(roster_keys(row,'showdown'))
                    con.execute('INSERT INTO candidates VALUES (?,?,?,?,?)',(encoded,encoded,0,'Balanced',index))
            before=path.read_bytes()
            all_rows,_=load_candidates(path,pool,kind='showdown',salary_cap=50000)
            rows,report=load_candidates(path,pool,kind='showdown',salary_cap=50000,rules=rules)
            self.assertEqual(len(all_rows),2)
            self.assertEqual(len(rows),1)
            self.assertEqual(player_key(rows[0]['Captain']),player_key(pool[5]))
            self.assertEqual(report['rejected'],1)
            self.assertEqual(path.read_bytes(),before)

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
        self.assertEqual(len(dialog.review.menu().actions()),3);self.assertIsNone(dialog._thread)
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

    def test_readiness_worker_uses_archive_inventory_without_running_outcomes(self):
        worker=ReviewWorker('sample/exports.sqlite','Winner','20-Max',readiness=True);received=[]
        worker.result.connect(received.append)
        with patch('grouping_replay_readiness.inspect_archives',return_value={'inventory':True}) as inspect, patch('construction_outcomes.review_outcomes') as outcomes:
            worker.run()
        self.assertEqual(received,[{'inventory':True}]);outcomes.assert_not_called()
        self.assertEqual(str(inspect.call_args.args[0]),'sample')
        self.assertEqual(inspect.call_args.kwargs,{'preset':'20-Max'})
    def test_readiness_menu_uses_matching_renderer_and_keeps_rules_unselected(self):
        dialog=CommonGroupsDialog(players(),[],'unused.sqlite')
        with patch('grouping_replay_readiness.inspect_archives',return_value={'inventory':True}), patch('grouping_replay_readiness.render',return_value='Frozen input inventory') as renderer:
            dialog.start(readiness=True);deadline=time.monotonic()+3
            while dialog._thread is not None and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.005)
            self.assertIsNone(dialog._thread);self.assertEqual(dialog.selected(),set())
            self.assertEqual(dialog.report.toPlainText(),'Frozen input inventory')
            self.assertIn('No generation trial',dialog.status.text())
            renderer.assert_called_once_with({'inventory':True})
        dialog.close()

if __name__=='__main__':unittest.main()
