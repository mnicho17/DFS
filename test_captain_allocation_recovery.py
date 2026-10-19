from test_environment import install
install()
from collections import Counter
from copy import deepcopy
import unittest
from unittest.mock import patch
from captain_pool import prepare_captain_pool, allocation_targets
from portfolio_rules import select_portfolio, normalize_rules, player_key
from portfolio_recovery import aggregate_recovery
from selection_shortage import PortfolioSelectionShortage
from test_portfolio_recovery import player


def fixture(counts=(8, 2), common=False):
    captains = [player(f'c{i}', LockCpt=True) for i in range(len(counts))]
    rows = []
    for captain, count in zip(captains, counts):
        for _ in range(count):
            i = len(rows)
            flex = [player(f'f{i}-{j}') for j in range(5)]
            if common: flex[0] = player('common')
            rows.append(dict(Captain=captain, Flex=flex))
    players = list({player_key(p): p for row in rows for p in [row['Captain']] + row['Flex']}.values())
    output, rules, _ = prepare_captain_pool(players, {'min_unique': 2}, len(rows))
    return rows, players, output, rules


def choose(rows, rules, **kwargs):
    return select_portfolio(rows, len(rows), kind='showdown', rules=rules,
                            automatic_recovery=True, allow_relaxation=False, **kwargs)


