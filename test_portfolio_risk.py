"""Deterministic RL-06 arithmetic and recorded-role oracles, no sports claims."""
import copy
import unittest

from portfolio_risk import (RiskCapture,calculate,recorded_player,role_evidence,
                           pair_coverage,distribution,summary,EXPORT_UNAVAILABLE)

CAPTURE = '2026-09-21T18:00:00-04:00'
KICKOFF = '2026-09-21T20:15:00-04:00'


def raw_player(key, **changes):
    value = 20 if key=='A' else 10 if key=='B' else 5
    return dict(Name='Private Athlete '+key,Team='AAA',Position='WR',FlexProjection=value,
        CptProjection=value*1.5,ProjectionSource='Synthetic frozen forecast',
        NFLDepthOrder=1,NFLAvailability='STARTER',NFLQBEligible=None,
        InjurySource='Sleeper',LiveStatusUpdatedAt='2026-09-21T17:00:00-04:00',**changes)


def make_capture(patterns=('AB','A','B',''), kind='showdown', *, updates=None, qualified=True):
    positions = dict(Q='QB',R1='RB',R2='RB',W='WR',T='TE',R3='RB',D='DST',W2='WR',W3='WR')
    keys = ['A','B',*positions] if kind=='classic' else ['A','B',*[f'O{i}' for i in range(6)]]
    pool = {}
    for key in keys:
        raw = raw_player(key)
        raw['Position'] = positions.get(key,'WR')
        raw.update((updates or {}).get(key,{}))
        pool[key] = recorded_player(key,raw,qualified=qualified,captured_at=CAPTURE,kickoff=KICKOFF,
                                    game='AAA@BBB 09/21/2026 08:15PM ET' if qualified else None)
    rows = []
    for pattern in patterns:
        if kind=='classic':
            players = ['Q','R1','R2','A' if 'A' in pattern else 'W2','B' if 'B' in pattern else 'W3','W','T','R3','D']
            roles = ['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']
        else:
            players = ['O0','O1','O2','O3','A' if 'A' in pattern else 'O4','B' if 'B' in pattern else 'O5']
            roles = ['CPT']+['FLEX']*5
        rows.append([dict(key=k,role=r) for k,r in zip(players,roles)])
    return RiskCapture.freeze(dict(version=1,source_kind='archive' if qualified else 'saved_export',
        label='Generated archive - submission not established' if qualified else
              'Saved export - submission and original pregame input not established',
        sport='NFL',format=kind,slate_date='2026-09-21' if qualified else None,
        source_count=len(rows),rosters=rows,pool=pool,forecast_qualified=qualified,rejected={},limits={},
        provenance=dict(input_id='PRIVATE_INPUT',archive_id='PRIVATE_ARCHIVE',export_id='PRIVATE_EXPORT',
                        path='C:/PRIVATE_PATH/private.csv',contest_id='PRIVATE_CONTEST',username='PRIVATE_USER')))


def captain_a(capture):
    d = capture.data
    for row in d['rosters']:
        a = next((s for s in row if s['key']=='A'),None)
        if a:
            row[0]['role'] = 'FLEX'; a['role'] = 'CPT'
            row.sort(key=lambda s:s['role']!='CPT')
    return RiskCapture.freeze(d)


