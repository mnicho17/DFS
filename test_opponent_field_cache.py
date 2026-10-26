"""Session field reuse preserves ordered inputs and fails closed on changes."""
from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
import opponent_field_cache as cache
from showdown_simulation import generate_showdown_field
from test_showdown_performance import _showdown_players


def receipt(rows):
    return ([dict(row) for row in rows], vars(rows))


class OpponentFieldCacheTests(unittest.TestCase):
    def setUp(self):
        cache.clear()
        self.players = _showdown_players()

    def field(self, **kwargs):
        return generate_showdown_field(self.players, 40, seed=18, reuse_field=True, **kwargs)

    def test_exact_order_diagnostics_and_mutation_isolation(self):
        expected = generate_showdown_field(self.players, 40, seed=18)
        a = self.field()
        self.assertEqual(receipt(a), receipt(expected))
        with patch('showdown_field.sample_field', side_effect=AssertionError('resampled')):
            b = self.field()
        self.assertEqual(receipt(b), receipt(expected))
        a[0]['Captain']['Name'] = 'changed'
        b.diagnostic.clear()
        self.assertEqual(receipt(self.field()), receipt(expected))

    def test_every_player_field_order_and_settings_are_keyed(self):
        self.field()
        from showdown_field import sample_field
        for change in ('ownership', 'order', 'extra'):
            saved = copy.deepcopy(self.players)
            if change == 'ownership': self.players[0]['OwnCpt'] = 99
            elif change == 'order': self.players.reverse()
            else: self.players[0]['FutureModelInput'] = 123
            with patch('showdown_field.sample_field', wraps=sample_field) as sampler:
                self.field()
                self.assertEqual(sampler.call_count, 1)
            self.players = saved
        for kwargs in ({'seed':19}, {'salary_cap':49000}):
            with patch('showdown_field.sample_field', wraps=sample_field) as sampler:
                generate_showdown_field(self.players,40,reuse_field=True,**kwargs)
                self.assertEqual(sampler.call_count,1)

    def test_cancelled_and_partial_work_never_promoted(self):
        self.assertEqual(len(self.field(cancel_callback=lambda:True)),0)
        self.assertEqual(len(cache._fields),0)
        self.field()
        self.assertEqual(len(self.field(cancel_callback=lambda:True)),0)

    def test_direct_and_audit_default_remain_fresh(self):
        self.field()
        from showdown_field import sample_field
        with patch('showdown_field.sample_field', wraps=sample_field) as sampler:
            generate_showdown_field(self.players,40,seed=18)
            self.assertEqual(sampler.call_count,1)

    def test_bounded_storage_and_ambiguous_inputs(self):
        with patch.object(cache,'_MAX_ROSTERS',60):
            self.field()
            generate_showdown_field(self.players,40,seed=19,reuse_field=True)
            self.assertEqual(sum(map(len,cache._fields.values())),40)
        players=copy.deepcopy(self.players)
        players[0].pop('FlexID')
        self.assertIsNone(cache.identity(players,40,50000,18,'salary-bands-v1',None))
        self.assertIsNone(cache.identity(self.players+[self.players[0]],40,50000,18,'salary-bands-v1',None))

    def test_code_and_profile_changes_invalidate_keys(self):
        args=(self.players,40,50000,18,'salary-bands-v1')
        with patch('candidate_library.code_id',return_value='a'):
            a=cache.identity(*args,None)
            self.assertNotEqual(a,cache.identity(*args,{'changed':True}))
        with patch('candidate_library.code_id',return_value='b'):
            self.assertNotEqual(a,cache.identity(*args,None))

    def test_complete_simulation_has_identical_metrics_and_hits(self):
        from showdown_simulation import simulate_showdown
        from optimizers import ShowdownOptimizer
        from test_primary_showdown_scoring import exact,evidence
        from test_ranking_audit_efficiency import values
        rows=ShowdownOptimizer(self.players)._build_lineups_fast(6)
        def run():
            return simulate_showdown(copy.deepcopy(rows),copy.deepcopy(self.players),
                scenarios=20,field_lineup_count=40,seed=90210,scenario_cache=True,
                capture_distributions=True)
        with patch('opponent_field_cache.get',return_value=None):
            expected=run()
        with patch('showdown_field.sample_field',side_effect=AssertionError('resampled')):
            actual=run()
        self.assertEqual(exact(values(expected)),exact(values(actual)))
        # Scenario cache receipts describe a save vs replay, not scored results.
        a,b=evidence(expected),evidence(actual)
        a.pop('scenario_cache',None);b.pop('scenario_cache',None)
        self.assertEqual(exact(a),exact(b))

    def test_history_validation_runs_before_hit(self):
        for p in self.players:
            p['GameInfo'] = 'ARI@CAR 09/24/2026 08:15PM ET'
        from test_showdown_history_field import profile
        from showdown_history_field import distributions
        from showdown_field import sample_field
        history=profile(distributions(sample_field(self.players,40,seed=18)))
        first=self.field(model='historical-blend-v1',history_profile=history)
        with patch('showdown_history_field.sample_field',side_effect=AssertionError('resampled')):
            self.assertEqual(receipt(first),receipt(self.field(model='historical-blend-v1',history_profile=history)))
        with patch('showdown_history_field.validate_profile',side_effect=ValueError('invalid history')):
            with self.assertRaisesRegex(ValueError,'invalid history'):
                self.field(model='historical-blend-v1',history_profile=history)

if __name__ == '__main__': unittest.main()
