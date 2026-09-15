import copy
import unittest
from optimizers import ShowdownLineup
from pipeline_audit import defense_mix, quarterback_mix, format_defense_pipeline


class DefensePipelineTests(unittest.TestCase):
    def test_captain_and_flex_defenses_count_once_each(self):
        lu=ShowdownLineup({'Position':'DST'},[{'Position':p} for p in ('D/ST','QB','WR','RB','TE')])
        lu.sim_metrics={'sim_top_one_pct':4.0}
        original=copy.deepcopy(lu)
        result=defense_mix([lu],scored=True)
        self.assertEqual(result['groups']['2'],{'count':1,'scored':1,'top1_sum':4.0})
        self.assertEqual(quarterback_mix([lu])['groups']['1']['count'],1)
        self.assertEqual(lu,original)
        text='\n'.join(format_defense_pipeline({'ranked':result,'selected':result}))
        self.assertIn('2 DST: 1/1 (100.0%)',text)
        self.assertIn('before portfolio rules',text)

    def test_missing_metadata_and_nonfinite_scores_are_not_zero_defenses(self):
        lu=ShowdownLineup({'Position':'DST'},[{}]*5)
        lu.sim_metrics={'sim_top_one_pct':float('nan')}
        result=defense_mix([lu],scored=True)
        self.assertEqual(result['groups']['unknown']['scored'],0)
        self.assertNotIn('0',result['groups'])
