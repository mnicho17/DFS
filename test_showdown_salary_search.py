from test_environment import install
install()
from copy import deepcopy
from itertools import combinations
import time
import unittest
from unittest.mock import patch
from optimizers import ShowdownOptimizer, _salary, _cpt_salary, _pkey
from showdown_simulation import filter_salary_candidates, showdown_signature
from lineup_ranking import BUILD_STYLES
from test_showdown_performance import _showdown_players


def salary(row):
    return _cpt_salary(row['Captain']) + sum(_salary(p) for p in row['Flex'])


def small_pool():
    captain = dict(Name='Captain', FlexID='c', Team='A', Position='RB',
                   FlexSalary=6600, CptSalary=10000, FlexProjection=10, CptProjection=15, LockCpt=True)
    flex = [dict(Name=f'F{i}', FlexID=f'f{i}', Team='B' if i % 2 else 'A', Position='RB',
                 FlexSalary=value, CptSalary=value * 1.5, FlexProjection=10 + i, CptProjection=(10 + i) * 1.5)
            for i, value in enumerate((1000, 6000, 7000, 8000, 9000, 10000))]
    return [captain] + flex


class ShowdownSalarySearchTests(unittest.TestCase):
    def test_fast_bounds_keep_every_feasible_combination_in_small_exhaustive_pool(self):
        players = small_pool(); before = deepcopy(players)
        expected = {showdown_signature(dict(Captain=players[0], Flex=list(flex)))
                    for flex in combinations(players[1:], 5)
                    if 44000 <= 10000 + sum(_salary(p) for p in flex) <= 50000}
        rows = ShowdownOptimizer(players, minimum_salary=44000, build_style='Randomized', seed=17)._build_lineups_fast(10)
        self.assertEqual({showdown_signature(row) for row in rows}, expected)
        self.assertEqual(len(rows), 3); self.assertEqual(players, before)

    def test_every_style_enforces_floor_and_cap_in_fast_and_exact_search(self):
        players = small_pool()
        for style in BUILD_STYLES:
            with self.subTest(style=style):
                for rows in (ShowdownOptimizer(players, minimum_salary=44000, build_style=style)._build_lineups_fast(2),
                             ShowdownOptimizer(players, minimum_salary=44000, build_style=style).build_lineups(1)):
                    self.assertTrue(rows)
                    self.assertTrue(all(44000 <= salary(row) <= 50000 for row in rows))
                    self.assertTrue(all(_pkey(row['Captain']) == 'c' for row in rows))
                    self.assertTrue(all(len({_pkey(p) for p in [row['Captain']] + row['Flex']}) == 6 for row in rows))
                    self.assertTrue(all(len({p['Team'] for p in [row['Captain']] + row['Flex']}) == 2 for row in rows))

    def test_impossible_salary_gap_does_not_relax_floor_in_either_path(self):
        for exact in (False, True):
            opt = ShowdownOptimizer(small_pool(), salary_cap=46000, minimum_salary=45500)
            rows = opt.build_lineups(1) if exact else opt._build_lineups_fast(2)
            self.assertEqual(rows, [])

    def test_locks_fades_exclusions_and_exposure_caps_remain_hard(self):
        players = _showdown_players(); players[6]['LockCpt'] = True
        players[8]['LockFlex'] = True; players[10]['FadeFlex'] = True
        players[12]['MaxPct'] = 0
        before = deepcopy(players)
        initial = ShowdownOptimizer(players, minimum_salary=49500, build_style='Balanced', seed=5)._build_lineups_fast(4)
        self.assertTrue(initial)
        excluded = {(_pkey(r['Captain']), tuple(sorted(_pkey(p) for p in r['Flex']))) for r in initial}
        rows = ShowdownOptimizer(players, minimum_salary=49500, build_style='Balanced', seed=5)._build_lineups_fast(4, excluded_signatures=excluded)
        self.assertTrue(rows); self.assertEqual(players, before)
        for row in rows:
            flex = {_pkey(p) for p in row['Flex']}
            self.assertEqual(_pkey(row['Captain']), _pkey(players[6]))
            self.assertIn(_pkey(players[8]), flex)
            self.assertNotIn(_pkey(players[10]), flex); self.assertNotIn(_pkey(players[12]), flex)
            self.assertNotIn((_pkey(row['Captain']), tuple(sorted(flex))), excluded)
            self.assertEqual(filter_salary_candidates([row], 50000, 'Maximize Salary'), [row])

    def test_cancelled_search_returns_without_continuing_to_fill(self):
        opt = ShowdownOptimizer(small_pool(), minimum_salary=44000)
        self.assertEqual(opt._build_lineups_fast(10, cancel_callback=lambda: True), [])
        self.assertEqual(opt.build_lineups(1, cancel_callback=lambda: True), [])

    def test_mid_search_cancellation_is_honored(self):
        calls = []
        def cancelled():
            calls.append(True)
            return len(calls) >= 20
        rows = ShowdownOptimizer(_showdown_players(), minimum_salary=49500)._build_lineups_fast(1000, cancel_callback=cancelled)
        self.assertLess(len(rows), 1000)
        self.assertLessEqual(len(calls), 24)
        self.assertTrue(all(49500 <= salary(row) <= 50000 for row in rows))

    def test_report_distinguishes_fresh_search_from_legacy_and_library_receipts(self):
        from build_diagnostics import create_build_diagnostic, format_build_report
        context = dict(kind='showdown', sport='NFL', settings={'compute_mode': 'Deep'})
        for generation_minimum in (None, 49500):
            receipt = dict(excluded=0, minimum=49500)
            if generation_minimum is not None: receipt['generation_minimum'] = generation_minimum
            report = create_build_diagnostic(context=context, timing_report={},
                portfolio_report={}, sim_report=dict(deep_build=dict(compute_mode='Deep'), salary_filter=receipt))
            text = format_build_report(report)
            self.assertEqual('Fresh Showdown search enforced' in text, generation_minimum is not None)

    def test_invalid_floor_is_rejected(self):
        for value in (-1, 50001, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ShowdownOptimizer(small_pool(), minimum_salary=value)

    def test_captain_seeds_obey_floor_and_keep_original_player_objects(self):
        from captain_coverage import seed_captains
        players = small_pool(); before = deepcopy(players); bank = {}
        count = seed_captains(players, players[:1], bank, set(), 120, salary_cap=50000,
            own_mode='Balanced', own_weight=.15, minimum_salary=44000,
            deadline=time.perf_counter() + 5, cancelled=lambda: False)
        self.assertGreater(count, 0); self.assertEqual(players, before)
        self.assertTrue(all(44000 <= salary(row) <= 50000 for row in bank.values()))
        self.assertTrue(all(row['Captain'] is players[0] for row in bank.values()))

    def test_deep_passes_same_strategy_floor_to_all_styles_and_captain_coverage(self):
        from main_window import LineupBuildWorker, _deep_shortlist
        from showdown_simulation import run_deep_showdown
        class EndGeneration(Exception): pass
        seen = []; seeds = []
        class Fake:
            def __init__(self, players, **kwargs): seen.append(kwargs)
            def build_lineups(self, **kwargs): return []
        worker = LineupBuildWorker(_showdown_players(), kind='showdown', num_lineups=8, salary_cap=50000,
            salary_strategy='Maximize Salary', compute_mode='Deep', sim_enabled=True,
            deep_options=dict(candidates=160, seeds=1, all_styles=True))
        with patch('showdown_simulation.ShowdownOptimizer', Fake), \
                patch('captain_coverage.seed_captains', side_effect=lambda *args, **kwargs: seeds.append(kwargs) or 0), \
                patch('showdown_coverage.expand_capped_candidates', return_value=[]), \
                patch('screening_audit.ScreeningAudit', side_effect=EndGeneration):
            with self.assertRaises(EndGeneration): run_deep_showdown(worker, _deep_shortlist)
        self.assertEqual({row['build_style'] for row in seen}, set(BUILD_STYLES))
        self.assertTrue(all(row['minimum_salary'] == 49500 for row in seen))
        self.assertEqual(seeds[0]['minimum_salary'], 49500)

    def test_exposure_coverage_uses_same_floor_and_retains_final_filter(self):
        from candidate_recovery import expand_candidates
        from test_portfolio_recovery import bank
        rows = bank(total=10)
        players = list({_pkey(p): p for row in rows for p in [row['Captain']] + row['Flex']}.values())
        seen = []
        class Fake:
            def __init__(self, players, **kwargs): seen.append(kwargs)
            def _build_lineups_fast(self, **kwargs): return bank()
        with patch('optimizers.ShowdownOptimizer', Fake):
            extra = expand_candidates(rows, players, 10, {}, kind='showdown', salary_cap=50000,
                salary_strategy='Maximize Salary', own_mode='Balanced', own_weight=.15,
                build_style='Strategic', max_additions=2)
        self.assertTrue(seen); self.assertTrue(all(row['minimum_salary'] == 49500 for row in seen))
        self.assertEqual(extra, [])


if __name__ == '__main__':
    unittest.main()
