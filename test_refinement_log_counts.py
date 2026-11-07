"""Exact refinement receipts captured from merged main 157f11b."""
from test_environment import install
install()
import copy
import unittest
import test_portfolio_value_total as reference
from portfolio_rules import select_portfolio

BASELINE = {
    'overlap': '5df7b2f59ddc5ec5769d8999506f8b70cef34e76101e6e3558936f58cf977b68',
    'empty': '953c4904c08372f425942dfaff024c6e6e9fdd826d59478a185ac3e4e327863b',
    'retained': '670caed7c676db277a87a14bf02c7f69edfa5f8cf7d95655e9cd7fe20edbb785',
}

class RefinementLogCountsTests(unittest.TestCase):
    def check_receipt(self, variant):
        rows, options = reference.case('refined')
        for n, row in enumerate(rows):
            row.sim_top_hits = set(range(n % 4, n % 4 + 5)) if variant != 'empty' else set()
            row.sim_top_five_hits = {0, 2, 4, n + 10} if variant != 'empty' else set()
            row.sim_win_hits = {n % 3} if variant != 'empty' else set()
        if variant == 'retained':
            options['retained_lineups'] = [options['fallback_lineups'][-1]]
        original = copy.deepcopy(rows)
        result = select_portfolio(rows, 8, **options)
        self.assertEqual(reference.digest(result), BASELINE[variant])
        self.assertTrue(result['report']['compliant'])
        self.assertEqual(result['report']['refinement_swaps'], 5 if variant == 'retained' else 6)
        self.assertEqual(rows, original)
        for field in ('sim_top_hits', 'sim_top_five_hits', 'sim_win_hits', 'sim_scenario_values', 'sim_metrics'):
            self.assertEqual([getattr(r, field) for r in rows], [getattr(r, field) for r in original])

    def test_classic_refinement_keeps_exact_receipt(self):
        rows = reference.classic_case()
        before = copy.deepcopy(rows)
        result = select_portfolio(rows, 4, kind='classic', rules={'min_unique': 1},
                                  allow_relaxation=False, refinement_passes=8)
        self.assertEqual(reference.digest(result),
                         '79b9ad775d4a5322fb6e7596ff8b4f54348ae27312114c8f623d5cadfe39dac7')
        self.assertEqual(result['report']['refinement_swaps'], 1)
        self.assertEqual(rows, before)
        self.assertEqual([r.sim_scenario_values for r in rows], [r.sim_scenario_values for r in before])

    def test_overlapping_and_new_scenario_hits_keep_exact_receipt(self):
        self.check_receipt('overlap')

    def test_empty_hit_sets_keep_exact_receipt(self):
        self.check_receipt('empty')

    def test_retained_entries_keep_exact_receipt(self):
        self.check_receipt('retained')

if __name__ == '__main__':
    unittest.main()
