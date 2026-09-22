import copy
import random
import unittest
from nfl_kickers import build_kicking_index,attach_kicking_history,prepare_kickers
from projection_sources import initialize_projection,prepare_nfl_projections
from nfl_auto_data import _reapply_context_adjustments,apply_auto_nfl_context
from nfl_specialists import sample_game,kicker_score


def row(week=1,**changes):
    r=dict(player_display_name='Test Kicker',position='K',team='SEA',opponent_team='NE',
           week=week,season=2025,season_type='REG',fg_att=2,fg_made=1,pat_att=3,pat_made=3,
           fg_made_0_19=0,fg_made_20_29=0,fg_made_30_39=1,fg_made_40_49=0,fg_made_50_59=0,fg_made_60_=0)
    r.update(changes);return r


def player():
    p=dict(Name='Test Kicker',Team='SEA',Opponent='NE',Position='K',GameInfo='NE@SEA 09/09/2026',NFLDepthOrder=1)
    initialize_projection(p,historical=12);return p


class KickerForecastTests(unittest.TestCase):
    def test_latest_eight_dedup_and_missing_vs_zero(self):
        rows=[row(i) for i in range(1,11)]
        rows += [row(10),row(11,fg_att=''),row(12,season_type='POST'),row(13,fg_att=-1),row(14,fg_att=0,fg_made=0,pat_att=0,pat_made=0)]
        p=player();attach_kicking_history(p,build_kicking_index(rows,2025))
        a=p['NFLKickingHistory']['player']
        self.assertEqual(a['games'],8);self.assertEqual(a['fga'],14)
        self.assertFalse(build_kicking_index([row(fg_att='')],2025))

    def test_override_precedence_and_idempotent_refresh(self):
        p=player();attach_kicking_history(p,build_kicking_index([row(i) for i in range(1,9)],2025))
        p['NFLRoleScore']=2
        _reapply_context_adjustments([p])
        self.assertEqual(p['ProjectionSource'],'Automatic kicker opportunities')
        self.assertEqual(p['BaseProjection'],p['FlexProjection'])
        before=copy.deepcopy(p);_reapply_context_adjustments([p]);self.assertEqual(before,p)
        p['ManualProjection']=0;_reapply_context_adjustments([p]);self.assertEqual(p['FlexProjection'],0)
        p['ManualProjection']=None;p['ImportedProjection']=11;_reapply_context_adjustments([p]);self.assertEqual(p['FlexProjection'],11)

    def test_missing_feed_clears_new_estimate_but_old_snapshot_unchanged(self):
        p=player();attach_kicking_history(p,build_kicking_index([row()],2025));prepare_nfl_projections([p])
        attach_kicking_history(p,{});prepare_nfl_projections([p])
        self.assertEqual(p['ProjectionSource'],'Historical average estimate');self.assertEqual(p['FlexProjection'],12)
        old=dict(Name='Legacy',Position='K',BaseProjection=12,FlexProjection=12)
        expected=copy.deepcopy(old);prepare_nfl_projections([old]);self.assertEqual(old,expected)

    def test_context_attenuation_and_prior_season_weight(self):
        p=player();attach_kicking_history(p,build_kicking_index([row()],2025));prepare_kickers([p])
        baseline=p['KickerProjection'];self.assertEqual(p['NFLKickerOpportunities']['prior_season_discount'],.5)
        p['NFLWeatherScore']=-2;prepare_kickers([p]);self.assertLess(p['KickerProjection'],baseline)
        p['GameInfo']='NE@SEA 09/09/2025';prepare_kickers([p]);self.assertEqual(p['NFLKickerOpportunities']['prior_season_discount'],1)

    def test_failed_attempts_cannot_credit_kicker_or_points_allowed(self):
        m=dict(fga=5,xpa=0,fg_rate=0,xp_rate=0,made_distance_mix=[1,0,0])
        attempts=0
        for seed in range(100):
            e=sample_game(random.Random(seed),['A','B'],{},0,{}, {},{'A':m})['A']
            attempts+=e['field_goal_attempts'];self.assertEqual(kicker_score(e),0);self.assertEqual(e['field_goals'],[])
        self.assertGreater(attempts,0)

    def test_full_enrichment_attaches_feed_without_changing_offensive_projection(self):
        p=player()
        apply_auto_nfl_context([p],usage_rows=[row()],usage_season=2025,sleeper_data={},fetch_external=False)
        self.assertEqual(p['ProjectionSource'],'Automatic kicker opportunities')
        self.assertEqual(p['NFLKickerOpportunities']['season'],2025)
