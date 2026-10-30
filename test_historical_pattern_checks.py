from test_environment import install
install()
import copy,unittest
from historical_pattern_checks import pattern_checks,render_checks,METRICS
from historical_series_review import combine_followups,ReviewLimitError


def report(deltas=(2,2,2,2,2),users=10,peers=5):
    contests=[]
    for i,delta in enumerate(deltas,1):
        date=f'2026-09-{i:02}';profiles=[]
        for u in range(users):
            a={k:None for k in METRICS};b=dict(a);a['player_pool']=20+delta;b['player_pool']=20
            profiles.append(dict(user_key='dk:u'+str(u),peer_users=peers,entries=50,metrics=a,peer_medians=b))
        contests.append(dict(contest_key='contest'+str(i),date=date,format='showdown',preset='150-Max',slate=['showdown',date,['A@B']],winners=profiles))
    return dict(review_mode='prior_qualified',since='2026-08-01',through=contests[-1]['date'],followup_from=contests[0]['date'],format='showdown',contests=contests,evidence=[],excluded=[])

class CrossGamePatternTests(unittest.TestCase):
    def metric(self,r,peers=5):return pattern_checks(r,peers)['showdown / 150-Max']['player_pool']
    def test_consistent_descriptive_difference_needs_five_independent_games(self):
        v=self.metric(report());self.assertEqual(v['slates'],5);self.assertEqual(v['mean_delta'],2)
        self.assertTrue(v['consistent_descriptive_direction']);self.assertEqual(v['leave_one_out_min'],2)
        self.assertFalse(self.metric(report((2,2,2,2)))['consistent_descriptive_direction'])
    def test_one_outlier_game_does_not_create_a_consistent_direction(self):
        v=self.metric(report((100,-1,-1,-1,-1)));self.assertFalse(v['consistent_descriptive_direction'])
        self.assertLess(v['leave_one_out_min'],0);self.assertGreater(v['leave_one_out_max'],0)
    def test_duplicate_contests_in_a_slate_do_not_inflate_games_or_distinct_users(self):
        r=report((2,),users=9);other=copy.deepcopy(r['contests'][0]);other['contest_key']='other';r['contests'].append(other)
        v=self.metric(r);self.assertEqual(v['slates'],0);self.assertEqual(v['excluded_small_slates'],1)
    def test_user_counts_within_contests_do_not_change_equal_slate_weighting(self):
        r=report((2,4));r['contests'][0]['winners']*=20
        self.assertEqual(self.metric(r)['mean_delta'],3)
    def test_metadata_missing_or_nonfinite_values_are_not_zero_differences(self):
        r=report((2,))
        for p in r['contests'][0]['winners']:p['metrics']['player_pool']=float('nan')
        self.assertEqual(pattern_checks(r),{})
    def test_peer_sensitivity_never_borrows_unsupported_profiles(self):
        self.assertEqual(pattern_checks(report(peers=5),10),{})
        self.assertIn('No matched evidence',render_checks(report(peers=5)))
    def test_formats_and_presets_do_not_pool_toward_minimum_games(self):
        r=report((2,2,2,2));other=copy.deepcopy(r['contests'])
        for c in other:c['preset']='20-Max'
        r['contests']+=other
        for v in pattern_checks(r).values():self.assertFalse(v['player_pool']['consistent_descriptive_direction'])
    def test_slate_date_and_format_identity_are_required(self):
        r=report();r['contests'][0]['slate'][1]='2026-08-01'
        with self.assertRaises(ValueError):pattern_checks(r)
    def test_winners_only_and_invalid_peer_thresholds_are_rejected(self):
        r=report();r['review_mode']='winners'
        with self.assertRaises(ValueError):pattern_checks(r)
        for value in (0,True,1.5):
            with self.assertRaises(ValueError):pattern_checks(report(),value)
    def test_combine_preserves_disjoint_windows_and_exposes_gaps(self):
        a=report((2,));b=report((3,));b['followup_from']=b['through']='2026-09-03';b['contests'][0].update(date='2026-09-03',contest_key='later',slate=['showdown','2026-09-03',['A@B']])
        result=combine_followups([b,a]);self.assertEqual(result['evaluation_windows'],[['2026-09-01','2026-09-01'],['2026-09-03','2026-09-03']])
        self.assertEqual(len(result['contests']),2)
    def test_overlapping_batches_are_rejected_instead_of_double_counted(self):
        a=report((2,))
        with self.assertRaises(ValueError):combine_followups([a,copy.deepcopy(a)])
    def test_conflicting_historical_receipts_and_modes_are_rejected(self):
        a=report((2,));b=report((3,));b['followup_from']=b['through']='2026-09-03';b['contests']=[]
        a['evidence']=[dict(contest_key='prior',source='one')];b['evidence']=[dict(contest_key='prior',source='two')]
        with self.assertRaises(ValueError):combine_followups([a,b])
        b['review_mode']='winners'
        with self.assertRaises(ValueError):combine_followups([a,b])

    def pairing_report(self,deltas=(2,2,2,2,2)):
        r=report(deltas);r['pairing_usage']=True
        for c,delta in zip(r['contests'],deltas):
            for p in c['winners']:
                p['metrics']['kd_two_pct']=10+delta;p['peer_medians']['kd_two_pct']=10
        return r

    def test_pairing_metrics_require_explicit_report_evidence(self):
        r=self.pairing_report();v=pattern_checks(r)['showdown / 150-Max']['kd_two_pct']
        self.assertEqual(v['mean_delta'],2);self.assertTrue(v['consistent_descriptive_direction'])
        self.assertIn('Two K/DST (%)',render_checks(r))
        r.pop('pairing_usage')
        self.assertNotIn('kd_two_pct',pattern_checks(r)['showdown / 150-Max'])

    def test_pairing_outlier_and_metric_specific_support_are_not_hidden(self):
        r=self.pairing_report((100,-1,-1,-1,-1))
        v=pattern_checks(r)['showdown / 150-Max']['kd_two_pct']
        self.assertFalse(v['consistent_descriptive_direction']);self.assertEqual(v['leave_one_out_min'],-1)
        for c in r['contests']:
            for p in c['winners']:p['metric_peer_users']={'kd_two_pct':4}
        self.assertNotIn('kd_two_pct',pattern_checks(r)['showdown / 150-Max'])

    def test_pairing_small_games_and_unknown_values_do_not_supply_support(self):
        r=self.pairing_report()
        for p in r['contests'][0]['winners']:p['metrics']['kd_two_pct']=None
        r['contests'][1]['winners']=r['contests'][1]['winners'][:9]
        v=pattern_checks(r)['showdown / 150-Max']['kd_two_pct']
        self.assertEqual(v['slates'],3);self.assertEqual(v['excluded_small_slates'],1)
        self.assertFalse(v['consistent_descriptive_direction'])

if __name__=='__main__':unittest.main()
