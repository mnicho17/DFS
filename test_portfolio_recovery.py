"""Offline behavioral contracts for AR-02 against the current strict selector."""
from test_environment import install, network_attempts
install()

from collections import Counter
from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from portfolio_rules import select_portfolio, player_key, _candidate_signature
from selection_shortage import PortfolioSelectionShortage
from candidate_recovery import expand_candidates
import showdown_simulation  # Load collaborators before temporary optimizer fakes.


def player(key, **kwargs):
    return dict(Name=key, FlexID=key, CptID='cpt-' + key, Team='A' if key.startswith('c') else 'B',
                Position='WR', FlexSalary=6000, CptSalary=9000,
                FlexProjection=10, CptProjection=15, **kwargs)


def bank(n=10, total=0, captain=0, specialist=0):
    common = player('common')
    captain_player = player('captain')
    rows = []
    for i in range(n):
        cpt = captain_player if i < captain else player(f'c{i}')
        if i < specialist:
            cpt = dict(cpt, Position='K' if i % 2 else 'DST')
        flex = [player(f'f{i}-{j}') for j in range(5)]
        if i < total:
            flex[0] = common
        rows.append(dict(Captain=cpt, Flex=flex))
    return rows


def choose(rows, **kwargs):
    return select_portfolio(rows, kwargs.pop('requested', len(rows)), kind='showdown',
                            automatic_recovery=True, allow_relaxation=False, **kwargs)


