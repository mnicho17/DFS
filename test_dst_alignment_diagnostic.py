"""Exact-marginal alignment controls; no production model or RNG changes."""
from test_environment import install
install()
import copy
import random
import statistics
import unittest
from scripts.dst_alignment_diagnostic import block_indices, compare_alignment, THRESHOLDS


class DstAlignmentTests(unittest.TestCase):
    def run_pair(self, qb=None, dst=None, **kwargs):
        return compare_alignment(qb or [0.,10.,20.,30.],dst or [12.,8.,4.,0.],10.,8.,seeds=(90210,),block_size=4,**kwargs)

    def test_exact_marginals_no_input_or_rng_mutation(self):
        qb=[0.,10.,20.,30.];dst=[12.,8.,4.,0.];before=copy.deepcopy((qb,dst));state=random.getstate()
        result=self.run_pair(qb,dst)
        self.assertEqual((qb,dst),before);self.assertEqual(random.getstate(),state)
        self.assertTrue(result['marginal_exact']);self.assertEqual(result,self.run_pair(qb,dst))
        self.assertEqual(result['dst_marginal']['mean'],6.)
        self.assertAlmostEqual(result['original']['correlation'],-1.)
        self.assertNotEqual(result['original']['correlation'],result['shifted']['correlation'])

    def test_fixed_event_boundary_oracle(self):
        result=self.run_pair()['original'];events=result['events']
        self.assertEqual(events['low_qb_high_dst'],dict(probability=.5,observed=True,brier=.25))
        self.assertEqual(events['high_qb_high_dst'],dict(probability=0.,observed=False,brier=0.))
        self.assertEqual(events['high_qb_low_dst'],dict(probability=.5,observed=False,brier=.25))
        self.assertAlmostEqual(result['mean_event_brier'],1/6)
        self.assertEqual(THRESHOLDS,dict(low_qb=10.,high_qb=20.,low_dst=4.,high_dst=8.))

    def test_block_permutations_preserve_each_seed_and_joint_dst_blocks(self):
        indices,offsets=block_indices((90210,481516,730001),4)
        left=list(range(12));right=[v*v for v in left]
        for block,offset in enumerate(offsets):
            part=indices[block*4:block*4+4]
            self.assertEqual(sorted(part),list(range(block*4,block*4+4)))
            self.assertTrue(all(i!=j for i,j in zip(part,range(block*4,block*4+4))))
            self.assertTrue(1<=offset<4)
        self.assertEqual(sorted(zip(left,right)),sorted((left[i],right[i]) for i in indices))
        result=compare_alignment(left,right,1.,2.,seeds=(90210,481516,730001),block_size=4)
        self.assertEqual(len(result['per_seed']),3)
        self.assertEqual(result['offsets'],offsets)

    def test_actuals_never_choose_offsets(self):
        a=compare_alignment([0.,10.,20.,30.],[12.,8.,4.,0.],10.,8.,seeds=(1,),block_size=4)
        b=compare_alignment([0.,10.,20.,30.],[12.,8.,4.,0.],-3.,-4.,seeds=(1,),block_size=4)
        self.assertEqual(a['offsets'],b['offsets']);self.assertEqual(a['original']['correlation'],b['original']['correlation'])
        self.assertNotEqual(a['original']['events'],b['original']['events'])

    def test_constant_series_correlation_is_unavailable(self):
        result=self.run_pair([1.]*4,[2.]*4)
        self.assertIsNone(result['original']['correlation']);self.assertIsNone(result['shifted']['correlation'])
        self.assertEqual(result['original']['covariance'],0.)

    def test_crps_matches_independent_pairwise_oracle(self):
        dst=[-4.,0.,8.,8.];actual=8.
        result=self.run_pair(dst=dst)['dst_marginal']
        expected=sum(abs(v-actual) for v in dst)/4-sum(abs(x-y) for x in dst for y in dst)/(2*16)
        self.assertEqual(result['crps'],expected)

    def test_exact_draw_budget_and_one_over(self):
        values=list(range(6000));result=compare_alignment(values,values,0.,0.,seeds=(1,2,3),block_size=2000)
        self.assertEqual(result['draws'],6000)
        with self.assertRaises(ValueError):block_indices((1,),6001)

    def test_partial_mismatched_and_empty_samples_rejected(self):
        for qb,dst in (([],[]),([1.]*3,[1.]*4),([1.]*4,[1.]*3)):
            with self.subTest(lengths=(len(qb),len(dst))),self.assertRaises(ValueError):
                compare_alignment(qb,dst,0.,0.,seeds=(1,),block_size=4)

    def test_bad_seed_and_block_configuration_rejected(self):
        for seeds,size in (((),4),((1,1),4),((True,),4),((-1,),4),((2**63,),4),((1.5,),4),((1,2,3,4),4),((1,),1),((1,),True),((1,),2.5)):
            with self.subTest(seeds=seeds,size=size),self.assertRaises(ValueError):block_indices(seeds,size)

    def test_unknown_or_nonfinite_actuals_rejected(self):
        for value in (None,True,'0',float('nan'),float('inf')):
            for which in (0,1):
                actuals=[0.,0.];actuals[which]=value
                with self.subTest(value=value,which=which),self.assertRaises(ValueError):
                    compare_alignment([0.]*4,[0.]*4,*actuals,seeds=(1,),block_size=4)

    def test_bad_model_draws_rejected(self):
        for value in (None,True,'0',float('nan'),-float('inf')):
            for which in (0,1):
                series=[[0.]*4,[0.]*4];series[which][2]=value
                with self.subTest(value=value,which=which),self.assertRaises(ValueError):
                    compare_alignment(*series,0.,0.,seeds=(1,),block_size=4)

    def test_cancel_before_during_and_before_return_has_no_result(self):
        calls=[]
        def count():calls.append(1);return False
        self.run_pair(cancelled=count);total=len(calls)
        for stop in (1,3,total):
            current=[]
            def cancelled():current.append(1);return len(current)>=stop
            with self.subTest(stop=stop),self.assertRaises(InterruptedError):self.run_pair(cancelled=cancelled)

    def test_cli_requires_reconciled_study_before_creating_outputs(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'not-created'/'report'
            result=subprocess.run([sys.executable,str(Path(__file__).resolve().parent/'scripts'/'audit_rotation_zero_prototype.py'),
                '--db',str(Path(folder)/'missing.sqlite'),'--output-prefix',str(output),'--completed-before','2026-11-07',
                '--dst-alignment-study'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,2)
            self.assertIn('requires --reconciled-study',result.stderr)
            self.assertFalse(output.parent.exists())


if __name__=='__main__':unittest.main()
