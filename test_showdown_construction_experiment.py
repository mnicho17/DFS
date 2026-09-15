import copy
import unittest
from unittest.mock import patch
from scripts.compare_showdown_construction import QBStress, generate_banks, summary
from showdown_simulation import active_showdown_players
from nfl_simulation import player_key
from test_showdown_performance import _showdown_players


class ConstructionExperimentTests(unittest.TestCase):
    def test_stresses_preserve_raw_draws_and_non_qbs(self):
        players=active_showdown_players(_showdown_players())
        raw={player_key(p):10. for p in players}
        transforms=[QBStress(players,p,77) for p in ('Baseline','QB mean -15%','QB wider outcomes')]
        original=copy.deepcopy(raw)
        outputs=[t(raw) for t in transforms]
        self.assertEqual(raw,original)
        self.assertEqual(len({t.digest.hexdigest() for t in transforms}),1)
        for p in players:
            key=player_key(p)
            if p['Position']=='QB':
                self.assertEqual(outputs[1][key],8.5)
                self.assertIn(outputs[2][key],(6.5,13.5))
            else:self.assertTrue(all(o[key]==10. for o in outputs))
        self.assertEqual(QBStress(players,'QB wider outcomes',77)(raw),outputs[2])

    def test_search_does_not_fade_a_locked_qb_or_mutate_inputs(self):
        players=active_showdown_players(_showdown_players())
        qb=next(p for p in players if p['Position']=='QB');qb['LockFlex']=True
        original=copy.deepcopy(players);calls=[]
        def fake_init(items,**kwargs):
            calls.append(copy.deepcopy(items))
            class Optimizer:
                def build_lineups(self,**kwargs):return []
            return Optimizer()
        with patch('scripts.compare_showdown_construction.ShowdownOptimizer',side_effect=fake_init):
            with self.assertRaisesRegex(ValueError,'Fewer than 150'):
                generate_banks(players,{},150,4,5)
        self.assertEqual(players,original)
        for items in calls:
            locked=next(p for p in items if player_key(p)==player_key(qb))
            self.assertTrue(locked['LockFlex'])
            self.assertFalse(locked.get('FadeFlex'))

    def test_captain_qb_counts_once(self):
        from optimizers import ShowdownLineup
        players=active_showdown_players(_showdown_players())
        qb=next(p for p in players if p['Position']=='QB')
        others=[p for p in players if p['Position']!='QB'][:5]
        self.assertEqual(summary([ShowdownLineup(qb,others)])['qb_counts'],{1:1})


if __name__=='__main__':unittest.main()
