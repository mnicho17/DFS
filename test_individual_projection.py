import copy
import json
import tempfile
import unittest
from pathlib import Path
import test_projection_sensitivity as fixtures
from projection_sensitivity import run_projection, save_projection, format_projection, OutcomeStress, PROFILES
from ownership_review import load_review


class IndividualProjectionTests(unittest.TestCase):
    def test_single_target_and_shared_captain_outcome(self):
        stress=OutcomeStress('Individual', [{'key':'a'}], 1)
        self.assertEqual(stress({'a':20.,'b':10.}), {'a':17.,'b':10.})
        from showdown_simulation import showdown_score
        from optimizers import ShowdownLineup
        self.assertEqual(showdown_score(ShowdownLineup({'FlexID':'a'},[{'FlexID':'b'}]),stress({'a':20.,'b':10.})),35.5)

    def test_both_real_simulators_dynamic_review_and_unchanged_bank(self):
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as folder:
                path,_,_=fixtures.ProjectionSensitivityTests().bank(folder,kind); original=path.read_bytes()
                report=run_projection(path,batches=1,scenarios=1000)
                self.assertEqual(len(report['profiles']),8)
                proof=report['evidence'][0]
                self.assertEqual(len({p['base_outcome_id'] for p in proof.values()}),1)
                self.assertEqual(len({p['opponent_field_id'] for p in proof.values()}),1)
                for name in report['profiles'][3:]:
                    self.assertEqual(len(report['targets'][name]),1)
                    for means in proof[name]['player_means'].values():self.assertAlmostEqual(means['stressed'],means['baseline']*.85)
                dest=Path(folder)/'new.json';save_projection(dest,report)
                self.assertEqual(len(load_review(dest,folder,comparison='projection')['rows']),3)
                self.assertIn('Individual-player dependencies',format_projection(report))
                self.assertEqual(path.read_bytes(),original)
                broken=copy.deepcopy(report);broken['rows']=broken['rows'][:-1];save_projection(dest,broken)
                with self.assertRaises(ValueError):load_review(dest,folder,comparison='projection')
                broken=copy.deepcopy(report);broken['profiles'][-1]='Unknown';dest.write_text(json.dumps(broken),encoding='utf-8')
                with self.assertRaises(ValueError):load_review(dest,folder,comparison='projection')

    def test_incomplete_individual_profile_discards_whole_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            path,_,_=fixtures.ProjectionSensitivityTests().bank(folder,'classic');calls=[]
            def simulate(rows,players,**kw):
                calls.append(1)
                for _ in range(1000):kw['outcome_transform']({'a':20.})
                return dict(lineups=rows,report=dict(scenarios=999 if len(calls)==8 else 1000,sensitivity_field_id='same'))
            report=run_projection(path,batches=1,scenarios=1000,simulate=simulate)
            self.assertEqual(len(calls),8)
            self.assertEqual(report['completed_batches'],0)
            self.assertEqual(report['status'],'incomplete')