class PortfolioRiskMathTests(unittest.TestCase):
    def test_four_buckets_are_disjoint_and_union_is_inclusive(self):
        for kind in ('classic','showdown'):
            d = calculate(make_capture(kind=kind),['A','B']).data
            self.assertEqual(d['pair']['buckets'],dict(both=1,a_only=1,b_only=1,neither=1))
            self.assertEqual(sum(d['pair']['buckets'].values()),d['N'])
            self.assertEqual((d['pair']['either'],d['pair']['exactly_one']),(3,2))
            self.assertEqual([e['any']['count'] for e in d['target_exposures']],[2,2])

    def test_equal_individual_exposure_different_joint_dependency_oracle(self):
        overlap = calculate(make_capture(('AB','AB','','')),['A','B']).data
        disjoint = calculate(make_capture(('A','A','B','B')),['A','B']).data
        for d in (overlap,disjoint):
            self.assertEqual(d['scenarios'][-1]['direct_change']['mean'],15)
            self.assertEqual([e['any']['pct'] for e in d['target_exposures']],[50,50])
        self.assertEqual(overlap['scenarios'][-1]['direct_change']['max'],30)
        self.assertEqual(disjoint['scenarios'][-1]['direct_change']['max'],20)

    def test_captain_oracle_is_27_5_and_scaling_is_once(self):
        d = calculate(captain_a(make_capture(('AB',))),['A','B']).data
        case = next(s for s in d['scenarios'] if s['retention']==[.25,.5])
        self.assertEqual(case['direct_change']['mean'],27.5)
        self.assertEqual(case['baseline_mean']-case['stressed_mean'],27.5)
        e = d['target_exposures'][0]
        self.assertEqual(e['any']['count'],e['captain']['count']+e['noncaptain']['count'])

    def test_unknown_unselected_forecast_keeps_direct_27_5(self):
        d = calculate(captain_a(make_capture(('AB',),updates={'O1':dict(FlexProjection=None)})),['A','B']).data
        case = next(s for s in d['scenarios'] if s['retention']==[.25,.5])
        self.assertEqual((d['M'],d['M_delta']),(0,1))
        self.assertEqual(case['direct_change']['mean'],27.5)
        self.assertIsNone(case['baseline_mean']); self.assertIsNone(case['stressed_mean'])

    def test_default_no_targets_and_all_five_single_retention_cases(self):
        cap = make_capture(('A','A',''))
        self.assertEqual(calculate(cap).data['scenarios'],[])
        d = calculate(cap,['A']).data
        self.assertEqual([s['retention'] for s in d['scenarios']],[[1],[.75],[.5],[.25],[0]])
        self.assertEqual(d['scenarios'][0]['baseline_mean'],d['scenarios'][0]['stressed_mean'])
        self.assertEqual([s['direct_change']['mean'] for s in d['scenarios']],[0,10/3,20/3,10,40/3])

    def test_joint_grid_has_25_cases_with_fixed_cohorts_and_affected_coverage(self):
        cap = make_capture(updates={'O4':dict(FlexProjection=None)})
        d = calculate(cap,['A','B']).data
        self.assertEqual(len(d['scenarios']),25)
        self.assertEqual({(s['M'],s['M_delta']) for s in d['scenarios']},{(2,4)})
        self.assertEqual(d['scenarios'][0]['affected']['count'],0)
        self.assertEqual(d['scenarios'][-1]['affected']['count'],3)
        self.assertEqual(len({s['baseline_mean'] for s in d['scenarios']}),1)

    def test_zero_negative_missing_and_nonfinite_forecasts_stay_distinct(self):
        for value,known in ((0,True),(-20,True),(None,False),(float('nan'),False),(float('inf'),False)):
            raw = dict(FlexProjection=value,CptProjection=None)
            d = calculate(make_capture(('A',),updates={'A':raw}),['A']).data
            self.assertEqual(d['M'],int(known))
            self.assertEqual(d['M_delta'],int(known))
            if known:
                self.assertEqual(d['scenarios'][-1]['direct_change']['mean'],value)
            else:
                self.assertIsNone(d['scenarios'][-1]['direct_change']['mean'])

    def test_declared_missing_forecast_overrides_numeric_value(self):
        d = calculate(make_capture(('A',),updates={'A':dict(ProjectionSource='Missing forecast')}),['A']).data
        self.assertEqual((d['M'],d['M_delta']),(0,0))
        p = make_capture(updates={'A':dict(ProjectionSource=None)}).data['pool']['A']
        self.assertEqual(p['forecast_source'],'source not recorded')
        self.assertEqual(p['base_projection'],20)

    def test_captain_conflict_only_blocks_affected_forecast_roles(self):
        cap = make_capture(('A','A'),updates={'A':dict(CptProjection=31)})
        d = cap.data
        d['rosters'][0] = captain_a(cap).data['rosters'][0]
        report = calculate(RiskCapture.freeze(d),['A']).data
        self.assertEqual((report['N'],report['M'],report['M_delta']),(2,1,1))
        for value,valid in ((30+0.5e-6,True),(30+2e-6,False),(None,True)):
            report = calculate(captain_a(make_capture(('A',),updates={'A':dict(CptProjection=value)})),['A']).data
            self.assertEqual(report['M'],int(valid))

    def test_nonpositive_total_projection_share_is_undefined(self):
        d = calculate(make_capture(('A',),updates={'A':dict(FlexProjection=-100,CptProjection=-150)}),['A']).data
        self.assertIsNone(d['selected_projection_share'])
        self.assertGreater(d['scenarios'][-1]['stressed_mean'],d['scenarios'][-1]['baseline_mean'])

    def test_empirical_quantile_convention_and_empty_denominator(self):
        d = distribution([30,30,0,0])
        self.assertEqual((d['min'],d['median'],d['p90'],d['max']),(0,0,30,30))
        self.assertIsNone(distribution([])['mean'])
        d = calculate(make_capture(()),['A']).data
        self.assertEqual((d['R'],d['N'],d['U'],d['M']),(0,0,0,0))
        self.assertIsNone(d['target_exposures'][0]['any']['pct'])

    def test_repeated_occurrences_count_separately_from_unique_rosters(self):
        d = calculate(make_capture(('AB','AB','AB','')),['A']).data
        self.assertEqual((d['R'],d['N'],d['U']),(4,4,2))
        self.assertEqual(d['target_exposures'][0]['any']['pct'],75)

    def test_alternative_union_and_all_coexisting_are_not_fallback_claims(self):
        d = calculate(make_capture(('AB','AB','A','')),['A'],['B','O0']).data
        self.assertEqual(d['alternative_union']['count'],4)
        self.assertTrue(d['alternatives_coverage'][0]['all_coexist'])
        self.assertFalse(d['alternatives_coverage'][1]['all_coexist'])
        self.assertEqual(d['scenarios'][-1]['direct_change']['mean'],15)

    def test_known_zero_exposure_is_different_from_outside_pool(self):
        d = calculate(make_capture(('','')),['A'],['B','UNKNOWN']).data
        self.assertEqual(d['target_exposures'][0]['any']['pct'],0)
        self.assertIsNone(d['alternative_union']['pct'])
        self.assertFalse(d['alternatives_coverage'][1]['coverage']['applicable'])
        self.assertEqual(calculate(make_capture(),['UNKNOWN']).data['scenarios'],[])

    def test_zero_one_and_multiple_showdown_quarterbacks_preserve_exact_pairs(self):
        for qbs in ((),('A',),('A','O0')):
            updates = {key:dict(Position='QB') for key in qbs}
            d = calculate(make_capture(('AB',),updates=updates)).data
            self.assertEqual(len(d['qb_receivers']),len(qbs)*(6-len(qbs)))
            self.assertTrue(all(p['appearances']['count']==1 for p in d['qb_receivers']))
        self.assertEqual(d['teams'][0]['appearances']['count'],1)

    def test_missing_team_context_is_not_counted_as_absence(self):
        d = calculate(make_capture(updates={'A':dict(Team=None)})).data
        self.assertEqual(d['context']['team'],2)
        self.assertEqual(d['teams'][0]['appearances']['denominator'],2)
        d = calculate(make_capture(qualified=False),['A']).data
        self.assertEqual(d['context']['game'],0)
        self.assertEqual(d['stress_reason'],EXPORT_UNAVAILABLE)

    def test_outcomes_ownership_and_payouts_cannot_change_allowed_input_math(self):
        original = make_capture()
        altered = make_capture(updates={'A':dict(ActualPoints=999,Rank=1,Payout=1000,ProjOwnPct=99,sim_scenarios=[99])})
        self.assertEqual(original,altered)
        self.assertEqual(calculate(original,['A','B']),calculate(altered,['A','B']))
        before = copy.deepcopy(original.data)
        calculate(original,['A'],['B'])
        self.assertEqual(original.data,before)

    def test_default_copy_omits_identifiers_names_and_rosters(self):
        report = calculate(make_capture(),['A','B'])
        text = summary(report)
        for secret in ('Private Athlete','PRIVATE_INPUT','PRIVATE_ARCHIVE','PRIVATE_EXPORT','PRIVATE_CONTEST','PRIVATE_USER','PRIVATE_PATH','private.csv'):
            self.assertNotIn(secret,text)
        self.assertIn('Private Athlete',summary(report,True))
        self.assertIn('PRIVATE: player names',summary(report,True))

    def test_more_than_two_or_duplicate_targets_rejected(self):
        for targets in (('A','A'),('A','B','O0')):
            with self.assertRaises(ValueError):calculate(make_capture(),targets)


