"""Finite opportunities, conditional means and protected simulator behavior."""
from test_environment import install,network_attempts
install()
import copy,math,random,statistics,unittest
from test_rotation_zero_prototype import pool
from nfl_simulation import _scenario_outcomes,player_key
from nfl_workload import sample_workloads
from joint_opportunity_prototype import allocate,no_success,JointOpportunityPrototype


class JointOpportunityTests(unittest.TestCase):
    def test_every_event_is_assigned_once_or_reserved(self):
        players=pool();work=sample_workloads(random.Random(3),players);before=copy.deepcopy(work)
        for seed in range(20):
            counts,_,budgets=allocate(players,work,random.Random(seed))
            for b in budgets:
                self.assertEqual(b['total'],b['assigned']+b['reserve'])
                self.assertIn(b['total'],(math.floor(b['expected_budget']),math.ceil(b['expected_budget'])))
            for row in counts.values():
                self.assertLessEqual(row['catches'],row['targets'])
                self.assertTrue(all(isinstance(v,int) and v>=0 for v in row.values()))
        self.assertEqual(work,before)

    def test_counts_and_occupancy_preserve_means_and_expected_counts(self):
        a=next(p for p in pool() if p['Position']=='TE' and p['NFLDepthOrder']==2)
        b=copy.deepcopy(a);b['FlexID']='other';b['Name']='other'
        players=[a,b];work={id(p):dict(p['NFLWorkload'],carries=.2,targets=.5,
            budgets=dict(carries=1.5,targets=2.5),efficiency=[4.,.7,10.]) for p in players}
        expected_zero=no_success(1.5,.2)*no_success(2.5,.35)
        means={};allocations=[]
        for mode in ('counts','occupancy'):
            model=JointOpportunityPrototype(scoring=mode);rng=random.Random(241);values=[]
            for _ in range(12000):
                v=model.apply(players,work,{player_key(p):5. for p in players},rng)
                values.append(v[player_key(a)])
                if mode=='counts':allocations.append(model.last_allocation['players'][player_key(a)])
                rng.random()
            self.assertAlmostEqual(statistics.mean(values),5.,delta=.16)
            self.assertAlmostEqual(sum(x==0 for x in values)/len(values),expected_zero,delta=.02)
            means[mode]=values
        for metric,expected in [('carries',.2),('targets',.5),('catches',.35)]:
            self.assertAlmostEqual(statistics.mean(x[metric] for x in allocations),expected,delta=.025)
        # The same count process drives both modes, without fitting outcome scores.
        self.assertEqual([v==0 for v in means['counts']],[v==0 for v in means['occupancy']])

    def test_finite_budget_creates_competition_instead_of_common_failures(self):
        a=next(p for p in pool() if p['Position']=='TE' and p['NFLDepthOrder']==2)
        b=copy.deepcopy(a);b['FlexID']='other';b['Name']='other';players=[a,b]
        work={id(p):dict(p['NFLWorkload'],carries=0.,targets=.5,
            budgets=dict(carries=0.,targets=1.),efficiency=[4.,1.,10.]) for p in players}
        rows=[allocate(players,work,random.Random(seed))[0] for seed in range(1000)]
        x=[r[player_key(a)]['catches'] for r in rows];y=[r[player_key(b)]['catches'] for r in rows]
        self.assertTrue(all(a+b==1 for a,b in zip(x,y)))
        self.assertAlmostEqual(statistics.correlation(x,y),-1.)

    def test_rng_order_inputs_and_untargeted_scores_are_protected(self):
        players=pool();before=copy.deepcopy(players);a=random.Random(81);b=random.Random(81)
        baseline=_scenario_outcomes(a,players)
        model=JointOpportunityPrototype();trial=_scenario_outcomes(b,players,rotation_zero_prototype=model)
        self.assertEqual(a.getstate(),b.getstate());self.assertEqual(players,before)
        from rotation_zero_prototype import eligible
        for p in players:
            if not eligible(p) and p['Position']!='K':self.assertEqual(baseline[player_key(p)],trial[player_key(p)])
        work=sample_workloads(random.Random(3),players);scores={player_key(p):5. for p in players}
        self.assertEqual(model.apply(players,work,scores,random.Random(7)),
            model.apply(list(reversed(players)),work,scores,random.Random(7)))
        self.assertEqual(network_attempts,[])

    def test_invalid_identity_missing_and_overallocated_budgets_fail_closed(self):
        players=pool();work=sample_workloads(random.Random(3),players)
        with self.assertRaisesRegex(ValueError,'Ambiguous'):allocate(players+[players[0]],work,random.Random(1))
        with self.assertRaisesRegex(ValueError,'missing shared'):allocate(players,{},random.Random(1))
        target=next(p for p in players if p['Position']=='TE' and p['NFLDepthOrder']==2)
        for changes in (dict(team='OTHER'),dict(carries=99999),dict(budgets=dict(carries=0.,targets=0.))):
            altered=dict(work);altered[id(target)]=dict(work[id(target)],**changes)
            with self.assertRaises(ValueError):allocate(players,altered,random.Random(1))
        with self.assertRaises(ValueError):JointOpportunityPrototype(scoring='unknown')

    def test_fractional_budget_zero_probability_and_impossible_positive_score(self):
        self.assertAlmostEqual(no_success(2.5,.7),.72**2*.86)
        self.assertEqual(no_success(0.,0.),1.)
        with self.assertRaises(ValueError):no_success(1.,2.)
        p=next(p for p in pool() if p['Position']=='TE' and p['NFLDepthOrder']==2)
        work={id(p):dict(p['NFLWorkload'],carries=0.,targets=0.)}
        for mode in ('counts','occupancy'):
            model=JointOpportunityPrototype(scoring=mode)
            with self.assertRaisesRegex(ValueError,'without expected'):model.apply([p],work,{player_key(p):5.},random.Random(1))
            self.assertEqual(model.apply([p],work,{player_key(p):0.},random.Random(1)),{player_key(p):0.})
