import unittest

from compute_settings import deep_phase_fractions
from optimizers import ShowdownOptimizer, MultiSportClassicOptimizer, ShowdownLineup, _pkey
from nfl_simulation import SimLineup
from portfolio_rules import select_portfolio
from ranked_diagnostics import ranked_group_summaries, format_ranked_groups
from build_diagnostics import _aggregate_warning, create_build_diagnostic, format_build_report
from test_showdown_performance import _showdown_players
from test_nfl_logic import _fixture_players


def sd_signature(lu):
    return (_pkey(lu['Captain']), tuple(sorted(_pkey(p) for p in lu['Flex'])))


class CandidateDiversityTests(unittest.TestCase):
    def test_showdown_next_batch_excludes_previous_batch_even_with_same_seed(self):
        players = _showdown_players()
        first = ShowdownOptimizer(players, seed=14).build_lineups(40)
        seen = {sd_signature(lu) for lu in first}
        second = ShowdownOptimizer(players, seed=14).build_lineups(40, excluded_signatures=seen)
        self.assertEqual(len(first), 40)
        self.assertEqual(len(second), 40)
        self.assertFalse(seen.intersection(sd_signature(lu) for lu in second))
        for lu in second:
            self.assertEqual(len({_pkey(p) for p in [lu['Captain']] + lu['Flex']}), 6)

    def test_classic_exact_exclusions_do_not_reemit_prior_candidates(self):
        players = _fixture_players()
        first = MultiSportClassicOptimizer(players, sport='NFL', seed=14).build_lineups(30)
        seen = {tuple(sorted(_pkey(p) for p in lu)) for lu in first}
        second = MultiSportClassicOptimizer(players, sport='NFL', seed=14).build_lineups(30, exact_excluded_signatures=seen)
        self.assertEqual(len(second), 30)
        self.assertFalse(seen.intersection(tuple(sorted(_pkey(p) for p in lu)) for lu in second))

    def test_individual_time_share_reserves_sim_and_portfolio_is_unchanged(self):
        self.assertEqual(deep_phase_fractions({}), (.38, .58))
        self.assertEqual(deep_phase_fractions({'selection_mode':'Individual ranking'}), (.60, .78))

    def test_ranked_blocks_have_correct_denominators_order_and_flags(self):
        rows=[]
        for i in range(301):
            captain=dict(FlexID='core', Name='Core', Team='A', Position='QB', FlexSalary=5000, CptSalary=7500)
            flex=[dict(FlexID=f'p{j}', Name=f'Player {j}', Team='B', Position='WR', FlexSalary=8000) for j in range(5)]
            lu=ShowdownLineup(captain,flex)
            lu.sim_metrics=dict(sim_scenarios=10000, sim_top_one_pct=(301-i)/10, sim_top_two_pct=40,
                                sim_top_five_pct=50,sim_win_rate=1,sim_mean=100,
                                showdown_correlation_flags=['example flag'] if i<150 else [])
            rows.append(lu)
        groups=ranked_group_summaries(list(reversed(rows)), 'showdown')
        self.assertEqual([g['count'] for g in groups], [150,150,1])
        self.assertEqual([g['flagged_lineups'] for g in groups], [150,0,0])
        self.assertEqual(groups[0]['metrics']['sim_top_one_pct']['high'], 30.1)
        self.assertEqual(groups[2]['captain_exposures'][0]['pct'],100)
        self.assertEqual(groups[2]['captain_exposures'][0]['count'],1)
        text='\n'.join(format_ranked_groups(groups))
        self.assertIn('Ranks 301–301',text)
        self.assertIn('Core [A QB] 1/1 (100.0%)',text)
        self.assertNotIn('/150',text.split('Ranks 301–301')[1])

    def test_report_integrates_classic_groups_and_discloses_names(self):
        rows=[SimLineup([dict(FlexID='p',Name='Example',Team='A',Position='QB',FlexSalary=5000)],
                       metrics=dict(sim_scenarios=100,sim_top_one_pct=5,sim_mean=10))]
        report=create_build_diagnostic(context=dict(kind='classic'),timing_report={},lineups=rows)
        text=format_build_report(report)
        self.assertIn('Ranked groups',text)
        self.assertIn('Example [A QB]',text)
        self.assertNotIn('no players,',text)

    def test_intentional_individual_mode_is_not_a_failed_rule(self):
        rows=[SimLineup([dict(FlexID=str(i),Team='A')],metrics=dict(sim_scenarios=100,sim_top_one_pct=i)) for i in range(4)]
        report=select_portfolio(rows,2,individual_ranking=True,refinement_passes=256)['report']
        self.assertEqual(report['refinement_stop_reason'],'disabled in individual ranking')
        self.assertEqual(report['refinement_seconds'],0)
        self.assertFalse(any('Individual ranking' in warning for warning in report['warnings']))
        self.assertEqual(_aggregate_warning('Individual ranking uses simulated finish rates subject to explicit portfolio rules.'),'')
        warning=_aggregate_warning('Automatic Showdown exposure guardrails were relaxed 14 times to fill the requested portfolio; displayed starting caps are not final limits.')
        self.assertIn('raised 14 times',warning)


if __name__ == '__main__':
    unittest.main()
