from test_environment import install
install()

import copy
import threading
import time
import unittest
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication
from main_window import LineupBuildWorker
from optimizers import ShowdownLineup, attach_showdown_metrics, lineup_is_complete_for_sport
from portfolio_recovery import expand_candidates, select_with_fallback
from portfolio_rules import _candidate_signature, player_key, select_portfolio
from selection_shortage import PortfolioSelectionShortage


def players(count=6):
    return [dict(FlexID=str(i), CptID='c'+str(i), Name='Synthetic '+str(i),
        Team='A' if i%2 else 'B', Position='RB', FlexSalary=7000, CptSalary=10500,
        FlexProjection=10+i, CptProjection=(10+i)*1.5, NFLDepthOrder=1)
        for i in range(count)]


def rows(ps):
    return [ShowdownLineup(ps[i], [p for p in ps if p is not ps[i]][:5]) for i in range(len(ps))]


class PortfolioRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_fast_worker_recovers_and_preserves_original_inputs(self):
        ps = players()
        before = copy.deepcopy(ps)
        worker = LineupBuildWorker(ps, kind='showdown', num_lineups=4,
            salary_cap=50000, sim_enabled=True, compute_mode='Fast')
        finished, errors = [], []
        worker.finished.connect(finished.append)
        worker.error.connect(errors.append)
        worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(finished), 1)
        result = finished[0]
        self.assertEqual(len(result['lineups']), 4)
        self.assertEqual(len({_candidate_signature(r, 'showdown') for r in result['lineups']}), 4)
        self.assertEqual(ps, before)
        self.assertEqual(result['portfolio_report']['automatic_fallback']['status'], 'completed')
        self.assertTrue(all(sum(float(p['FlexSalary']) for p in r['Flex']) + r['Captain']['CptSalary'] <= 50000 for r in result['lineups']))

    def test_strict_failure_becomes_complete_with_disclosed_effective_caps(self):
        bank = rows(players())
        with self.assertRaises(PortfolioSelectionShortage):
            select_portfolio(bank, 4, kind='showdown', allow_relaxation=False)
        result = select_with_fallback(bank, 4, kind='showdown')
        report = result['report']
        self.assertEqual(len(result['lineups']), 4)
        self.assertEqual(report['automatic_fallback']['attempts'], [1])
        self.assertEqual(report['automatic_showdown_guardrails']['effective_total_max_count'], 4)
        self.assertIn('Explicit limits, configured uniqueness', report['text'])
        self.assertTrue(all(any(r is b for b in bank) for r in result['lineups']))

    def test_real_deep_worker_keeps_validation_and_recovers_automatic_caps(self):
        worker = LineupBuildWorker(players(), kind='showdown', sport='NFL', num_lineups=4,
            salary_cap=50000, salary_strategy='Balanced Spend', sim_enabled=True, compute_mode='Deep',
            sim_scenarios=2500, deep_time_limit_seconds=20,
            deep_options={'candidates': 60, 'shortlist': 20, 'field': 30, 'screening': 250})
        finished, errors = [], []
        worker.finished.connect(finished.append)
        worker.error.connect(errors.append)
        worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(finished), 1)
        result = finished[0]
        self.assertEqual(len(result['lineups']), 4)
        self.assertGreater(result['timing_report']['validation_scenarios'], 0)
        self.assertTrue(all(getattr(row, 'sim_metrics', {}).get('sim_scenarios', 0) > 0 for row in result['lineups']))
        self.assertEqual(result['portfolio_report']['automatic_fallback']['status'], 'completed')

    def test_explicit_total_caps_including_zero_are_never_relaxed(self):
        for cap in (0, 50):
            with self.subTest(cap=cap):
                ps = players()
                ps[0]['MaxPct'] = cap
                before = copy.deepcopy(ps)
                with self.assertRaises(PortfolioSelectionShortage):
                    select_with_fallback(rows(ps), 4, kind='showdown')
                self.assertEqual(ps, before)

    def test_explicit_captain_caps_and_configured_player_constraints_survive(self):
        ps = players()
        ps[0]['MaxCptPct'] = 0
        rules = {'player_constraints': {player_key(ps[1]): {'MaxCptPct': 0}}}
        result = select_with_fallback(rows(ps), 4, kind='showdown', rules=rules)
        self.assertEqual(len(result['lineups']), 4)
        self.assertFalse(any(r['Captain'] in ps[:2] for r in result['lineups']))
        self.assertEqual(rules['player_constraints'][player_key(ps[1])]['MaxCptPct'], 0)

    def test_configured_uniqueness_groups_and_team_limits_are_never_relaxed(self):
        ps = players()
        for rules in ({'min_unique': 3}, {'max_team_pct': 50},
                      {'groups': [{'type':'never_together', 'player_keys':['0','1']}]},
                      {'max_game_pct': 50}):
            with self.subTest(rules=rules):
                for p in ps:
                    p['GameKey'] = 'A@B'
                with self.assertRaises(PortfolioSelectionShortage):
                    select_with_fallback(rows(ps), 4, kind='showdown', rules=rules)

    def test_retained_identity_and_successful_strict_order_are_preserved(self):
        bank = rows(players())
        retained = [bank[0]]
        result = select_with_fallback(bank[1:], 4, kind='showdown', retained_lineups=retained)
        self.assertIs(result['lineups'][0], retained[0])
        options = dict(kind='showdown', rules={'balance_ownership':False})
        expected = select_portfolio(bank, 4, allow_relaxation=False, **options)
        actual = select_with_fallback(bank, 4, **options)
        self.assertEqual([id(r) for r in actual['lineups']], [id(r) for r in expected['lineups']])
        self.assertNotIn('automatic_fallback', actual['report'])

    def test_individual_ranking_does_not_enable_automatic_targets(self):
        result = select_with_fallback(rows(players()), 4, kind='showdown', individual_ranking=True)
        self.assertEqual(len(result['lineups']), 4)
        self.assertEqual(result['report']['automatic_showdown_guardrails'], {})
        self.assertNotIn('automatic_fallback', result['report'])

    def test_retained_rows_cannot_bypass_explicit_limits_in_a_complete_portfolio(self):
        ps = players()
        ps[0]['MaxPct'] = 25
        bank = rows(ps)
        with self.assertRaises(PortfolioSelectionShortage):
            select_with_fallback(bank[2:], 4, kind='showdown', retained_lineups=bank[:2])

    def test_player_constraint_overrides_remain_hard_in_every_stage(self):
        ps = players()
        ps[0]['MaxPct'] = 100
        with self.assertRaises(PortfolioSelectionShortage):
            select_with_fallback(rows(ps), 4, kind='showdown',
                rules={'player_constraints': {player_key(ps[0]): {'MaxPct':50}}})

    def test_cancellation_during_fallback_never_returns_a_trial(self):
        event = threading.Event()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            select_with_fallback(rows(players()), 4, kind='showdown',
                selection_cancel_callback=event.is_set,
                progress_callback=lambda _: event.set())
        with patch('portfolio_recovery.select_portfolio', side_effect=AssertionError('must not run')):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                select_with_fallback([], 4, selection_cancel_callback=event.is_set)

    def test_expired_budget_stops_without_unbounded_retry(self):
        with self.assertRaisesRegex(PortfolioSelectionShortage, 'tried 0 bounded'):
            select_with_fallback(rows(players()), 4, kind='showdown', deadline=time.perf_counter()-1)

    def test_budget_expiry_during_a_trial_is_an_expected_shortage(self):
        bank = rows(players())
        with self.assertRaises(TimeoutError):
            select_portfolio(bank, 4, kind='showdown', allow_relaxation=False,
                automatic_cap_increase=1, selection_deadline=0)
        with patch('portfolio_recovery.select_portfolio',
                   side_effect=[PortfolioSelectionShortage('Original shortage'), TimeoutError('budget')]):
            with self.assertRaisesRegex(PortfolioSelectionShortage, 'tried 1 bounded'):
                select_with_fallback(bank, 4, kind='showdown')

    def test_classic_expansion_uses_real_optimizer_without_mutating_locks_or_fades(self):
        from test_nfl_logic import _fixture_players
        ps = _fixture_players()
        ps[0]['LockFlex'] = True
        ps[-1]['FadeFlex'] = True
        before = copy.deepcopy(ps)
        worker = LineupBuildWorker(ps, kind='classic', num_lineups=2, salary_cap=50000, sim_enabled=False)
        extra = expand_candidates(worker, ps, [], max_extra=3)
        self.assertTrue(extra)
        self.assertLessEqual(len(extra), 3)
        for row in extra:
            self.assertTrue(lineup_is_complete_for_sport(row, 'NFL'))
            self.assertIn(ps[0], row)
            self.assertNotIn(ps[-1], row)
            self.assertLessEqual(sum(p['FlexSalary'] for p in row), 50000)
        self.assertEqual(ps, before)

    def test_classic_cap_retry_detaches_nested_inputs_and_preserves_original_objects(self):
        ps = players(7)
        ps[0]['MaxPct'] = 50
        ps[0]['nested'] = {'value': 1}
        before = copy.deepcopy(ps)
        worker = LineupBuildWorker(ps, kind='classic', num_lineups=2, salary_cap=50000)
        def build(opt, **kwargs):
            self.assertTrue(opt.players[0]['FadeFlex'])
            opt.players[0]['nested']['value'] = 99
            self.assertIn(tuple(str(i) for i in range(6)), kwargs['exact_excluded_signatures'])
            return [opt.players[1:]]
        class Fake:
            def __init__(self, players, **kwargs): self.players = players
            build_lineups = build
        with patch('optimizers.MultiSportClassicOptimizer', Fake):
            extra = expand_candidates(worker, ps, [ps[:6], ps[:5]+ps[6:]], max_extra=1)
        self.assertEqual(ps, before)
        self.assertIs(extra[0][0], ps[1])

    def test_captain_retry_does_not_fade_the_player_from_flex(self):
        from showdown_coverage import expand_capped_candidates
        ps = players(7)
        ps[0].update(MaxPct=100, MaxCptPct=25)
        original = copy.deepcopy(ps)
        def build(opt, **kwargs):
            self.assertTrue(opt.players[0]['FadeCpt'])
            self.assertFalse(opt.players[0].get('FadeFlex'))
            return [ShowdownLineup(opt.players[1], [opt.players[0]] + opt.players[2:6])]
        class Fake:
            def __init__(self, players, **kwargs): self.players = players
            _build_lineups_fast = build
        with patch('optimizers.ShowdownOptimizer', Fake):
            extra = expand_capped_candidates([rows(ps)[0]], ps, 4, {'balance_ownership':False},
                salary_cap=50000, own_mode='Balanced', own_weight=.15, build_style='Strategic', max_extra=1)
        self.assertEqual(len(extra), 1)
        self.assertIs(extra[0]['Flex'][0], ps[0])
        self.assertEqual(ps, original)

    def test_cancelled_or_expired_expansion_does_not_construct_an_optimizer(self):
        worker = LineupBuildWorker(players(), kind='classic', num_lineups=4, salary_cap=50000)
        with patch('optimizers.MultiSportClassicOptimizer', side_effect=AssertionError('must not run')):
            self.assertEqual(expand_candidates(worker, worker.players, [], deadline=0), [])
            worker._cancel_event.set()
            self.assertEqual(expand_candidates(worker, worker.players, []), [])

    def test_constraint_defaults_cannot_make_a_locked_classic_player_a_retry_exclusion(self):
        ps = players()
        ps[0].update(LockFlex=True, MaxPct=50)
        worker = LineupBuildWorker(ps, kind='classic', num_lineups=1, salary_cap=50000,
            portfolio_rules={'player_constraints':{player_key(ps[0]):{'MaxPct':50}}})
        with patch('optimizers.MultiSportClassicOptimizer', side_effect=AssertionError('locked target')):
            self.assertEqual(expand_candidates(worker, ps, [ps]), [])

    def test_fallback_warning_and_effective_caps_survive_build_history(self):
        from build_diagnostics import create_build_diagnostic, format_build_report
        result = select_with_fallback(rows(players()), 4, kind='showdown')
        record = create_build_diagnostic(context={'sport':'NFL','kind':'showdown','settings':{}},
            portfolio_report=result['report'], lineups=result['lineups'], timing_report={})
        self.assertIn('Automatic fallback completed 4/4', format_build_report(record))
        self.assertEqual(record['portfolio']['automatic_showdown_guardrails']['fallback_cap_increase'], 1)
