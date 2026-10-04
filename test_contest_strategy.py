from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from contest_strategy import execution_profile, sampled_payout, attach_strategy
from lineup_ranking import ranked_lineups, finish_tooltip
from nfl_simulation import SimLineup, simulate_nfl_contest
from optimizers import ShowdownLineup, ShowdownOptimizer, MultiSportClassicOptimizer
from portfolio_rules import select_portfolio
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players
from showdown_simulation import simulate_showdown


def profile(objective='DOUBLE_UP',payouts='1-4 = 20'):
    return execution_profile(dict(name='Explicit contest',objective=objective,field_size=10,
                                  entry_fee=10,user_entries=1,payouts=payouts))


class ContestStrategyTests(unittest.TestCase):
    def test_missing_profile_cannot_silently_use_tournament(self):
        self.assertIsNone(execution_profile(None,'TOURNAMENT'))
        for intent in ('DOUBLE_UP','MULTIPLIER'):
            with self.assertRaisesRegex(ValueError,'require a real contest profile'):
                execution_profile(None,intent)

    def test_double_up_structure_is_explicit_and_not_inferred(self):
        for payouts in ('1=30\n2-4=20','2-4=20','1-4=10','1=20\n3-4=20'):
            with self.assertRaisesRegex(ValueError,'Double-Up requires'):
                profile(payouts=payouts)
        self.assertEqual(profile('MULTIPLIER','1-2=50')['objective'],'MULTIPLIER')
        self.assertEqual(profile('TOURNAMENT')['objective'],'TOURNAMENT')

    def test_cutoff_tie_splits_paid_and_unpaid_places(self):
        # 9 sampled opponents + this entry, tied ranks 4-6: one paid prize / 3.
        self.assertAlmostEqual(sampled_payout([0,1,2,3,5,5,6,7,8],5,profile()),20/3)
        self.assertEqual(sampled_payout(list(range(9)),100,profile()),20)
        self.assertEqual(sampled_payout(list(range(9)),-1,profile()),0)

    def test_objective_changes_single_entry_selection_and_tooltip(self):
        rows = [SimLineup([dict(Name=name,FlexNamePlusID=name,Team='A',FlexProjection=20)])
                for name in ('Steady','Ceiling')]
        base = [dict(sim_scenarios=100,sim_cash_rate=80,sim_expected_profit=1,sim_mean=40,sim_top_one_pct=1),
                dict(sim_scenarios=100,sim_cash_rate=30,sim_expected_profit=8,sim_mean=35,sim_top_one_pct=20)]
        for objective,winner in (('DOUBLE_UP','Steady'),('MULTIPLIER','Ceiling'),('TOURNAMENT','Ceiling')):
            p = profile(objective)
            for row,m in zip(rows,base):
                row.sim_metrics = attach_strategy(dict(m),p)
            selected = select_portfolio(rows,1,allow_relaxation=False,refinement_passes=100)['lineups']
            self.assertEqual(selected[0][0]['Name'],winner)
            self.assertEqual(ranked_lineups(rows)[0][0]['Name'],winner)
            self.assertIn('Paid-finish rate',finish_tooltip(selected[0]))
            self.assertIn(objective.title().replace('_','-'),finish_tooltip(selected[0]))

    def test_mixed_payout_profiles_rejected(self):
        rows = [SimLineup([dict(Name=str(i),FlexNamePlusID=str(i),Team='A',FlexProjection=10)]) for i in range(2)]
        for row,p in zip(rows,(profile(),profile(payouts='1-3=20'))):
            row.sim_metrics = attach_strategy(dict(sim_scenarios=100),p)
        with self.assertRaisesRegex(ValueError,'consistently scored'):
            select_portfolio(rows,1)

    def test_both_formats_exact_cash_metrics_and_no_input_mutation(self):
        for kind,players,opt in (('classic',_fixture_players(),MultiSportClassicOptimizer),
                                 ('showdown',_showdown_players(),ShowdownOptimizer)):
            before = copy.deepcopy(players)
            rows = opt(players,salary_cap=50000).build_lineups(3)
            if kind == 'classic':
                result = simulate_nfl_contest(rows,players,scenarios=20,field_lineup_count=30,
                                              field_config={'contest_profile':profile()})
            else:
                result = simulate_showdown(rows,players,scenarios=20,field_lineup_count=30,contest_profile=profile())
            self.assertEqual(result['report']['scenarios'],20)
            for row in result['lineups']:
                m = row.sim_metrics
                self.assertEqual(m['sim_selection_objective'],'DOUBLE_UP')
                self.assertAlmostEqual(m['sim_expected_profit'],m['sim_expected_payout']-10)
                self.assertTrue(0<=m['sim_cash_rate']<=100)
            self.assertEqual(players,before)

    def test_showdown_rescore_drops_previous_contest_strategy(self):
        players = _showdown_players()
        rows = ShowdownOptimizer(players,50000).build_lineups(2)
        paid = simulate_showdown(rows,players,scenarios=5,field_lineup_count=15,contest_profile=profile())
        ordinary = simulate_showdown(paid['lineups'],players,scenarios=5,field_lineup_count=15)
        for row in ordinary['lineups']:
            self.assertNotIn('sim_selection_fields',row.sim_metrics)
            self.assertNotIn('sim_expected_profit',row.sim_metrics)

    def test_worker_blocks_unscored_cash_and_fast_showdown(self):
        from main_window import LineupBuildWorker
        for kwargs in (dict(contest_objective='DOUBLE_UP'),
                       dict(contest_profile=profile(),kind='showdown'),
                       dict(contest_profile=profile(),sim_enabled=False)):
            worker = LineupBuildWorker(_showdown_players(),**{'kind':'classic','salary_cap':50000,'num_lineups':1,**kwargs})
            errors,finished = [],[]
            worker.error.connect(lambda *args: errors.append(args))
            worker.finished.connect(finished.append)
            worker.run()
            self.assertEqual(len(errors),1)
            self.assertFalse(finished)

    def test_saved_showdown_repeatability_preserves_payout_profile_and_rank_label(self):
        import tempfile
        from pathlib import Path
        from repeatability import save_bank,run_repeatability,format_report
        players = _showdown_players()
        rows = ShowdownOptimizer(players).build_lineups(2)
        p = profile()
        for row in rows:
            row.sim_metrics = attach_strategy(dict(sim_scenarios=1000,sim_cash_rate=80,sim_expected_profit=6,sim_top_one_pct=1),p)
        with tempfile.TemporaryDirectory() as folder:
            receipt = save_bank(rows,players,kind='showdown',salary_cap=50000,field_count=10,
                                field_config={'contest_profile':p},folder=folder)
            def simulate(candidates, pool, **kwargs):
                self.assertEqual(kwargs['contest_profile'],p)
                return {'lineups':candidates,'report':{'scenarios':kwargs['scenarios']}}
            with patch('showdown_simulation.simulate_showdown',side_effect=simulate):
                result = run_repeatability(Path(folder)/(receipt['bank_id']+'.dfsbank'),batches=2,scenarios=1000)
            self.assertEqual(result['completed_batches'],2)
            self.assertIn('Double-Up: paid-finish rate',format_report(result))
