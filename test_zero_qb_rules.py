from test_environment import install
install()
import copy
import tempfile
import unittest
from unittest.mock import patch
from optimizers import ShowdownLineup
from portfolio_rules import select_portfolio, normalize_rules, portfolio_report
from zero_qb_rules import maximum, count_limit, is_zero
from selection_shortage import PortfolioSelectionShortage


def lineup(index, qb=None, shared=None):
    players = [dict(Name=f'{index}-{i}', FlexID=f'{index}-{i}', Team='A' if i<3 else 'B',
                    Position='WR', FlexProjection=100-index, CptProjection=150-index) for i in range(6)]
    if qb is not None:
        players[qb]['Position']='QB'
        if shared:players[qb].update(Name=shared, FlexID=shared)
    return ShowdownLineup(players[0], players[1:])


class ZeroQbTests(unittest.TestCase):
    def choose(self, rows, n=3, **kwargs):
        rules=dict(balance_ownership=False,min_unique=1,max_zero_qb_pct=0)
        rules.update(kwargs.pop('rules',{}))
        return select_portfolio(rows,n,kind='showdown',rules=rules,allow_relaxation=False,**kwargs)

    def test_unset_preserves_exact_default_output_and_report(self):
        rows=[lineup(i,qb=None if i<3 else 1) for i in range(8)]
        a=select_portfolio(rows,3,kind='showdown',rules=dict(balance_ownership=False))
        b=select_portfolio(rows,3,kind='showdown',rules=dict(balance_ownership=False,max_zero_qb_pct=None))
        self.assertEqual(a,b)
        self.assertNotIn('max_zero_qb_pct',normalize_rules({}))

    def test_zero_requires_qb_at_captain_or_flex_and_survives_refinement(self):
        rows=[lineup(i) for i in range(4)]+[lineup(4,0),lineup(5,1),lineup(6,1),lineup(7,0)]
        result=self.choose(rows,refinement_passes=30,refinement_polish_duplication=True)
        self.assertEqual(len(result['lineups']),3)
        self.assertFalse(any(is_zero(r) for r in result['lineups']))
        self.assertEqual(result['report']['zero_qb_rule']['count'],0)
        self.assertIn('Zero-QB maximum (explicit)',result['report']['text'])

    def test_percentage_rounds_down_against_requested_not_candidate_count(self):
        rows=[lineup(i) for i in range(5)]+[lineup(i,1) for i in range(5,10)]
        result=self.choose(rows,rules=dict(max_zero_qb_pct=50))
        self.assertEqual(sum(is_zero(r) for r in result['lineups']),1)
        self.assertEqual(result['report']['zero_qb_rule']['limit'],1)
        self.assertEqual(count_limit(5,19),0)
        self.assertEqual(count_limit(100,3),3)

    def test_unknown_position_is_rejected_only_when_active(self):
        row=lineup(1,1);row['Flex'][2].pop('Position')
        with self.assertRaisesRegex(ValueError,'position metadata'):self.choose([row],n=1)
        select_portfolio([row],1,kind='showdown',rules=dict(balance_ownership=False))

    def test_retained_zero_qb_conflicts_do_not_weaken_the_limit(self):
        row=lineup(0)
        with self.assertRaisesRegex(PortfolioSelectionShortage,'Retained'):
            self.choose([lineup(i,1) for i in range(1,5)],retained_lineups=[row])

    def test_no_qb_bank_stops_with_explicit_blocker_and_no_partial_result(self):
        with self.assertRaisesRegex(PortfolioSelectionShortage,'Zero-QB maximum 0/3'):
            self.choose([lineup(i) for i in range(4)],automatic_recovery=True,repair_time_limit=1)

    def test_manual_qb_fade_remains_hard(self):
        rows=[lineup(i,1) for i in range(4)]
        for row in rows:row['Flex'][0]['FadeFlex']=True
        with self.assertRaises(PortfolioSelectionShortage):self.choose(rows,automatic_recovery=True,repair_time_limit=1)

    def test_manual_qb_exposure_cap_remains_hard(self):
        rows=[lineup(i,1,shared='QB') for i in range(5)]
        with self.assertRaises(PortfolioSelectionShortage):
            self.choose(rows,rules=dict(player_constraints={'QB':dict(MaxPct=0)}),automatic_recovery=True,repair_time_limit=1)

    def test_auto_recovery_can_raise_player_caps_without_relaxing_zero_qb(self):
        rows=[lineup(i,1,shared='QB') for i in range(6)]+[lineup(i) for i in range(6,12)]
        result=self.choose(rows,n=5,rules=dict(balance_ownership=True),automatic_recovery=True,repair_time_limit=1)
        self.assertFalse(any(is_zero(r) for r in result['lineups']))
        self.assertTrue(result['report']['portfolio_recovery']['automatic_recovery_ran'])
        self.assertEqual(result['report']['zero_qb_rule']['limit'],0)

    def test_feasibility_witness_preserves_construction_limit(self):
        rows=[lineup(i) for i in range(4)]+[lineup(i,1) for i in range(4,8)]
        result=self.choose(rows,feasibility_only=True,automatic_recovery=True,repair_time_limit=5)
        self.assertFalse(any(is_zero(r) for r in result['lineups']))

    def test_exact_cbc_witness_preserves_construction_limit(self):
        rows=[lineup(i) for i in range(4)]+[lineup(i,1) for i in range(4,8)]
        with patch('portfolio_feasibility.diversity_first_witness',return_value=None):
            result=self.choose(rows,feasibility_only=True,automatic_recovery=True,repair_time_limit=5)
        self.assertEqual(result['method'],'bounded CBC repair')
        self.assertEqual(len(result['lineups']),3)
        self.assertFalse(any(is_zero(r) for r in result['lineups']))

    def test_allowed_retained_zero_qb_uses_the_same_budget(self):
        fixed=lineup(0)
        rows=[lineup(i) for i in range(1,4)]+[lineup(i,1) for i in range(4,8)]
        result=self.choose(rows,rules=dict(max_zero_qb_pct=50),retained_lineups=[fixed],
                           refinement_passes=20,automatic_recovery=True)
        self.assertIn(fixed,result['lineups'])
        self.assertEqual(sum(is_zero(r) for r in result['lineups']),1)

    def test_cancelled_repair_does_not_publish_a_portfolio(self):
        with self.assertRaisesRegex(ValueError,'cancelled'):
            self.choose([lineup(i) for i in range(4)],selection_cancel_callback=lambda:True)

    def test_invalid_percentages_fail_closed(self):
        for value in (-1,101,float('nan'),float('inf'),'bad',True):
            with self.subTest(value=value),self.assertRaises(ValueError):maximum(value)

    def test_report_marks_conflicting_portfolio_noncompliant(self):
        result=portfolio_report([lineup(1)],dict(max_zero_qb_pct=0),kind='showdown',requested=1)
        self.assertFalse(result['compliant'])
        self.assertTrue(any('zero-QB' in warning for warning in result['warnings']))

    def test_recipe_snapshot_and_generation_compatibility(self):
        from build_recipes import normalize_recipe,dump_recipes_json,load_recipes_json
        from build_snapshots import create_snapshot,validate_snapshot
        from candidate_library import candidate_generation_id
        recipe=dict(sport='NFL',contest_kind='showdown',max_zero_qb_pct=10)
        self.assertEqual(load_recipes_json(dump_recipes_json({'test':recipe}))['test']['max_zero_qb_pct'],10)
        self.assertNotIn('max_zero_qb_pct',normalize_recipe(dict(max_zero_qb_pct=None)))
        a=create_snapshot([lineup(1)['Captain']],dict(sport='NFL',contest_kind='showdown'),{})
        b=create_snapshot(a['inputs']['players'],recipe,dict(max_zero_qb_pct=10))
        validate_snapshot(b)
        self.assertNotEqual(a['input_id'],b['input_id'])
        self.assertEqual(candidate_generation_id(a),candidate_generation_id(b))

    def test_screening_and_full_stream_identity_ignore_only_this_portfolio_rule(self):
        from showdown_screening import target
        from showdown_full_screening import _location
        args=dict(limit=16,salary_cap=50000,salary_strategy='Flexible',screening={},folder='cache')
        rules=dict(min_unique=2)
        with patch('showdown_screening.validate_library',return_value=dict(status='complete')):
            a=target('library',[],rules=rules,**args)
            b=target('library',[],rules=dict(rules,max_zero_qb_pct=0),**args)
            c=target('library',[],rules=dict(rules,min_unique=3),**args)
        self.assertEqual(a,b)
        self.assertEqual(_location(*a),_location(*b))
        self.assertNotEqual(a,c)
        self.assertEqual(rules,dict(min_unique=2))
