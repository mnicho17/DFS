from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from qb_coverage import normalize_coverage, coverage_report, paired_point_review
from portfolio_rules import select_portfolio
from test_portfolio_rules import _player
from optimizers import ShowdownLineup


def fixtures():
    a = _player('A','ARI','G',20,Position='QB',NFLDepthOrder=1,NFLQBEligible=True)
    b = _player('B','CAR','G',15,Position='QB',NFLDepthOrder=1,NFLQBEligible=True)
    wr = _player('WR','ARI','G',10,Position='WR')
    rows = []
    for i, included in enumerate(((a,b),(a,),(b,),())):
        captain = _player('C'+str(i),'CAR','G',10,Position='RB')
        flex = list(included)+[wr]
        flex += [_player(f'F{i}-{j}','ARI' if j%2 else 'CAR','G',5,Position='RB') for j in range(5-len(flex))]
        rows.append(ShowdownLineup(captain,flex))
    players = list({p['FlexNamePlusID']:p for row in rows for p in [row['Captain']]+row['Flex']}.values())
    config = dict(enabled=True,qb_a='A',qb_b='B',fade_a=2,fade_b=2,neither=1)
    return rows,players,config


class QBCoverageTests(unittest.TestCase):
    def test_occurrences_and_receiver_dependence(self):
        rows,players,config = fixtures()
        report = coverage_report(rows+[rows[0]],players,config)
        self.assertEqual(report['counts'],dict(both=2,a_only=1,b_only=1,neither=1))
        self.assertEqual(report['qbs'][0]['receiver_dependent_entries'],5)
        self.assertEqual(report['qbs'][0]['non_captain'],3)

    def test_targets_survive_selection_and_retained_rows(self):
        rows,players,config = fixtures()
        result = select_portfolio(rows,3,kind='showdown',rules=dict(qb_coverage=config,player_constraints={'WR':{'MaxPct':100}}),
                                  retained_lineups=[rows[1]],automatic_recovery=True,refinement_passes=100)
        self.assertEqual({id(lu) for lu in result['lineups']},{id(lu) for lu in rows[1:]})
        self.assertFalse(any(v['shortage'] for v in result['report']['qb_coverage']['targets'].values()))
        self.assertFalse(result['report']['portfolio_recovery']['automatic_recovery_ran'])

    def test_incompatible_retained_rules_and_insufficient_bank_fail_without_relaxation(self):
        rows,players,config = fixtures()
        for bank,retained in ((rows,[rows[0]]),(rows[:3],[])):
            with self.assertRaisesRegex(ValueError,'QB targets remain hard'):
                select_portfolio(bank,3,kind='showdown',rules=dict(qb_coverage=config),retained_lineups=retained,automatic_recovery=True)

    def test_disabled_parity_and_validation(self):
        rows,players,config = fixtures()
        plain = select_portfolio(rows,3,kind='showdown')
        disabled = select_portfolio(rows,3,kind='showdown',rules=dict(qb_coverage=dict(config,enabled=False)))
        self.assertEqual(plain,disabled)
        for changed in (dict(config,qb_b='A'),dict(config,fade_a=-1),dict(config,fade_a=1.1)):
            with self.assertRaises(ValueError): normalize_coverage(changed)

    def test_paired_exit_points_captain_receiver_and_no_mutation(self):
        rows,players,config = fixtures()
        a = next(p for p in players if p['Name']=='A')
        rows[0] = ShowdownLineup(a,[p for p in rows[0]['Flex'] if p['Name']!='A']+[rows[0]['Captain']])
        before = copy.deepcopy((rows,players,config))
        outcomes = {p['FlexNamePlusID']:p['FlexProjection'] for p in players}
        with patch('nfl_simulation._scenario_outcomes',return_value=outcomes) as draws:
            normal = paired_point_review(rows,players,config,elapsed=1,scenarios=3)
            stress = paired_point_review(rows,players,config,elapsed=0,receiver_efficiency=.5,scenarios=3)
        self.assertEqual(draws.call_count,6)
        self.assertTrue(all(case['mean_change']==0 for case in normal['cases']))
        self.assertLess(stress['cases'][1]['mean_change'],stress['cases'][2]['mean_change'])
        self.assertIn('Not a paid-finish',stress['competitive_threshold'])
        self.assertEqual((rows,players,config),before)

    def test_cancel_and_missing_qb_and_backup_cannot_enter_review(self):
        rows,players,config = fixtures()
        result = paired_point_review(rows,players,config,cancelled=lambda:True)
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['scenarios'],0)
        with self.assertRaisesRegex(ValueError,'missing'):
            paired_point_review(rows,[p for p in players if p['Name']!='A'],config)
        with self.assertRaisesRegex(ValueError,'eligible starting'):
            paired_point_review(rows,[dict(p,NFLQBEligible=False) if p['Name']=='A' else p for p in players],config)

    def test_selection_cancel_and_explicit_minimum_conflict_remain_hard(self):
        rows,players,config = fixtures()
        with self.assertRaisesRegex(ValueError,'cancelled'):
            select_portfolio(rows,3,kind='showdown',rules=dict(qb_coverage=config),selection_cancel_callback=lambda:True)
        rules = dict(qb_coverage=config,player_constraints={'A':{'MinPct':100},'WR':{'MaxPct':100}})
        with self.assertRaisesRegex(ValueError,'QB targets remain hard'):
            select_portfolio(rows,3,kind='showdown',rules=rules,automatic_recovery=True)

    def test_saved_report_exposes_shortage_and_diagnostics_remove_player_keys(self):
        from portfolio_rules import portfolio_report
        from build_diagnostics import create_build_diagnostic,format_build_report
        rows,players,config = fixtures()
        report = portfolio_report(rows[:2],dict(qb_coverage=config),kind='showdown')
        self.assertFalse(report['compliant'])
        self.assertTrue(any('QB coverage' in warning for warning in report['warnings']))
        qb = coverage_report(rows,players,config)
        diagnostic = create_build_diagnostic(context={},timing_report={},sim_report={'qb_coverage':qb})
        self.assertTrue(all('key' not in row for row in diagnostic['qb_coverage']['qbs']))
        self.assertIn('Both QBs',format_build_report(diagnostic))

    def test_conditional_captain_arithmetic(self):
        rows,players,config = fixtures()
        a = next(p for p in players if p['Name']=='A')
        row = ShowdownLineup(a,[p for p in rows[0]['Flex'] if p['Name']!='A']+[rows[0]['Captain']])
        outcomes = {p['FlexNamePlusID']:p['FlexProjection'] for p in players}
        with patch('nfl_simulation._scenario_outcomes',return_value=outcomes):
            result = paired_point_review([row],players,config,elapsed=.25,receiver_efficiency=.5,scenarios=1)
        self.assertAlmostEqual(result['cases'][1]['mean_change'],-20*.75*1.5-10*.75*.5)