class PortfolioRiskRoleTests(unittest.TestCase):
    def role(self, **updates):
        raw = raw_player('A'); raw.update(updates)
        return role_evidence(raw,CAPTURE,KICKOFF)

    def test_role_age_24_hour_boundary_uses_historical_kickoff(self):
        self.assertEqual(self.role(LiveStatusUpdatedAt='2026-09-20T20:15:00-04:00')['state'],'timely')
        self.assertEqual(self.role(LiveStatusUpdatedAt='2026-09-20T20:14:59-04:00')['state'],'stale')
        self.assertEqual(self.role()['state'],'timely')

    def test_missing_naive_future_and_after_snapshot_role_times(self):
        for value in (None,'2026-09-21T17:00:00','2026-09-21T19:00:00-04:00','2026-09-22T00:00:00Z'):
            self.assertEqual(self.role(LiveStatusUpdatedAt=value)['state'],'unknown')

    def test_role_source_and_per_player_match_required_not_usage_or_news(self):
        for fields in (dict(InjurySource=None),dict(LiveStatusUpdatedAt=None,NFLUsageCheckedAt=CAPTURE),
                       dict(LiveStatusUpdatedAt=None,NFLNewsUpdatedAt=CAPTURE),dict(NFLDepthOrder=None),dict(NFLAvailability=None)):
            self.assertEqual(self.role(**fields)['state'],'unknown')

    def test_conflicting_availability_is_visible_without_certification(self):
        for fields in (dict(InjuryStatus='OUT'),dict(NFLActive=False),dict(NFLDepthOrder=2),dict(LiveStatusConflict=True)):
            self.assertEqual(self.role(**fields)['state'],'conflict')

    def test_non_qb_rotation_not_exclusive_backup_and_unrelated_alternative(self):
        cap = make_capture(updates={'B':dict(NFLDepthOrder=2,NFLAvailability='BACKUP 2')})
        d = calculate(cap,['A'],['B']).data
        self.assertEqual(d['capture']['pool']['B']['role']['group'],'Other depth roles / rotation')
        self.assertIn('not exclusive replacement',d['alternatives_coverage'][0]['relationship'])
        cap = make_capture(updates={'B':dict(Team='BBB',NFLDepthOrder=2,NFLAvailability='BACKUP 2')})
        self.assertIn('User-selected alternative',calculate(cap,['A'],['B']).data['alternatives_coverage'][0]['relationship'])

    def test_missing_forecast_does_not_erase_timely_role_or_known_exposure(self):
        d = calculate(make_capture(updates={'A':dict(FlexProjection=None)}),['A']).data
        self.assertEqual(d['capture']['pool']['A']['role']['state'],'timely')
        self.assertEqual(d['target_exposures'][0]['any']['count'],2)
        self.assertEqual(d['M_delta'],2)


if __name__=='__main__':unittest.main()
