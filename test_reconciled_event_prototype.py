"""Forecast constraints, raw event points and opposing DST linkage."""
from test_environment import install,network_attempts
install()
import copy,math,random,statistics,unittest
from unittest.mock import patch
import pulp
from test_linked_event_prototype import players
from nfl_simulation import player_key
from reconciled_event_prototype import ReconciledEventPrototype,BOUNDS,TOLERANCE


class ReconciledEventTests(unittest.TestCase):
    def test_forecast_below_frozen_ppr_floor_is_reconciled_without_point_multiplier(self):
        rows=players();p=next(p for p in rows if p['Position']=='TE');p['FlexProjection']=.2
        original=copy.deepcopy(rows);model=ReconciledEventPrototype(rows)
        self.assertGreater(p['NFLWorkload']['targets']*p['NFLWorkload']['efficiency'][1],.2)
        for key,forecast in model.forecasts.items():
            self.assertAlmostEqual(model.expected[key],forecast,delta=TOLERANCE)
            self.assertEqual(model.normalizers[key],1.)
            from nfl_workload import points
            self.assertAlmostEqual(points(model.work[key]),forecast,delta=TOLERANCE)
        result=model.apply(rows,{},dict.fromkeys(model.keys,999.),random.Random(81))
        for key in model.forecasts:self.assertEqual(result[key],model.last_ledger['players'][key]['raw_points'])
        self.assertEqual(rows,original)

    def test_reconciled_rates_and_budgets_obey_declared_bounds(self):
        model=ReconciledEventPrototype(players())
        for w in model.work.values():
            self.assertLessEqual(w['efficiency'][0],BOUNDS['rush_yards']+TOLERANCE)
            self.assertLessEqual(w['efficiency'][1],1.)
            self.assertLessEqual(w['efficiency'][2],BOUNDS['receive_yards']+TOLERANCE)
            self.assertLessEqual(w['rates']['rushing_td_per_carry'],BOUNDS['rush_td']+TOLERANCE)
            self.assertLessEqual(w['rates']['receiving_td_per_catch'],BOUNDS['receive_td']+TOLERANCE)
        for team,qb in model.qbs.items():self.assertLessEqual(qb['interception_probability'],BOUNDS['interception']+TOLERANCE)
        self.assertEqual(model.reconciliation['status'],'Optimal')
        self.assertEqual(len(model.reconciliation['solver_sha256']),64)
        self.assertTrue(model.reconciliation['changes'])
        model.apply(players(),{},dict.fromkeys(model.keys,0.),random.Random(81))
        for b in model.last_ledger['budgets']:self.assertEqual(b['assigned']+b['reserve'],b['total'])

    def test_offense_and_dst_means_and_negative_opposing_correlation(self):
        rows=players();model=ReconciledEventPrototype(rows,link_dst=True);rng=random.Random(713)
        values={k:[] for k in model.forecasts}
        for _ in range(6000):
            result=model.apply(rows,{},dict.fromkeys(model.keys,0.),rng);rng.random()
            for key in values:values[key].append(result[key])
        for key,draws in values.items():
            se=statistics.pstdev(draws)/math.sqrt(len(draws))
            self.assertLessEqual(abs(statistics.mean(draws)-model.forecasts[key]),5*se+TOLERANCE,key)
        for key,link in model.dst_links.items():
            self.assertLess(statistics.correlation(values[key],values[link['qb']]),-.05)
            self.assertGreaterEqual(min(values[key]),-4.)

    def test_link_toggle_preserves_offensive_draws_parent_rng_and_manual_score(self):
        rows=players();manual=next(p for p in rows if p['Position']=='WR');manual['ProjectionSource']='Manual'
        before=copy.deepcopy(rows);off=ReconciledEventPrototype(rows);on=ReconciledEventPrototype(rows,link_dst=True)
        a=random.Random(17);b=random.Random(17);state=a.getstate();base=dict.fromkeys(on.keys,123.)
        x=off.apply(rows,{},base,a);y=on.apply(rows,{},base,b)
        for key in off.affected_keys:self.assertEqual(x[key],y[key])
        self.assertEqual(y[player_key(manual)],123.);self.assertEqual(rows,before)
        self.assertEqual(a.getstate(),state);self.assertEqual(b.getstate(),state)
        self.assertEqual(network_attempts,[])

    def test_dst_floor_is_bounded_without_clipping_and_link_is_monotone(self):
        rows=players()
        for p in rows:
            if p['Position']=='DST':p['FlexProjection']=0.
        model=ReconciledEventPrototype(rows,link_dst=True)
        for link in model.dst_links.values():
            self.assertGreaterEqual(link['forecast']+link['coefficient']*(link['opponent_mean']-link['maximum'])-link['noise'],-4.-TOLERANCE)
        base=dict.fromkeys(model.keys,0.)
        with patch('linked_event_prototype.LinkedEventPrototype.apply',side_effect=lambda *args:dict(base)),patch('reconciled_event_prototype.random.Random') as stream:
            stream.return_value.uniform.return_value=0.
            model.last_ledger={'players':{q['key']:{'raw_points':0.} for q in model.qbs.values()}}
            low=model.apply(rows,{},base,random.Random(1))
            for link in model.dst_links.values():model.last_ledger['players'][link['qb']]['raw_points']=link['maximum']
            high=model.apply(rows,{},base,random.Random(1))
            for key in model.dst_links:self.assertLess(high[key],low[key])
            next(iter(model.last_ledger['players'].values()))['raw_points']=1e9
            with self.assertRaisesRegex(ValueError,'bound exceeded'):model.apply(rows,{},base,random.Random(1))

    def test_infeasible_forecast_and_nonoptimal_solve_fail_without_fallback(self):
        rows=players();before=copy.deepcopy(rows);next(p for p in rows if p['Position']=='WR')['FlexProjection']=100000.
        with self.assertRaisesRegex(ValueError,'Infeasible'):ReconciledEventPrototype(rows)
        self.assertEqual(before,players())
        with patch('pulp.LpProblem.solve',return_value=pulp.LpStatusNotSolved):
            with self.assertRaisesRegex(ValueError,'Not Solved'):ReconciledEventPrototype(players())

    def test_dst_missing_ambiguous_manual_or_unmodeled_opponent_fails_closed(self):
        rows=players();rows=[p for p in rows if not (p['Team']=='ARI' and p['Position']=='DST')]
        with self.assertRaisesRegex(ValueError,'two unambiguous'):ReconciledEventPrototype(rows,link_dst=True)
        rows=players();p=next(p for p in rows if p['Position']=='DST');p['ProjectionSource']='Manual'
        with self.assertRaisesRegex(ValueError,'two unambiguous'):ReconciledEventPrototype(rows,link_dst=True)
        rows=players();p=next(p for p in rows if p['Position']=='QB');p['ProjectionSource']='Manual'
        with self.assertRaisesRegex(ValueError,'modeled opposing'):ReconciledEventPrototype(rows,link_dst=True)
        rows=players();p=copy.deepcopy(next(p for p in rows if p['Position']=='DST'));p['FlexID']='other';p['Name']='other';rows.append(p)
        with self.assertRaisesRegex(ValueError,'Ambiguous defensive'):ReconciledEventPrototype(rows,link_dst=True)

    def test_reconciliation_and_event_draws_are_order_independent(self):
        rows=players();a=ReconciledEventPrototype(rows,link_dst=True);b=ReconciledEventPrototype(list(reversed(rows)),link_dst=True)
        base=dict.fromkeys(a.keys,0.)
        self.assertEqual(a.apply(rows,{},base,random.Random(31)),b.apply(list(reversed(rows)),{},base,random.Random(31)))


    def test_coupled_qb_mean_conflict_has_an_explanatory_lower_bound(self):
        rows=players()
        for p in rows:
            if p['Team']=='ARI' and p['Position']=='QB':p['FlexProjection']=.1
            if p['Team']=='ARI' and p['Position'] in ('WR','TE'):p['FlexProjection']=20.
        with self.assertRaisesRegex(ValueError,'QB mean.*below conservative lower bound'):
            ReconciledEventPrototype(rows)
