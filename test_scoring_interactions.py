"""Exact same-draw summaries, legacy compatibility and qualified evidence accounting."""
import copy
import unittest
from unittest.mock import patch
from build_snapshots import fingerprint
from scoring_distributions import DistributionCapture, validate_distribution
from distribution_calibration import summarize, report_lines


class ScoringInteractionTests(unittest.TestCase):
    def players(self):
        return [dict(Name=name, FlexID=name, Team=team, Position=pos,
            GameInfo='BUF@MIA 09/10/2099 08:00PM ET') for name,team,pos in
            [('Kicker','BUF','K'),('Defense','MIA','DST'),('Runner','BUF','RB')]]

    def raw(self, players=None):
        c=DistributionCapture(players or self.players(),'showdown',4,7)
        for k,d,r in [(0,2,0),(2,0,4),(0,-2,0),(4,0,8)]:
            c.record(dict(Kicker=k,Defense=d,Runner=r))
        return c.finish(4)

    def record(self, raw=None):
        payload=dict(raw or self.raw(),schema=1,input_id='a'*64,model_version='b'*64)
        return dict(payload=payload,capture_id=fingerprint(payload))

    def evidence(self):
        value=self.record(); p=value['payload']
        return [dict(r,actual=0 if r['position']=='K' else 3,kind=p['kind'],model=p['model_version'],
            capture_id=value['capture_id'],captured_at=p['finished_at'],scenarios=4) for r in p['players']]

    def test_exact_zeros_include_negative_dst_without_rounding(self):
        rows={r['name']:r for r in self.raw()['players']}
        self.assertEqual([rows[n]['zero_count'] for n in ('kicker','defense','runner')],[2,2,2])
        c=DistributionCapture(self.players()[:1],'classic',2,1)
        c.record({'Kicker':1e-10});c.record({'Kicker':-1e-10})
        self.assertEqual(c.finish(2)['players'][0]['zero_count'],0)

    def test_exact_joint_counts_and_correlation_from_same_scenarios(self):
        raw=self.raw();validate_distribution(self.record(raw))
        pairs=[(r['name'],p) for r in raw['players'] for p in r.get('interactions',[])]
        self.assertEqual(len(pairs),3)
        same=next(p for _,p in pairs if p['relationship']=='same team')
        self.assertEqual((same['both_positive_count'],same['both_zero_count'],same['correlation']),(2,2,1))
        opposing=next(p for name,p in pairs if name=='defense' and p['name']=='kicker')
        self.assertEqual((opposing['both_positive_count'],opposing['both_zero_count']),(0,0))
        self.assertAlmostEqual(opposing['correlation'],0)

    def test_order_independent_pair_ownership_and_values(self):
        first=self.raw()['players'];second=self.raw(list(reversed(self.players())))['players']
        self.assertEqual(sorted(first,key=lambda r:r['name']),sorted(second,key=lambda r:r['name']))

    def test_constant_scores_have_unknown_correlation(self):
        c=DistributionCapture(self.players(),'classic',2,1)
        for _ in range(2):c.record(dict(Kicker=0,Defense=0,Runner=0))
        raw=c.finish(2);validate_distribution(self.record(raw))
        self.assertTrue(all(p['correlation'] is None for r in raw['players'] for p in r.get('interactions',[])))

    def test_missing_team_game_and_ambiguous_names_do_not_invent_pairs(self):
        for field,value in [('Team',''),('GameInfo','unknown')]:
            players=self.players()
            for p in players:p[field]=value
            self.assertFalse(any(r.get('interactions') for r in self.raw(players)['players']))
        players=self.players();players[2]['Name']='Kicker'
        raw=self.raw(players)
        self.assertFalse(any(r.get('interactions') for r in raw['players']))

    def test_pair_budget_withholds_all_interactions_but_keeps_zeros(self):
        with patch('scoring_interactions.MAX_PAIRS',1):raw=self.raw()
        self.assertFalse(any('interactions' in r for r in raw['players']))
        self.assertTrue(all('zero_count' in r for r in raw['players']))
        with patch('scoring_interactions.MAX_PAIR_SCENARIOS',1):raw=self.raw()
        self.assertFalse(any('interactions' in r for r in raw['players']))

    def test_partial_cancelled_or_missing_outcomes_never_publish_stats(self):
        c=DistributionCapture(self.players(),'showdown',2,1)
        c.record(dict(Kicker=0,Defense=0,Runner=0))
        self.assertEqual(c.finish(1)['status'],'not captured')
        c.record(dict(Kicker=0,Defense=float('nan')))
        self.assertEqual(c.finish(2)['status'],'not captured')

    def test_legacy_capture_is_readable_but_zero_evidence_unknown(self):
        raw=self.raw()
        for row in raw['players']:
            for k in ('zero_count','team','interactions','game'):row.pop(k,None)
        validate_distribution(self.record(raw))
        rows=self.evidence()
        for row in rows:
            row.pop('zero_count');row.pop('interactions',None)
        result=summarize(rows)
        self.assertEqual(result['interactions'],[])
        self.assertTrue(all(g['player_weighted']['zero_scores']['player_games']==0 for g in result['groups']))
        self.assertIn('quantiles cannot reconstruct','\n'.join(report_lines(result)))

    def test_modified_optional_statistics_rejected_even_with_recomputed_receipt(self):
        for change in ('negative','boolean','fraction','overflow','bad_pair','bad_relation','bad_count','bad_correlation','duplicate'):
            raw=self.raw();row=next(r for r in raw['players'] if r.get('interactions'));p=row['interactions'][0]
            if change in ('negative','boolean','fraction','overflow'):
                row['zero_count']={'negative':-1,'boolean':True,'fraction':1.2,'overflow':5}[change]
            elif change=='bad_pair':p['name']='unknown'
            elif change=='bad_relation':p['relationship']='same team' if p['relationship']=='opposing team' else 'opposing team'
            elif change=='bad_count':p['both_zero_count']=4
            elif change=='bad_correlation':p['correlation']=float('inf')
            else:row['interactions'].append(copy.deepcopy(p))
            with self.subTest(change=change),self.assertRaises(ValueError):validate_distribution(self.record(raw))

    def test_report_zeros_joint_evidence_deduplicates_and_excludes_conflicts(self):
        rows=self.evidence();one=summarize(rows);repeated=summarize(rows*150)
        self.assertEqual(one['groups'],repeated['groups']);self.assertEqual(one['interactions'],repeated['interactions'])
        self.assertEqual(sum(p['pair_games'] for p in one['interactions']),3)
        all_group=next(g for g in one['groups'] if g['axis']=='all')['player_weighted']['zero_scores']
        self.assertEqual(all_group['sim_pct'],50);self.assertAlmostEqual(all_group['actual_pct'],100/3)
        self.assertIn('Joint outcomes','\n'.join(report_lines(one)))
        conflict=dict(rows[0],actual=9)
        self.assertEqual(sum(p['pair_games'] for p in summarize(rows+[conflict])['interactions']),1)

    def test_different_captures_games_models_and_formats_never_create_joint_observations(self):
        for field in ('capture_id','game','model','kind'):
            rows=self.evidence();rows[0][field]='classic' if field=='kind' else 'c'*64
            result=summarize(rows)
            self.assertLessEqual(sum(p['pair_games'] for p in result['interactions']),1)


class QualifiedInteractionIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from test_distribution_build_selection import SIMBuildSelectionTests
        self.tmp=tempfile.TemporaryDirectory()
        SIMBuildSelectionTests.build_fixture(self,'classic')

    from test_distribution_build_selection import SIMBuildSelectionTests as _fixture
    snapshot=_fixture.snapshot
    capture=_fixture.capture

    def tearDown(self):self.tmp.cleanup()

    def test_automatic_discovery_persists_stats_and_warm_reuses_them(self):
        import sqlite3
        from contextlib import closing
        import calibration_capture_discovery as discovery
        discovery.refresh(self.db)
        with closing(sqlite3.connect(self.db)) as conn:data=discovery.read(conn)
        self.assertTrue(all(r['zero_count']==0 for r in data['rows']))
        report=summarize(data['rows'])
        self.assertTrue(report['interactions'])
        self.assertTrue(all(p['sim_both_positive_pct']==100 for p in report['interactions']))
        self.assertEqual(discovery.refresh(self.db)['reused'],1)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(data['rows'],discovery.read(conn)['rows'])

    def test_captured_team_conflict_cannot_pass_exact_snapshot_qualification(self):
        import json
        import sqlite3
        from contextlib import closing
        from distribution_build_selection import catalog
        value=__import__('json').loads(self.capture_path.read_text(encoding='utf-8'))
        # Remove joint stats so internal consistency is not the reason for rejection.
        for row in value['payload']['players']:row.pop('interactions',None)
        row=value['payload']['players'][0]
        row['team']='SEA' if row['team']=='NE' else 'NE'
        value['capture_id']=fingerprint(value['payload'])
        target=self.capture_path.with_name(value['payload']['input_id']+'-'+value['capture_id']+'.json')
        target.write_text(json.dumps(value),encoding='utf-8');self.capture_path.unlink()
        with closing(sqlite3.connect(self.db)) as conn:data=catalog(conn,self.ident,self.root)
        self.assertEqual(data['candidates'],[])
        self.assertTrue(any('identity differs' in r['reason'] for r in data['rejected']))

    def test_explicit_comparison_preserves_stats(self):
        import sqlite3
        from contextlib import closing
        from distribution_validation import compare_distributions
        from distribution_build_selection import catalog, save_choice
        with closing(sqlite3.connect(self.db)) as conn:data=catalog(conn,self.ident,self.root)
        save_choice(self.db,self.ident,data['candidates'][0])
        with closing(sqlite3.connect(self.db)) as conn:
            result=compare_distributions(conn,self.ident,self.snap['input_id'],'classic',
                data['scores'],self.root)
        self.assertEqual(result['status'],'matched')
        self.assertTrue(summarize(result['rows'])['interactions'])


if __name__=='__main__':unittest.main()
