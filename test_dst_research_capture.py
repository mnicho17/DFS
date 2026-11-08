"""Research-only capture/sampler contracts, using synthetic identities and hashes."""
from test_environment import install
install()
import copy
import hashlib
import json
import random
import unittest
from scripts.dst_research_capture import capture_inputs, verify_capture, sample_capture, PLAN


def fixture():
    return dict(game_date='2026-11-08',game_key='A@B',kickoff='2026-11-08T18:00:00Z',
                captured_at='2026-11-08T15:00:00Z',
                defenses=[dict(key='A-DST',team='A',opponent='B',forecast=5.),
                          dict(key='B-DST',team='B',opponent='A',forecast=7.)],
                source_hashes=dict(salary='a'*64,frozen_inputs='b'*64),
                model_hash='c'*64,pregame_evidence_hash='d'*64)


class DstResearchCaptureTests(unittest.TestCase):
    def test_deterministic_detached_order_invariant_capture(self):
        inputs=fixture();before=copy.deepcopy(inputs)
        result=capture_inputs(**inputs)
        self.assertEqual(inputs,before)
        inputs['defenses'].reverse()
        self.assertEqual(result,capture_inputs(**inputs))
        inputs['defenses'][0]['forecast']=99
        self.assertEqual(result['payload']['defenses'][1]['forecast'],7.)
        self.assertEqual(verify_capture(result),result)
        self.assertEqual(result['payload']['status'],'unqualified_capture')

    def test_tampering_and_false_qualification_rejected(self):
        for field,value in [('game_key','X'),('status','qualified')]:
            result=capture_inputs(**fixture());result['payload'][field]=value
            with self.assertRaises(ValueError):verify_capture(result)
        result=capture_inputs(**fixture());result['sha256']='0'*64
        with self.assertRaises(ValueError):verify_capture(result)

    def test_duplicate_or_conflicting_identity_in_either_order(self):
        for change in (dict(key='A-DST'),dict(team='A'),dict(opponent='X')):
            for reverse in (False,True):
                inputs=fixture();inputs['defenses'][1].update(change)
                if reverse:inputs['defenses'].reverse()
                with self.assertRaises(ValueError):capture_inputs(**inputs)

    def test_missing_extra_partial_or_actual_fields_rejected(self):
        for change in ('missing','actual','empty','one'):
            inputs=fixture()
            if change=='missing':del inputs['defenses'][0]['key']
            if change=='actual':inputs['defenses'][0]['actual']=0
            if change=='empty':inputs['defenses'][0]['key']=' '
            if change=='one':inputs['defenses'].pop()
            with self.assertRaises(ValueError):capture_inputs(**inputs)

    def test_time_window_and_timezone_checks(self):
        for field,value in [('captured_at','2026-11-08T18:00:00Z'),
                            ('captured_at','2026-11-07T00:00:00Z'),
                            ('captured_at','2026-11-08T15:00:00'),
                            ('kickoff','2026-11-09T18:00:00Z'),
                            ('game_date','2026-11-07'),('game_date','2026-12-08')]:
            inputs=fixture();inputs[field]=value
            with self.assertRaises(ValueError):capture_inputs(**inputs)

    def test_bad_hashes_and_forecasts(self):
        for forecast in (None,True,float('nan'),float('inf'),-5,101):
            inputs=fixture();inputs['defenses'][0]['forecast']=forecast
            with self.assertRaises(ValueError):capture_inputs(**inputs)
        inputs=fixture();inputs['source_hashes']['salary']='bad'
        with self.assertRaises(ValueError):capture_inputs(**inputs)

    def test_sampler_reproducible_bounded_and_no_input_or_rng_mutation(self):
        receipt=capture_inputs(**fixture());before=copy.deepcopy(receipt)
        parent=random.Random(123);parent_state=parent.getstate();global_state=random.getstate()
        result=sample_capture(receipt)
        self.assertEqual(result,sample_capture(receipt))
        self.assertEqual(receipt,before);self.assertEqual(random.getstate(),global_state)
        self.assertEqual(parent.getstate(),parent_state)
        for defense in result['defenses']:
            for values in defense['samples'].values():
                self.assertEqual(len(values),6000);self.assertGreaterEqual(min(values),-4.)
        self.assertEqual(result['status'],'unqualified_samples')

    def test_independent_hash_seed_oracle_and_common_normals(self):
        result=sample_capture(capture_inputs(**fixture()))
        normals=[]
        for defense in result['defenses']:
            material=[PLAN,'2026-11-08','A@B',defense['key'],90210]
            digest=hashlib.sha256(json.dumps(material,separators=(',',':')).encode()).digest()
            oracle=random.Random(int.from_bytes(digest,'big')).gauss(0,1)
            normals.append(oracle)
            for factor,spec in defense['specifications'].items():
                self.assertEqual(defense['samples'][factor][0],max(-4.,spec['location']+spec['latent_spread']*oracle))
        self.assertNotEqual(normals[0],normals[1])

    def test_floor_forecast_degenerate_draws(self):
        inputs=fixture();inputs['defenses'][0]['forecast']=-4
        result=sample_capture(capture_inputs(**inputs))
        for values in result['defenses'][0]['samples'].values():self.assertEqual(set(values),{-4.})

    def test_cancel_capture_and_mid_sampling_withholds_result(self):
        with self.assertRaises(InterruptedError):capture_inputs(**fixture(),cancelled=lambda:True)
        receipt=capture_inputs(**fixture());count=0
        def cancelled():
            nonlocal count
            count+=1
            return count>=1500
        with self.assertRaises(InterruptedError):sample_capture(receipt,cancelled=cancelled)

    def test_cancel_final_sample_return(self):
        receipt=capture_inputs(**fixture());count=0
        def count_only():
            nonlocal count
            count+=1
            return False
        sample_capture(receipt,cancelled=count_only)
        total=count;count=0
        def cancelled():
            nonlocal count
            count+=1
            return count==total
        with self.assertRaises(InterruptedError):sample_capture(receipt,cancelled=cancelled)