class CaptainAllocationRecoveryTests(unittest.TestCase):
    def test_generated_shares_recover_without_mutating_inputs(self):
        rows, players, output, rules = fixture()
        before = deepcopy((rows, players, output, rules))
        result = choose(rows, rules, refinement_passes=2)
        self.assertEqual((rows, players, output, rules), before)
        self.assertEqual(Counter(player_key(r['Captain']) for r in result['lineups']), {'c0': 8, 'c1': 2})
        diag = result['report']['portfolio_recovery']
        self.assertEqual(diag['recovery_stage'], 'required')
        self.assertEqual(diag['captain_pool_allocation']['c0'], {'target': 5, 'selected': 8})
        self.assertIn('Automatic Captain allocation c0: 5 -> 8', result['report']['text'])
        self.assertFalse(any('exceeds its maximum' in w or 'minimum is' in w for w in result['report']['warnings']))
        self.assertEqual(result['report']['effective_min_unique'], 2)

    def test_150_lineups_six_locked_captains_and_automatic_total_cap(self):
        rows, _, _, rules = fixture((30, 30, 25, 25, 25, 15), common=True)
        result = choose(rows, rules)
        self.assertEqual(len(result['lineups']), 150)
        diag = result['report']['portfolio_recovery']
        self.assertEqual(diag['starting_automatic_caps']['total']['common'], 112)
        self.assertEqual(diag['effective_automatic_caps']['total']['common'], 150)
        self.assertEqual(set(diag['starting_automatic_caps']['captain'].values()), {25})
        self.assertEqual({r['target'] for r in diag['captain_pool_allocation'].values()}, {25})

    def test_balanced_success_does_not_redistribute(self):
        rows, _, _, rules = fixture((5, 5))
        with patch('portfolio_recovery.recover', side_effect=AssertionError('not needed')):
            result = choose(rows, rules)
        self.assertFalse(result['report']['portfolio_recovery']['automatic_recovery_ran'])

    def test_manual_caps_without_generation_receipt_stay_hard(self):
        rows, _, _, rules = fixture()
        rules.pop('captain_pool_allocation')
        with self.assertRaises(PortfolioSelectionShortage) as caught:
            choose(rows, rules)
        self.assertIn('Captain cap 5/10 (explicit)', str(caught.exception))

    def test_changed_cap_or_request_does_not_inherit_automatic_permission(self):
        _, _, _, rules = fixture()
        self.assertEqual(allocation_targets(normalize_rules(rules), 10), {'c0': 5, 'c1': 5})
        for change in ('cap', 'minimum', 'lock', 'count', 'bad target'):
            current = deepcopy(rules)
            if change == 'cap': current['player_constraints']['c0']['MaxCptPct'] = 60
            elif change == 'minimum': current['player_constraints']['c0']['MinCptPct'] = 10
            elif change == 'lock': current['player_constraints']['c0']['LockCpt'] = False
            elif change == 'count': current['captain_pool_allocation']['requested'] = 11
            else: current['captain_pool_allocation']['targets']['c0'] = '5'
            self.assertEqual(allocation_targets(normalize_rules(current), 10), {}, change)

    def test_missing_selected_captain_does_not_release_portfolio(self):
        rows, _, _, rules = fixture((10, 0))
        # The selected pool must include the Captain with no available rows.
        empty = player('c1', LockCpt=True)
        _, rules, _ = prepare_captain_pool([rows[0]['Captain'], empty], {'min_unique': 2}, 10)
        with self.assertRaises(PortfolioSelectionShortage) as caught:
            choose(rows, rules)
        diag = caught.exception.recovery_diagnostics
        self.assertEqual(diag['starting_automatic_caps'], diag['effective_automatic_caps'])
        self.assertNotIn('captain_pool_allocation', diag)

    def test_uniqueness_and_explicit_group_constraints_remain_hard(self):
        rows, _, _, rules = fixture()
        for constraint in ('uniqueness', 'group'):
            current = deepcopy(rules); candidates = deepcopy(rows)
            if constraint == 'uniqueness':
                candidates[1]['Flex'] = candidates[0]['Flex']
            else:
                current['groups'] = [dict(type='at_least_one', player_keys=['absent'])]
            with self.assertRaises(PortfolioSelectionShortage):
                choose(candidates, current)

    def test_unrelated_manual_total_cap_is_not_relaxed(self):
        rows, _, _, rules = fixture(common=True)
        rules['player_constraints']['common'] = dict(MaxPct=50)
        with self.assertRaises(PortfolioSelectionShortage) as caught:
            choose(rows, rules)
        diag = caught.exception.recovery_diagnostics
        self.assertNotIn('common', diag['starting_automatic_caps']['total'])
        self.assertNotIn('captain_pool_allocation', diag)

    def test_redistribution_also_works_without_ownership_balancing(self):
        rows, _, _, rules = fixture()
        rules['balance_ownership'] = False
        result = choose(rows, rules)
        self.assertEqual(len(result['lineups']), 10)
        self.assertEqual(result['report']['portfolio_recovery']['recovery_stage'], 'required')

    def test_generated_recovery_reserves_time_for_later_stages(self):
        from portfolio_feasibility import repair
        rows, _, _, rules = fixture()
        budgets = []
        def record(*args, **kwargs):
            budgets.append(kwargs['seconds'])
            return repair(*args, **kwargs)
        with patch('portfolio_feasibility.repair', side_effect=record):
            choose(rows, rules)
        self.assertEqual(len(budgets), 4)
        self.assertLessEqual(budgets[1], 5)
        self.assertLessEqual(budgets[2], 7.5)
        self.assertTrue(all(budget > 0 for budget in budgets))

    def test_retained_rows_survive_redistribution(self):
        rows, players, _, _ = fixture()
        retained = rows[:2]
        _, rules, _ = prepare_captain_pool(players, {'min_unique': 2}, 10, retained)
        result = choose(rows, rules, retained_lineups=retained)
        self.assertTrue({id(r) for r in retained} <= {id(r) for r in result['lineups']})

    def test_cancellation_does_not_release_recovered_rows(self):
        rows, _, _, rules = fixture()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            choose(rows, rules, selection_cancel_callback=lambda: True)

    def test_aggregate_diagnostics_do_not_disclose_captain_keys(self):
        rows, _, _, rules = fixture()
        diag = choose(rows, rules)['report']['portfolio_recovery']
        aggregate = aggregate_recovery(diag)
        self.assertNotIn('c0', str(aggregate)); self.assertNotIn('c1', str(aggregate))
        self.assertEqual(sorted(r['selected'] for r in aggregate['captain_pool_allocation'].values()), [2, 8])

    def test_saved_validation_recomputes_pool_permission_without_trusting_report(self):
        from saved_repair import BuildReceipt, validate_proposal, signatures
        rows, players, _, rules = fixture()
        receipt = BuildReceipt(kind='showdown', sport='NFL', requested=10,
            context=dict(portfolio_rules=normalize_rules({'min_unique': 2}), salary_cap=50000))
        receipt.retained = Counter(signatures(rows[:2], 'showdown'))
        payload = dict(requested=10, lineups=rows, portfolio_report={})
        self.assertEqual(validate_proposal(receipt, payload, players), '')
        bad = deepcopy(payload); bad['lineups'][0]['Captain'] = player('outsider')
        bad['portfolio_report'] = {'captain_pool_allocation': {'outsider': {'selected': 10}}}
        self.assertNotEqual(validate_proposal(receipt, bad, players), '')
        self.assertEqual(validate_proposal(receipt, dict(payload, lineups=rows[2:]), players), 'incomplete')
        faded = deepcopy(players); faded[-1]['FadeFlex'] = True
        self.assertEqual(validate_proposal(receipt, payload, faded), 'validation_failed')


if __name__ == '__main__':
    unittest.main()
