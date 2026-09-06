import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtCore, QtWidgets
from lineup_ranking import BUILD_STYLES, finish_rank, ranked_lineups, search_jobs
from compute_settings import normalize_deep_settings, matching_deep_profile, DEEP_PROFILES
from main_window import MainWindow, LineupBuildWorker, _deep_shortlist
from nfl_simulation import SimLineup, simulate_nfl_contest
from optimizers import MultiSportClassicOptimizer, ShowdownOptimizer, ShowdownLineup
from portfolio_rules import select_portfolio
from showdown_simulation import simulate_showdown, showdown_signature
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


def metrics(rate):
    return dict(sim_scenarios=100, sim_top_one_pct=rate, sim_top_two_pct=rate + 1,
                sim_top_five_pct=rate + 2, sim_win_rate=rate / 2, sim_mean=100,
                sim_edge=100-rate, sim_return_index=100-rate)


class RankedSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_finish_order_is_not_edge_order_and_shortlist_uses_same_measure(self):
        rows = [SimLineup([dict(FlexID=str(i), Team="A", FlexProjection=10)], metrics=metrics(i)) for i in range(8)]
        ranked = ranked_lineups(rows)
        self.assertEqual([r.sim_metrics['sim_top_one_pct'] for r in ranked], list(range(7, -1, -1)))
        result = select_portfolio(rows, 3, individual_ranking=True, refinement_passes=100)
        self.assertEqual(result['lineups'], ranked[:3])
        self.assertEqual(result['report']['refinement_swaps'], 0)
        self.assertEqual(_deep_shortlist(rows, 3, individual_ranking=True), ranked[:3])
        rows[0].sim_metrics.update(sim_top_one_pct=7, sim_top_two_pct=99)
        self.assertIs(ranked_lineups(rows)[0], rows[0])

    def test_ranking_respects_explicit_max_and_retained_entries(self):
        a = dict(FlexID="a", Team="A", FlexProjection=10, MaxPct=50)
        rows = [SimLineup([a, dict(FlexID=str(i), Team="B")], metrics=metrics(90-i)) for i in range(2)]
        other = SimLineup([dict(FlexID="x", Team="A")], metrics=metrics(1))
        result = select_portfolio(rows + [other], 2, individual_ranking=True, retained_lineups=[rows[0]])
        self.assertEqual(result['lineups'], [rows[0], other])

    def test_individual_output_can_select_450_without_clipping_at_150(self):
        rows = [SimLineup([dict(FlexID=str(i), Team="A")], metrics=metrics(i/10)) for i in range(500)]
        result = select_portfolio(rows, 450, individual_ranking=True)
        self.assertEqual(len(result['lineups']), 450)
        self.assertEqual(result['lineups'], ranked_lineups(rows)[:450])

    def test_style_schedule_and_settings_preserve_tier(self):
        self.assertEqual([style for style, _ in search_jobs([1, 2], 'Chalk', True)], list(BUILD_STYLES) * 2)
        profile = next(iter(DEEP_PROFILES))
        options = normalize_deep_settings(dict(DEEP_PROFILES[profile], all_styles=True, selection_mode='Individual ranking'))
        self.assertEqual(matching_deep_profile(options, DEEP_PROFILES[profile]['scenarios']), profile)

    def test_both_workers_share_one_deduplicated_sim_pool_across_styles(self):
        for kind in ('classic', 'showdown'):
            with self.subTest(kind=kind):
                players = _fixture_players() if kind == 'classic' else _showdown_players()
                optimizer = MultiSportClassicOptimizer(players, sport='NFL') if kind == 'classic' else ShowdownOptimizer(players)
                bank = optimizer.build_lineups(20)
                self.assertGreaterEqual(len(bank), 5)
                styles, sim_calls = [], []
                def build(opt, **kwargs):
                    styles.append(opt.build_style)
                    return bank[BUILD_STYLES.index(opt.build_style):BUILD_STYLES.index(opt.build_style)+1]
                def sim(candidates, pool, **kwargs):
                    sim_calls.append((list(candidates), kwargs))
                    rows = []
                    for i, raw in enumerate(candidates):
                        lu = SimLineup(raw, metrics=metrics(i)) if kind == 'classic' else ShowdownLineup(raw['Captain'], raw['Flex'])
                        lu.sim_metrics = metrics(i)
                        rows.append(lu)
                    return dict(lineups=rows, report=dict(scenarios=kwargs['scenarios']))
                worker = LineupBuildWorker(players, kind=kind, num_lineups=3, salary_cap=50000,
                    sim_enabled=True, compute_mode='Deep', salary_strategy='Balanced',
                    deep_options=dict(all_styles=True, selection_mode='Individual ranking', candidates=100, shortlist=50),
                    portfolio_rules=dict(min_unique=1, balance_ownership=False))
                results, errors = [], []
                worker.finished.connect(results.append)
                worker.error.connect(errors.append)
                target = 'main_window.MultiSportClassicOptimizer.build_lineups' if kind == 'classic' else 'showdown_simulation.ShowdownOptimizer.build_lineups'
                sim_target = 'main_window.simulate_nfl_contest' if kind == 'classic' else 'showdown_simulation.simulate_showdown'
                with mock.patch(target, autospec=True, side_effect=build), mock.patch(sim_target, side_effect=sim), mock.patch('main_window.generate_nfl_field_lineups', return_value=([], [])), mock.patch('main_window.generate_nfl_scenario_lineups', return_value=([], {})):
                    worker.run()
                self.assertFalse(errors, errors)
                self.assertEqual(set(styles), set(BUILD_STYLES))
                self.assertEqual(len(sim_calls), 2)
                self.assertNotEqual(sim_calls[0][1]['seed'], sim_calls[1][1]['seed'])
                self.assertEqual(len(sim_calls[0][0]), 5)
                self.assertEqual(len(results[0]['lineups']), 3)
                self.assertEqual(results[0]['portfolio_report']['refinement_swaps'], 0)

    def test_sim_finish_bands_are_nested_for_both_contests(self):
        players = _fixture_players()
        rows = MultiSportClassicOptimizer(players, sport='NFL').build_lineups(3)
        classic = simulate_nfl_contest(rows, players, scenarios=12, field_lineup_count=30)['lineups']
        players = _showdown_players()
        rows = ShowdownOptimizer(players).build_lineups(3)
        showdown = simulate_showdown(rows, players, scenarios=12, field_lineup_count=30)['lineups']
        for lu in classic + showdown:
            m = lu.sim_metrics
            self.assertLessEqual(m['sim_win_rate'], m['sim_top_one_pct'])
            self.assertLessEqual(m['sim_top_one_pct'], m['sim_top_two_pct'])
            self.assertLessEqual(m['sim_top_two_pct'], m['sim_top_five_pct'])

    def test_450_results_page_in_order_and_save_correct_lineup_on_both_tabs(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QtCore.QSettings(folder + '/test.ini', QtCore.QSettings.IniFormat)
            with mock.patch('main_window.QtCore.QSettings', return_value=settings):
                window = MainWindow()
            try:
                classic_base = MultiSportClassicOptimizer(_fixture_players(), sport='NFL').build_lineups(1)[0]
                sd_base = ShowdownOptimizer(_showdown_players()).build_lineups(1)[0]
                for kind in ('classic', 'showdown'):
                    rows = []
                    for i in range(450):
                        if kind == 'classic':
                            raw = [dict(p) for p in classic_base]
                            raw[0]['FlexID'] = f'page-{i}'
                            lu = SimLineup(raw, metrics=metrics(i/5))
                        else:
                            lu = ShowdownLineup(dict(sd_base['Captain'], FlexID=f'page-{i}'), sd_base['Flex'])
                            lu.sim_metrics = metrics(i/5)
                        rows.append(lu)
                    if kind == 'classic':
                        window._populate_classic_lineups(rows, 'NFL')
                    else:
                        window._populate_showdown_lineups(rows)
                    table = window.tbl_cl if kind == 'classic' else window.tbl_sd
                    combo = getattr(window, '_' + kind + '_pages')[0]
                    self.assertEqual(table.rowCount(), 150)
                    self.assertEqual(combo.count(), 3)
                    self.assertEqual(table.verticalHeaderItem(0).text(), '1')
                    combo.setCurrentIndex(1)
                    self.assertEqual(table.verticalHeaderItem(0).text(), '151')
                    table.cellWidget(0, 0).setChecked(True)
                    saved = window.saved_classic if kind == 'classic' else window.saved_showdown
                    self.assertIs(saved[0], rows[299])
                    combo.setCurrentIndex(0)
                    table.cellWidget(0, 0).setChecked(True)
                    self.assertIs(saved[0], rows[449])
                    self.assertIs(saved[1], rows[299])
                    table.cellWidget(0, 0).setChecked(False)
                    combo.setCurrentIndex(2)
                    self.assertEqual(table.verticalHeaderItem(149).text(), '450')
                    combo.setCurrentIndex(1)
                    self.assertTrue(table.cellWidget(0, 0).isChecked())
                    table.cellWidget(0, 0).setChecked(False)
                    self.assertFalse(saved)
                self.assertEqual(window.spin_cl.maximum(), 1000)
                self.assertEqual(window.spin_sd.maximum(), 1000)
            finally:
                window.close()


if __name__ == '__main__':
    unittest.main()
