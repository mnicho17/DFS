"""Linked raw production, forecast calibration and diagnostic isolation."""
from test_environment import install,network_attempts
install()
import copy,math,random,statistics,unittest
from test_rotation_zero_prototype import pool
from nfl_eligibility import apply_qb_eligibility
from nfl_simulation import _scenario_outcomes,player_key
from linked_event_prototype import LinkedEventPrototype


def players():return apply_qb_eligibility(pool())


class LinkedEventTests(unittest.TestCase):
    def test_raw_qb_and_receiving_production_reconcile_and_reserves_stay_unresolved(self):
        rows=players();model=LinkedEventPrototype(rows);rng=random.Random(617)
        for _ in range(50):
            model.apply(rows,{},dict.fromkeys(model.keys,1.),rng);rng.random()
            ledger=model.last_ledger['players'];budgets=model.last_ledger['budgets']
            for team,qb in model.qbs.items():
                q=ledger[qb['key']];others=[v for k,v in ledger.items() if model.teams[k]==team and k!=qb['key']]
                self.assertEqual(q['passing_yards'],sum(v['receiving_yards'] for v in others))
                self.assertEqual(q['passing_td'],sum(v['receiving_td'] for v in others))
                self.assertEqual(q['passing_completions'],sum(v['catches'] for v in others))
                self.assertLessEqual(q['passing_td'],q['passing_completions'])
                self.assertLessEqual(q['interceptions'],q['passing_attempts']-q['passing_completions'])
                self.assertEqual(q['unresolved_targets'],sum(b['reserve'] for b in budgets if b['team']==team and b['metric']=='targets'))
            for b in budgets:self.assertEqual(b['assigned']+b['reserve'],b['total'])

    def test_event_scoring_replaces_baseline_score_noise_and_preserves_parent_rng(self):
        rows=players();model=LinkedEventPrototype(rows);rng=random.Random(13);state=rng.getstate()
        sampled={'sentinel':[1,2]};before=copy.deepcopy(sampled)
        a=model.apply(rows,sampled,dict.fromkeys(model.keys,1.),rng)
        b=model.apply(rows,sampled,dict.fromkeys(model.keys,9999.),rng)
        for key in model.affected_keys:self.assertEqual(a[key],b[key])
        self.assertEqual(state,rng.getstate());self.assertEqual(sampled,before)
        self.assertEqual(a,model.apply(list(reversed(rows)),sampled,dict.fromkeys(model.keys,1.),rng))

    def test_forecast_means_and_analytic_calibration(self):
        rows=players();model=LinkedEventPrototype(rows);rng=random.Random(420);draws={k:[] for k in model.affected_keys}
        for key,forecast in model.forecasts.items():self.assertAlmostEqual(model.expected[key]*model.normalizers[key],forecast)
        for _ in range(6000):
            values=model.apply(rows,{},dict.fromkeys(model.keys,0.),rng);rng.random()
            for key in draws:draws[key].append(values[key])
        for key,values in draws.items():
            se=statistics.pstdev(values)/math.sqrt(len(values))
            self.assertLessEqual(abs(statistics.mean(values)-model.forecasts[key]),5*se+1e-9,key)
        # Calibration is disclosed; normalized points need not equal raw DK points.
        self.assertTrue(any(abs(v-1)>.05 for v in model.normalizers.values()))

    def test_pipeline_keeps_default_rng_manual_dst_and_inputs_unchanged(self):
        rows=players();manual=next(p for p in rows if p['Position']=='WR');manual['ProjectionSource']='Manual'
        before=copy.deepcopy(rows);a=random.Random(91);b=random.Random(91);model=LinkedEventPrototype(rows)
        baseline=_scenario_outcomes(a,rows);trial=_scenario_outcomes(b,rows,rotation_zero_prototype=model)
        self.assertEqual(a.getstate(),b.getstate());self.assertEqual(rows,before)
        for p in rows:
            if p['Position']=='DST' or p is manual:self.assertEqual(baseline[player_key(p)],trial[player_key(p)])
        self.assertEqual(network_attempts,[])

    def test_unknown_or_ambiguous_qb_and_invalid_event_probabilities_fail_closed(self):
        rows=players()
        for p in rows:
            if p['Position']=='QB':p.pop('NFLQBEligible')
        with self.assertRaisesRegex(ValueError,'frozen eligible quarterback'):LinkedEventPrototype(rows)
        rows=players();qb=next(p for p in rows if p['Position']=='QB');other=copy.deepcopy(qb);other['FlexID']='extra';other['Name']='extra'
        with self.assertRaisesRegex(ValueError,'frozen eligible quarterback'):LinkedEventPrototype(rows+[other])
        rows=players();p=next(p for p in rows if p['Position']=='WR');p['NFLWorkload']['rates']['receiving_td_per_catch']=2.
        with self.assertRaisesRegex(ValueError,'event probability'):LinkedEventPrototype(rows)

    def test_frozen_player_identity_and_positive_forecast_without_events_are_rejected(self):
        rows=players();model=LinkedEventPrototype(rows)
        altered=copy.deepcopy(rows);altered[0]['FlexID']='changed'
        with self.assertRaisesRegex(ValueError,'identities changed'):model.apply(altered,{},dict.fromkeys(model.keys,0.),random.Random(1))
        with self.assertRaisesRegex(ValueError,'Ambiguous'):LinkedEventPrototype(rows+[rows[0]])
        p=next(p for p in rows if p['Position']=='TE');p['NFLWorkload']['carries']=0.;p['NFLWorkload']['targets']=0.
        with self.assertRaisesRegex(ValueError,'no positive expected'):LinkedEventPrototype(rows)


    def test_shared_completion_environment_preserves_forecast_means(self):
        rows=players();model=LinkedEventPrototype(rows,completion_environment=.2);rng=random.Random(147)
        values={k:[] for k in model.affected_keys};contexts=[]
        for _ in range(6000):
            result=model.apply(rows,{},dict.fromkeys(model.keys,0.),rng);rng.random()
            for key in values:values[key].append(result[key])
            contexts.append(model.last_ledger['completion_contexts']['ARI'])
        self.assertEqual(set(contexts),{.8,1.2})
        for key,draws in values.items():
            se=statistics.pstdev(draws)/math.sqrt(len(draws))
            self.assertLessEqual(abs(statistics.mean(draws)-model.forecasts[key]),5*se+1e-9,key)

    def test_completion_bounds_keep_probabilities_valid_without_clipping_bias(self):
        for value in (-.1,.21,float('nan'),True):
            with self.assertRaises(ValueError):LinkedEventPrototype(players(),completion_environment=value)
        rows=players()
        for p in rows:
            if p['Position'] in ('RB','WR','TE') and p.get('NFLWorkload'):
                p['NFLWorkload']['efficiency'][1]=1.
        for p in rows:
            if p['Position']=='QB':p['NFLWorkload']['rates']['interceptions_per_attempt']=0.
        model=LinkedEventPrototype(rows,completion_environment=.2)
        self.assertTrue(all(v==0. for v in model.completion_amplitudes.values()))
        model.apply(rows,{},dict.fromkeys(model.keys,0.),random.Random(1))
        self.assertTrue(all(v==1. for v in model.last_ledger['completion_contexts'].values()))
