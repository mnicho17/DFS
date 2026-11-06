from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
from captain_preflight import inspect,lines,summary
from captain_pool import prepare_captain_pool
from test_portfolio_recovery import bank,player


class CaptainPreflightTests(unittest.TestCase):
    def test_missing_captain_is_a_generated_target_gap_not_infeasibility(self):
        rows=bank(10,captain=10);players=[rows[0]['Captain'],player('missing',LockCpt=True)]
        players[0]['LockCpt']=True
        _,rules,_=prepare_captain_pool(players,{},10)
        c=inspect(rows,[],players,rules,10)
        self.assertEqual(c['distinct_candidates'],10)
        missing=next(r for r in c['captains'] if r['player']=='missing')
        self.assertEqual((missing['available'],missing['target'],missing['shortfall']),(0,5,5))
        self.assertEqual(missing['target_source'],'generated allocation')
        self.assertIn('1 Captain target(s) short',summary(c))
        self.assertIn('not proof of slate infeasibility','\n'.join(lines({'salary_eligible':c})))
    def test_duplicate_rows_and_retained_overlap_cannot_inflate_coverage(self):
        rows=bank(2,captain=2);players=[rows[0]['Captain']]
        c=inspect(rows+[copy.deepcopy(rows[0])],[rows[0],rows[0]],players,{},2)
        self.assertEqual(c['distinct_candidates'],2);self.assertEqual(c['retained_distinct'],1)
        rev=inspect(list(reversed(rows)),[rows[0]],players,{},2)
        self.assertEqual(c,rev)
    def test_manual_minimum_in_player_or_rule_stays_distinct_from_automatic(self):
        rows=bank(2,captain=2);p=rows[0]['Captain'];p['MinCptPct']=60
        for rules in ({},{'player_constraints':{'captain':{'MinCptPct':60}}}):
            r=inspect(rows,[],[p],rules,10)['captains'][0]
            self.assertEqual((r['target'],r['shortfall'],r['target_source']),(6,4,'manual minimum'))
    def test_stages_are_captured_without_claiming_rejection_causes(self):
        rows=bank(2,captain=2);p=rows[0]['Captain'];p['LockCpt']=True
        stages={'salary_eligible':inspect(rows,[],[p],{},2),'selection_bank':inspect(rows[:1],[],[p],{},2)}
        text='\n'.join(lines(stages))
        self.assertIn('Before screening',text);self.assertIn('Available to final selection',text)
        self.assertIn('do not establish a rejection cause',text)
        self.assertNotIn('Independent validation output',text)
    def test_capture_does_not_change_inputs_and_can_be_cancelled(self):
        rows=bank(10,captain=10);players=[rows[0]['Captain']];before=copy.deepcopy((rows,players))
        inspect(rows,[],players,{},10);self.assertEqual((rows,players),before)
        with self.assertRaisesRegex(ValueError,'cancelled'):inspect(rows,[],players,{},10,lambda:True)
    def test_build_report_freezes_stage_evidence(self):
        from build_diagnostics import create_build_diagnostic,format_build_report
        rows=bank(2,captain=2);p=rows[0]['Captain'];p['LockCpt']=True
        stages={'salary_eligible':inspect(rows,[],[p],{},2)}
        report=create_build_diagnostic(context={'kind':'showdown','sport':'NFL'},timing_report={},sim_report={'captain_candidate_coverage':stages})
        stages['salary_eligible']['distinct_candidates']=999
        text=format_build_report(report)
        self.assertIn('2 distinct signatures for 2 requested',text);self.assertNotIn('999',text)

    def test_deep_library_reports_coverage_before_sim_without_extending_library(self):
        from main_window import LineupBuildWorker,_deep_shortlist
        from showdown_simulation import run_deep_showdown
        rows=bank(10,captain=10)
        players=list({p['FlexID']:p for row in rows for p in [row['Captain']]+row['Flex']}.values())
        worker=LineupBuildWorker(players,kind='showdown',num_lineups=10,salary_cap=50000,sim_enabled=True,compute_mode='Deep',salary_strategy='Balanced Spend',deep_time_limit_seconds=1)
        worker.library_candidates=rows;events=[]
        worker.progress.connect(lambda a,b,text:events.append(text))
        def simulate(*a,**k):events.append('SIM');return {'lineups':rows,'report':{'scenarios':0}}
        with patch('showdown_simulation.ShowdownOptimizer',side_effect=AssertionError('No generation')),patch('showdown_simulation.simulate_showdown',side_effect=simulate):
            result=run_deep_showdown(worker,_deep_shortlist)
        stages=result['sim_report']['captain_candidate_coverage']
        self.assertEqual(stages['salary_eligible']['distinct_candidates'],10)
        self.assertEqual(stages['selected']['distinct_candidates'],10)
        self.assertNotIn('validated',stages)
        early=next(i for i,text in enumerate(events) if text.startswith('Candidate coverage:'))
        self.assertLess(early,events.index('SIM'))
    def test_failed_selection_keeps_exception_evidence_and_adds_stage_counts(self):
        from main_window import LineupBuildWorker,_deep_shortlist
        from showdown_simulation import run_deep_showdown
        from selection_shortage import PortfolioSelectionShortage
        rows=bank(10,captain=10)
        players=list({p['FlexID']:p for row in rows for p in [row['Captain']]+row['Flex']}.values())
        worker=LineupBuildWorker(players,kind='showdown',num_lineups=10,salary_cap=50000,sim_enabled=True,compute_mode='Deep',salary_strategy='Balanced Spend',deep_time_limit_seconds=1)
        worker.library_candidates=rows;error=PortfolioSelectionShortage('original shortage');error.recovery_diagnostics={'kept':True}
        with patch('showdown_simulation.simulate_showdown',return_value={'lineups':rows,'report':{'scenarios':0}}),patch('showdown_simulation.select_portfolio',side_effect=error):
            with self.assertRaises(PortfolioSelectionShortage) as caught:run_deep_showdown(worker,_deep_shortlist)
        self.assertIs(caught.exception,error);self.assertEqual(error.recovery_diagnostics,{'kept':True})
        self.assertIn('original shortage',str(error));self.assertIn('Available to final selection: 10 distinct',str(error))

    def test_render_order_survives_sorted_json_serialization(self):
        import json
        rows=bank(2,captain=2);c=inspect(rows,[],[rows[0]['Captain']],{},2)
        stages={key:c for key in ('salary_eligible','shortlisted','validated','selection_bank','selected')}
        self.assertEqual(lines(stages),lines(json.loads(json.dumps(stages,sort_keys=True))))
