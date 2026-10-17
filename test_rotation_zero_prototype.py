"""Opt-in zero-score prototype: conditional means and team interactions."""
from test_environment import install, network_attempts
install()
import copy,hashlib,json,math,random,statistics,unittest
from unittest.mock import patch
from nfl_simulation import _scenario_outcomes,player_key
from nfl_workload import prepare_workloads


def pool():
    rows=[]
    for team,opponent in [('ARI','CAR'),('CAR','ARI')]:
        for i,(pos,depth,mean) in enumerate([('QB',1,16),('RB',1,12),('RB',2,4),('WR',1,15),('WR',2,5),('TE',1,7),('TE',2,2),('K',1,8),('DST',0,7)]):
            rows.append(dict(Name=team+str(i),Team=team,Opponent=opponent,GameKey='ARI@CAR',
                GameInfo='ARI@CAR 10/15/2026 08:15PM ET',Position=pos,NFLDepthOrder=depth,
                NFLAvailability='ACTIVE',NFLActive=True,FlexID=team+str(i),FlexProjection=mean,
                ProjectionInputsVersion=1,ProjectionSource='Automatic workload estimate' if pos in ('QB','RB','WR','TE') else 'Historical average estimate',
                HistoricalPPG=mean,NFLUsageGames=3,NFLUsageSeason=2026))
    prepare_workloads(rows)
    return rows


class RotationZeroTests(unittest.TestCase):
    def test_default_draws_match_prechange_reference(self):
        players=pool();rng=random.Random(481516)
        rows=[{k:round(v,8) for k,v in _scenario_outcomes(rng,players).items()} for _ in range(100)]
        self.assertEqual(hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
            'ea3d7c81ba7b27ff080e2246284349cd49aac78184f26f6d435d5b1c21f64055')

    def test_conditional_mean_is_preserved_for_each_sampled_workload(self):
        from rotation_zero_prototype import RotationZeroPrototype,opportunity
        player=next(p for p in pool() if p['Position']=='TE' and p['NFLDepthOrder']==2)
        for targets in (.1,1.,3.):
            w=dict(player['NFLWorkload'],targets=targets,carries=0.)
            before={player_key(player):5.};q=min(.95,math.exp(-opportunity(w)))
            model=RotationZeroPrototype()
            with patch('rotation_zero_prototype._gate_rng') as stream:
                stream.return_value.random.return_value=0.
                zero=model.apply([player],{id(player):w},before,random.Random(1))[player_key(player)]
                stream.return_value.random.return_value=1.
                positive=model.apply([player],{id(player):w},before,random.Random(1))[player_key(player)]
            self.assertEqual(zero,0.)
            self.assertAlmostEqual(q*zero+(1-q)*positive,5.,places=12)

    def test_ineligible_roles_sources_and_invalid_evidence_are_unchanged(self):
        from rotation_zero_prototype import eligible,RotationZeroPrototype
        template=next(p for p in pool() if p['Position']=='TE' and p['NFLDepthOrder']==2)
        for changes in (dict(Position='QB'),dict(Position='K'),dict(Position='DST'),dict(NFLDepthOrder=0),
                dict(NFLDepthOrder=1),dict(NFLAvailability='STARTER'),dict(NFLAvailability='OUT'),
                dict(ProjectionSource='Manual'),dict(NFLWorkload=None)):
            player=dict(template,**changes);self.assertFalse(eligible(player))
            before={player_key(player):5.}
            self.assertEqual(RotationZeroPrototype().apply([player],{},before,random.Random(1)),before)
        promoted=copy.deepcopy(template);promoted['NFLWorkload']['promoted_slots']=1
        self.assertFalse(eligible(promoted))

    def test_rng_budgets_and_input_are_unchanged_but_kickers_receive_new_offense(self):
        from rotation_zero_prototype import RotationZeroPrototype
        players=pool();before=copy.deepcopy(players);a=random.Random(77);b=random.Random(77)
        observed=[]
        from nfl_specialists import specialist_outcomes
        def specialists(*args):
            observed.append(dict(args[2]));return specialist_outcomes(*args)
        with patch('nfl_specialists.specialist_outcomes',side_effect=specialists):
            baseline=_scenario_outcomes(a,players)
            trial=_scenario_outcomes(b,players,rotation_zero_prototype=RotationZeroPrototype())
        self.assertEqual(a.getstate(),b.getstate());self.assertEqual(players,before)
        self.assertNotEqual(observed[0],observed[1])
        for p in players:
            if p['Position'] in ('QB','DST') or (p['NFLDepthOrder']==1 and p['Position']!='K'):
                self.assertEqual(baseline[player_key(p)],trial[player_key(p)])
        from nfl_workload import sample_workloads
        baseline_work=[];trial_work=[]
        def capture(storage):
            def sampled(*args):
                value=sample_workloads(*args);storage.append(copy.deepcopy(value));return value
            return sampled
        with patch('nfl_workload.sample_workloads',side_effect=capture(baseline_work)):
            _scenario_outcomes(random.Random(99),players)
        with patch('nfl_workload.sample_workloads',side_effect=capture(trial_work)):
            _scenario_outcomes(random.Random(99),players,rotation_zero_prototype=RotationZeroPrototype())
        self.assertEqual(baseline_work,trial_work)

    def test_model_is_deterministic_and_does_not_rewrite_outcomes(self):
        from rotation_zero_prototype import RotationZeroPrototype
        from nfl_workload import sample_workloads
        players=pool();work=sample_workloads(random.Random(3),players)
        baseline={player_key(p):5. for p in players};before=dict(baseline)
        model=RotationZeroPrototype()
        a=model.apply(players,work,baseline,random.Random(7))
        b=model.apply(list(reversed(players)),work,baseline,random.Random(7))
        self.assertEqual(a,b);self.assertEqual(baseline,before)
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            model.apply(players+[players[0]],work,baseline,random.Random(1))
        self.assertEqual(network_attempts,[])

    def test_kicker_team_form_is_recomputed_after_zero_gates(self):
        from rotation_zero_prototype import RotationZeroPrototype
        players=pool();events={t:dict(extra_points=0,field_goals=[]) for t in ('ARI','CAR')}
        with patch('nfl_specialists.sample_game',return_value=events) as game, patch('rotation_zero_prototype._gate_rng') as gate:
            gate.return_value.random.return_value=0.
            _scenario_outcomes(random.Random(77),players)
            old=dict(game.call_args.args[2])
            _scenario_outcomes(random.Random(77),players,rotation_zero_prototype=RotationZeroPrototype())
            new=dict(game.call_args.args[2])
        for team in ('ARI','CAR'):self.assertLess(new[team],old[team])

    def test_missing_sampled_workload_rejected_and_zero_opportunity_guard_is_finite(self):
        from rotation_zero_prototype import RotationZeroPrototype
        player=next(p for p in pool() if p['Position']=='TE' and p['NFLDepthOrder']==2)
        model=RotationZeroPrototype();before={player_key(player):5.}
        with self.assertRaisesRegex(ValueError,'missing shared sampled workload'):
            model.apply([player],{},before,random.Random(1))
        w=dict(player['NFLWorkload'],carries=0.,targets=0.)
        with patch('rotation_zero_prototype._gate_rng') as gate:
            gate.return_value.random.return_value=1.
            value=model.apply([player],{id(player):w},before,random.Random(1))[player_key(player)]
        self.assertAlmostEqual(value,100.)
        with self.assertRaises(ValueError):RotationZeroPrototype(opportunity_basis='unknown')