class RecoveryTests(unittest.TestCase):
    def test_strict_success_never_invokes_recovery(self):
        with patch('portfolio_recovery.recover', side_effect=AssertionError('unneeded fallback')):
            result = choose(bank())
        diag = result['report']['portfolio_recovery']
        self.assertFalse(diag['automatic_recovery_ran'])
        self.assertEqual(diag['strict_selected_count'], 10)
        self.assertEqual(diag['starting_automatic_caps'], diag['effective_automatic_caps'])

    def test_total_cap_bounded_stages_stop_at_first_success(self):
        for n, count, stage in [(10, 9, 'ceil+10%'), (10, 10, 'ceil+25%'), (150, 150, 'required')]:
            with self.subTest(n=n, count=count):
                result = choose(bank(n, total=count))
                diag = result['report']['portfolio_recovery']
                self.assertEqual(len(result['lineups']), n)
                self.assertEqual(diag['recovery_stage'], stage)
                self.assertEqual(diag['attempted_stages'][-1], stage)
                self.assertEqual(diag['effective_automatic_caps']['total']['common'], count)
                self.assertIn('concentration increased', result['report']['text'])
                self.assertNotIn('without weakening', result['report']['text'])

    def test_captain_cap_recovery(self):
        result = choose(bank(captain=4))
        diag = result['report']['portfolio_recovery']
        self.assertEqual(diag['starting_automatic_caps']['captain']['captain'], 3)
        self.assertEqual(diag['effective_automatic_caps']['captain']['captain'], 4)
        self.assertEqual(diag['attempted_stages'], ['ceil+10%'])

    def test_combined_k_dst_captain_recovery(self):
        result = choose(bank(specialist=2))
        diag = result['report']['portfolio_recovery']
        self.assertEqual(diag['starting_automatic_caps']['specialist'], 1)
        self.assertEqual(diag['effective_automatic_caps']['specialist'], 2)

    def test_explicit_player_captain_and_zero_caps_never_relaxed(self):
        for field, value in [('MaxPct', 80), ('MaxPct', 0), ('MaxCptPct', 30), ('MaxCptPct', 0)]:
            with self.subTest(field=field, value=value):
                rows = bank(total=10, captain=4)
                target = rows[0]['Flex'][0] if field == 'MaxPct' else rows[0]['Captain']
                target[field] = value
                before = deepcopy(rows)
                with self.assertRaises(PortfolioSelectionShortage) as caught:
                    choose(rows)
                diag = caught.exception.recovery_diagnostics
                label = 'total' if field == 'MaxPct' else 'captain'
                self.assertNotIn(player_key(target), diag['starting_automatic_caps'][label])
                self.assertEqual(rows, before)

    def test_rule_map_override_and_zero_are_explicit(self):
        for value in (0, 80):
            with self.assertRaises(PortfolioSelectionShortage):
                choose(bank(total=10), rules={'player_constraints': {'common': {'MaxPct': value}}})

    def test_legacy_explicit_flex_cap_is_preserved(self):
        rows = bank(total=10)
        rows[0]['Flex'][0]['MaxFlexPct'] = 80
        with self.assertRaises(PortfolioSelectionShortage):
            choose(rows)

    def test_unrelated_rule_map_does_not_erase_player_lock_or_fade(self):
        rows = bank(total=9)
        rows[0]['Flex'][0]['LockFlex'] = True
        with self.assertRaises(PortfolioSelectionShortage):
            choose(rows, rules={'player_constraints': {'common': {'MinPct': 20}}})
        rows = bank(total=10)
        rows[0]['Flex'][0]['FadeFlex'] = True
        with self.assertRaises(PortfolioSelectionShortage):
            choose(rows, rules={'player_constraints': {'common': {'MaxCptPct': 0}}})

    def test_explicit_minimums_are_required_for_recovery(self):
        for field in ('MinPct', 'MinCptPct'):
            with self.subTest(field=field), self.assertRaises(PortfolioSelectionShortage):
                choose(bank(total=10), rules={'player_constraints': {'absent': {field: 10}}})

    def test_locks_fades_and_groups_preserved(self):
        rows = bank(total=10)
        rows[0]['Flex'][0]['LockFlex'] = True
        result = choose(rows, rules={'groups': [{'type': 'at_least_one', 'player_keys': ['common']}]})
        self.assertTrue(all('common' in {player_key(p) for p in lu['Flex']} for lu in result['lineups']))
        for tag in ('FadeFlex', 'LockCpt'):
            invalid = bank(total=10)
            invalid[0]['Flex'][0][tag] = True
            with self.subTest(tag=tag), self.assertRaises(PortfolioSelectionShortage):
                choose(invalid)
        for group in ({'type': 'never_together', 'player_keys': ['common', 'c0']},
                      {'type': 'at_least_one', 'player_keys': ['absent']}):
            with self.assertRaises(PortfolioSelectionShortage):
                choose(bank(total=10), rules={'groups': [group]})

    def test_team_and_game_caps_preserved_including_zero(self):
        for field in ('max_team_pct', 'max_game_pct'):
            for value in (0, 50):
                rows = bank(total=10)
                for lu in rows:
                    for p in [lu['Captain']] + lu['Flex']:
                        p['GameKey'] = 'A@B'
                with self.subTest(field=field, value=value), self.assertRaises(PortfolioSelectionShortage):
                    choose(rows, rules={field: value})

    def test_configured_uniqueness_never_relaxed(self):
        rows = bank(total=10)
        for row in rows:
            row['Flex'] = rows[0]['Flex']
        with self.assertRaises(PortfolioSelectionShortage):
            choose(rows, rules={'min_unique': 3})

    def test_retained_lineups_preserved(self):
        rows = bank(total=10)
        result = choose(rows[2:], requested=10, retained_lineups=rows[:2])
        self.assertTrue(all(any(lu is row for lu in result['lineups']) for row in rows[:2]))
        self.assertEqual(result['retained_count'], 2)

    def test_recovery_follows_strict_repair(self):
        import portfolio_feasibility
        original = portfolio_feasibility.repair
        calls = []
        def repair(*args, **kwargs):
            calls.append(deepcopy(args[5]['total']))
            return original(*args, **kwargs)
        with patch('portfolio_feasibility.repair', side_effect=repair):
            result = choose(bank(total=9))
        self.assertEqual([limits['common'] for limits in calls], [8, 9])
        self.assertEqual(result['report']['portfolio_recovery']['strict_selected_count'], 9)

    def test_strict_repair_success_never_invokes_recovery(self):
        # High-scoring cross-pair blocks the two disjoint pairs in greedy selection.
        a, b, c, d = [player(k) for k in 'abcd']
        rows = [[a, b], [a, c], [b, d]]
        with patch('portfolio_recovery.recover', side_effect=AssertionError('unneeded')):
            result = select_portfolio(rows, 2, automatic_recovery=True, allow_relaxation=False,
                                      rules={'min_unique': 2}, kind='classic')
        self.assertEqual(len(result['lineups']), 2)
        self.assertFalse(result['report']['portfolio_recovery']['automatic_recovery_ran'])

    def test_cancellation_never_starts_fallback(self):
        with patch('portfolio_recovery.recover', side_effect=AssertionError('cancel fallback')):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                choose(bank(total=10), selection_cancel_callback=lambda: True)
            cancelled = [False]
            def strict_repair(*args, **kwargs):
                cancelled[0] = True
                return None
            with patch('portfolio_feasibility.repair', side_effect=strict_repair):
                with self.assertRaisesRegex(ValueError, 'cancelled'):
                    choose(bank(total=10), selection_cancel_callback=lambda: cancelled[0])

    def test_no_remaining_deep_budget_skips_recovery(self):
        with patch('portfolio_recovery.recover', side_effect=AssertionError('no time')):
            with self.assertRaises(PortfolioSelectionShortage):
                choose(bank(total=10), recovery_deadline=time.perf_counter() - 1)

    def test_diagnostics_deterministic_and_inputs_unchanged(self):
        rows = bank(total=10)
        rules = {'min_unique': 2, 'groups': []}
        before = deepcopy((rows, rules))
        first = choose(rows, rules=rules)
        second = choose(rows, rules=rules)
        self.assertEqual(first['report']['portfolio_recovery'], second['report']['portfolio_recovery'])
        self.assertEqual((rows, rules), before)
        self.assertEqual(network_attempts, [])

    def test_minimums_and_retained_rows_survive_recovery_and_refinement(self):
        rows = bank(total=10, captain=4)
        rules = {'player_constraints': {'captain': {'MinCptPct': 40}, 'common': {'MinPct': 100}}}
        result = choose(rows[1:], requested=10, retained_lineups=rows[:1], rules=rules, refinement_passes=3)
        self.assertTrue(result['report']['compliant'])
        self.assertEqual(sum(player_key(lu['Captain']) == 'captain' for lu in result['lineups']), 4)
        self.assertIn(rows[0], result['lineups'])

    def test_cancel_during_recovery_discards_solver_success(self):
        import portfolio_feasibility
        repair = portfolio_feasibility.repair
        cancelled = [False]
        calls = []
        def run(*args, **kwargs):
            result = repair(*args, **kwargs)
            calls.append(result)
            if result:
                cancelled[0] = True
            return result
        with patch('portfolio_feasibility.repair', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                choose(bank(total=9), selection_cancel_callback=lambda: cancelled[0])
        self.assertEqual(len(calls), 2)

    def test_diagnostics_persist_without_mislabeling_or_disclosing_player_ids(self):
        from build_diagnostics import create_build_diagnostic, format_build_report
        result = choose(bank(total=9))
        diagnostic = create_build_diagnostic(context={'kind': 'showdown', 'sport': 'NFL'},
            timing_report={}, portfolio_report=result['report'], displayed_count=10)
        diag = diagnostic['portfolio']['portfolio_recovery']
        self.assertEqual(diag['recovery_stage'], 'ceil+10%')
        self.assertNotIn('common', str(diag))
        text = format_build_report(diagnostic)
        self.assertIn('concentration increased', text)
        self.assertIn('strict selected 9', text)
        self.assertNotIn('rule could not', text)

    def test_entry_safety_reviews_concentration_but_still_blocks_explicit_rules(self):
        from entry_safety import build_entry_safety_report
        result = choose(bank(total=9))
        report = result['report']
        safety = build_entry_safety_report(result['lineups'], kind='showdown', sport='NFL',
                                           salary_cap=50000, portfolio_report=report)
        self.assertEqual(safety['blockers'], 0)
        checks = {c['key']: c for c in safety['checks']}
        self.assertEqual(checks['automatic_recovery']['status'], 'review')
        self.assertEqual(checks['portfolio_rules']['status'], 'pass')
        report['warnings'].append('Explicit maximum exposure exceeded.')
        safety = build_entry_safety_report(result['lineups'], kind='showdown', sport='NFL',
                                           salary_cap=50000, portfolio_report=report)
        self.assertGreater(safety['blockers'], 0)
        self.assertEqual(next(c for c in safety['checks'] if c['key'] == 'portfolio_rules')['status'], 'block')

    def test_failed_recovery_never_commits_trial_caps(self):
        with self.assertRaises(PortfolioSelectionShortage) as caught:
            choose(bank(total=10), rules={'player_constraints': {'absent': {'MinPct': 10}}})
        diag = caught.exception.recovery_diagnostics
        self.assertEqual(diag['attempted_stages'], ['ceil+10%', 'ceil+25%', 'required'])
        self.assertEqual(diag['starting_automatic_caps'], diag['effective_automatic_caps'])
        self.assertEqual(diag['recovery_stage'], 'none')


class CandidateRecoveryTests(unittest.TestCase):
    def probe(self, rows, players, **kwargs):
        return expand_candidates(rows, players, 10, kwargs.pop('rules', {}),
            kind=kwargs.pop('kind', 'showdown'), salary_cap=50000, own_mode='Balanced',
            own_weight=.15, build_style='Strategic', **kwargs)

    def test_cpt_specific_retry_keeps_player_available_in_flex(self):
        rows = bank(captain=10)
        players = list({player_key(p): p for lu in rows for p in [lu['Captain']] + lu['Flex']}.values())
        before = deepcopy(players)
        probes = []
        class Fake:
            def __init__(self, ps, **kwargs): self.players = ps
            def _build_lineups_fast(self, **kwargs):
                cpt = next(p for p in self.players if p['FlexID'] == 'captain')
                probes.append(cpt)
                return [dict(Captain=self.players[1], Flex=[cpt] + self.players[2:6])]
        # Explicit total=100 isolates insufficient Captain coverage.
        players[0]['MaxPct'] = 100
        before = deepcopy(players)
        with patch('optimizers.ShowdownOptimizer', Fake):
            result = self.probe(rows, players, max_additions=1)
        self.assertTrue(probes[0]['FadeCpt'])
        self.assertFalse(probes[0].get('FadeFlex', False))
        self.assertIs(result[0]['Flex'][0], players[0])
        self.assertEqual(players, before)

    def test_classic_retries_capped_player_before_scoring(self):
        common = player('common', MaxPct=50)
        others = [player(f'p{i}') for i in range(12)]
        rows = [[common, p] for p in others[:10]]
        players = [common] + others
        before = deepcopy(players)
        probes = []
        class Fake:
            def __init__(self, ps, **kwargs): self.players = ps
            def build_lineups(self, **kwargs):
                probes.append((deepcopy(self.players), kwargs))
                return [[self.players[1], self.players[2]], [self.players[3], self.players[4]]]
        with patch('optimizers.MultiSportClassicOptimizer', Fake):
            result = self.probe(rows, players, kind='classic', max_additions=2)
        self.assertEqual(len(result), 2)
        self.assertTrue(probes[0][0][0]['FadeFlex'])
        self.assertEqual(players, before)
        self.assertIs(result[0][0], players[1])

    def test_no_coverage_retry_without_budget_or_after_cancel(self):
        rows = bank(total=10)
        players = [p for lu in rows for p in [lu['Captain']] + lu['Flex']]
        for options in ({'seconds': 0}, {'max_additions': 0}, {'cancelled': lambda: True}):
            with patch('optimizers.ShowdownOptimizer', side_effect=AssertionError('no retry')):
                self.assertEqual(self.probe(rows, players, **options), [])

    def test_showdown_recovery_candidates_keep_salary_floor_and_ceiling(self):
        rows = bank(total=10)
        players = list({player_key(p): p for lu in rows for p in [lu['Captain']] + lu['Flex']}.values())
        with patch('optimizers.ShowdownOptimizer._build_lineups_fast', return_value=bank()):
            # These 39,000-salary alternatives do not meet Near Cap's 47,500 floor.
            self.assertEqual(self.probe(rows, players, salary_strategy='Near Cap'), [])

    def test_sufficient_coverage_does_not_expand_a_high_share(self):
        rows = bank(30, total=25)
        players = list({player_key(p): p for lu in rows for p in [lu['Captain']] + lu['Flex']}.values())
        with patch('optimizers.ShowdownOptimizer', side_effect=AssertionError('sufficient coverage')):
            self.assertEqual(self.probe(rows, players), [])


class WorkerRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_deep_showdown_coverage_precedes_all_scoring_and_obeys_budget(self):
        from main_window import LineupBuildWorker, _deep_shortlist
        from showdown_simulation import run_deep_showdown
        from optimizers import ShowdownLineup
        rows = bank(total=10)
        players = list({player_key(p): p for lu in rows for p in [lu['Captain']] + lu['Flex']}.values())
        worker = LineupBuildWorker(players, kind='showdown', num_lineups=10, salary_cap=50000,
            sim_enabled=True, compute_mode='Deep', salary_strategy='Balanced Spend',
            deep_options={'candidates': 20, 'shortlist': 20, 'field': 10}, deep_time_limit_seconds=15)
        events, budgets = [], []
        def coverage(current, *args, **kwargs):
            events.append('coverage')
            budgets.append((len(current), kwargs['max_additions'], kwargs['seconds']))
            return []
        def simulate(current, *args, **kwargs):
            events.append('scoring')
            wrapped = [ShowdownLineup(lu['Captain'], lu['Flex']) for lu in current]
            return {'lineups': wrapped, 'report': {'scenarios': 1}}
        with patch('captain_coverage.seed_captains', return_value=0), \
             patch('showdown_simulation.ShowdownOptimizer.build_lineups', return_value=rows), \
             patch('showdown_coverage.expand_capped_candidates', side_effect=coverage), \
             patch('showdown_simulation.simulate_showdown', side_effect=simulate), \
             patch('feasible_shortlist.preserve', side_effect=lambda rows, *a, **k: (rows, [], {})):
            result = run_deep_showdown(worker, _deep_shortlist)
        self.assertIn('coverage', events)
        self.assertIn('scoring', events)
        self.assertLess(max(i for i,e in enumerate(events) if e == 'coverage'), events.index('scoring'))
        self.assertTrue(all(count + allowance == 20 and seconds <= 15 * .38 for count, allowance, seconds in budgets))
        self.assertEqual(len(result['lineups']), 10)
        self.assertEqual(result['portfolio_report']['portfolio_recovery']['recovery_stage'], 'ceil+25%')

    def test_saved_deep_showdown_library_never_generates_or_expands(self):
        from main_window import LineupBuildWorker, _deep_shortlist
        from showdown_simulation import run_deep_showdown
        rows = bank()
        players = list({player_key(p): p for lu in rows for p in [lu['Captain']] + lu['Flex']}.values())
        worker = LineupBuildWorker(players, kind='showdown', num_lineups=10, salary_cap=50000,
            sim_enabled=True, compute_mode='Deep', salary_strategy='Balanced Spend', deep_time_limit_seconds=1)
        worker.library_candidates = rows
        before = deepcopy(rows)
        with patch('showdown_simulation.ShowdownOptimizer', side_effect=AssertionError('library extension')), \
             patch('showdown_coverage.expand_capped_candidates', side_effect=AssertionError('library extension')), \
             patch('showdown_simulation.simulate_showdown', return_value={'lineups': rows, 'report': {'scenarios': 0}}):
            result = run_deep_showdown(worker, _deep_shortlist)
        self.assertEqual(len(result['lineups']), 10)
        self.assertEqual(rows, before)

    def test_cancelled_deep_work_never_invokes_automatic_recovery(self):
        from main_window import LineupBuildWorker
        from test_nfl_logic import _fixture_players
        from test_showdown_performance import _showdown_players
        for kind, players in [('classic', _fixture_players()), ('showdown', _showdown_players())]:
            worker = LineupBuildWorker(players, kind=kind, num_lineups=10, salary_cap=50000,
                sim_enabled=True, compute_mode='Deep', salary_strategy='Balanced Spend',
                repair_source='saved',
                deep_options={'candidates': 40, 'shortlist': 15, 'field': 20}, deep_time_limit_seconds=5)
            events, results, errors = [], [], []
            def progress(done, total, text):
                events.append(text)
                if 'Phase 2' in text:
                    worker.request_cancel()
            worker.progress.connect(progress)
            worker.finished.connect(results.append)
            worker.error.connect(errors.append)
            with patch('portfolio_recovery.recover', side_effect=AssertionError('cancelled fallback')):
                worker.run()
            self.assertFalse(errors, errors)
            self.assertTrue(any('Phase 2' in text for text in events), events)
            self.assertTrue(results[0]['cancelled'])

    def test_special_pre_cancelled_deep_showdown_receipt_is_preserved(self):
        from main_window import LineupBuildWorker
        from optimizers import ShowdownOptimizer
        from test_showdown_performance import _showdown_players
        players = _showdown_players()
        retained = ShowdownOptimizer(players).build_lineups(1)
        worker = LineupBuildWorker(players, kind='showdown', num_lineups=10, salary_cap=50000,
            sim_enabled=True, compute_mode='Deep', retained_lineups=retained)
        worker.request_cancel()
        results = []
        worker.finished.connect(results.append)
        with patch('portfolio_recovery.recover', side_effect=AssertionError('cancelled fallback')):
            worker.run()
        self.assertTrue(results[0]['cancelled'])
        self.assertEqual(_candidate_signature(results[0]['lineups'][0], 'showdown'),
                         _candidate_signature(retained[0], 'showdown'))
        self.assertIn('deep_build', results[0]['sim_report'])
        self.assertTrue(results[0]['portfolio_report']['warnings'])
        self.assertNotIn('portfolio_recovery', results[0]['portfolio_report'])

    def test_saved_deep_classic_library_never_generates_or_expands(self):
        from main_window import LineupBuildWorker
        from optimizers import MultiSportClassicOptimizer
        from test_nfl_logic import _fixture_players
        players = _fixture_players()
        rows = MultiSportClassicOptimizer(players, sport='NFL').build_lineups(10)
        worker = LineupBuildWorker(players, kind='classic', num_lineups=10, salary_cap=50000,
            sim_enabled=True, compute_mode='Deep', candidate_library='test-library.dfslib',
            repair_source='saved', deep_time_limit_seconds=5)
        worker.progress.connect(lambda done, total, text: worker.request_cancel() if 'Phase 2' in text else None)
        results, errors = [], []
        worker.finished.connect(results.append)
        worker.error.connect(errors.append)
        with patch('candidate_library.load_candidates', return_value=(rows, {'accepted': 10, 'rejected': 0})), \
             patch('main_window.MultiSportClassicOptimizer', side_effect=AssertionError('library extension')), \
             patch('candidate_recovery.expand_candidates', side_effect=AssertionError('library extension')):
            worker.run()
        self.assertFalse(errors, errors)
        self.assertTrue(results[0]['cancelled'])


if __name__ == '__main__':
    unittest.main()
