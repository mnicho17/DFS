from test_environment import install
install()
from contextlib import closing
import copy,unittest
from unittest.mock import patch
import analysis_imports as ai
from opponent_history import _connect,index_contest
from test_hindsight_evidence import SourceFixture
from test_hindsight_solver import athletes
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db,source_bytes
from portfolio_pattern_audit import audit_peers,features,METRICS,PatternAuditLimit
from winner_portfolio_review import review_winners,comparisons,peer_support_check,render_overview
from historical_pattern_checks import pattern_checks,METRICS as ALL_METRICS

class PortfolioPatternAuditTests(unittest.TestCase):
    def fixture(self,kind='showdown',split=None,captain_wr=False,duplicate=False):
        pool=athletes(kind)
        if split:
            for i,p in enumerate(pool):p['team']='SEA' if i<split else 'NE'
        def edit(rows):
            results(duplicate=duplicate)(rows)
            if captain_wr:
                h=rows[0]
                for r in rows[1:101]:r[h.index('Lineup')]='CPT C FLEX A FLEX B FLEX D FLEX E FLEX F'
        f=SourceFixture(kind,pool=pool,edit_results=edit);self.addCleanup(f.close);index_contest(f.db,f.source['hash']);return f
    def audit(self,f):
        key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        with closing(_connect(f.db,True)) as c:return audit_peers(c,key,f.salary['manifest']['players'],{'dk:other':50})['dk:other']
    def test_team_splits_cover_six_slots_and_keep_captain_orientation_separate(self):
        for split,metric in ((5,'split_5_1_pct'),(4,'split_4_2_pct'),(3,'split_3_3_pct')):
            with self.subTest(split=split):
                f=self.fixture(split=split);r=review_winners(f.db,'2026-09-22');p=r['contests'][0]['winners'][0]
                self.assertEqual(p['metrics'][metric],100);self.assertEqual(p['peer_medians'][metric],100)
                self.assertEqual(sum(p['metrics'][k] for k in METRICS[:3]),100)
    def test_qb_opposing_dst_includes_captain_and_flex_roles(self):
        for captain_wr in (False,True):
            with self.subTest(captain_wr=captain_wr):
                f=self.fixture(captain_wr=captain_wr);p=review_winners(f.db,'2026-09-22')['contests'][0]['winners'][0]
                self.assertEqual(p['metrics']['qb_opposing_dst_pct'],100);self.assertEqual(p['peer_medians']['qb_opposing_dst_pct'],100)
                self.assertEqual(p['metrics']['all_four_pct'],0)
    def test_same_team_defense_does_not_count_as_opposing(self):
        def row(pos,team,i):return (str(i),'CPT' if i==0 else 'FLEX',dict(position=pos,team=team))
        slots=[row(pos,team,i) for i,(pos,team) in enumerate([('WR','A'),('QB','A'),('DST','A'),('K','B'),('RB','B'),('TE','B')])]
        self.assertEqual(features(slots)[3:],(0,1))
    def test_identical_duplicate_entries_keep_legitimate_weights_and_are_readonly(self):
        f=self.fixture(duplicate=True);before=logical_db(f.db);sources=source_bytes(f.root)
        r=review_winners(f.db,'2026-09-22');c=r['contests'][0];p=c['winners'][0]
        self.assertEqual(p['entries'],50);self.assertEqual(c['peer_pattern_audit']['entries'],50)
        self.assertEqual(p['metric_peer_users']['qb_opposing_dst_pct'],1)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
    def test_conflicting_explicit_slot_ids_never_borrow_name_only_cache_resolution(self):
        f=self.fixture();answers=[]
        for eid in ('1050','1099'):
            f.change("UPDATE opponent_entry_slots SET original_label='A' WHERE role='CPT'")
            f.change("UPDATE opponent_entry_slots SET original_label='A (1101)' WHERE entry_id=? AND role='CPT'",(eid,))
            r=self.audit(f);self.assertEqual(r['known'],49);self.assertEqual(r['unknown'],1)
            self.assertTrue(all(v is None for v in r['metrics'].values()));answers.append(r)
        self.assertEqual(answers[0],answers[1])
    def test_missing_or_extra_slots_and_unknown_metadata_do_not_become_zero_patterns(self):
        for alteration in ("DELETE FROM opponent_entry_slots WHERE entry_id='1050' AND ordinal=3", "UPDATE opponent_entries SET metadata_complete=0 WHERE entry_id='1050'", "INSERT INTO opponent_entry_slots SELECT contest_key,entry_id,6,role,original_label,player_id,player_name,position,team,salary FROM opponent_entry_slots WHERE entry_id='1050' AND ordinal=5"):
            with self.subTest(alteration=alteration):
                f=self.fixture();f.change(alteration);r=self.audit(f)
                self.assertEqual(r['unknown'],1);self.assertTrue(all(v is None for v in r['metrics'].values()))
    def test_unresolved_peer_portfolio_is_withheld_in_full_with_coverage_reported(self):
        f=self.fixture();f.change("UPDATE opponent_entry_slots SET player_id='999999' WHERE entry_id='1050' AND ordinal=1")
        c=review_winners(f.db,'2026-09-22')['contests'][0];p=c['winners'][0]
        self.assertEqual(p['peer_users'],0);self.assertTrue(all(v is None for v in p['peer_medians'].values()))
        self.assertEqual(c['peer_pattern_audit']['unknown_entries'],1);self.assertEqual(c['peer_pattern_audit']['excluded_users'],1)
    def test_cache_eviction_does_not_change_pattern_weights(self):
        f=self.fixture();f.change("UPDATE opponent_entry_slots SET original_label='G',player_id='106',player_name='G',position='WR',team='SEA' WHERE entry_id='1050' AND ordinal=1")
        with patch('portfolio_pattern_audit.MAX_CACHE',1):r=self.audit(f)
        self.assertEqual(r['known'],50);self.assertEqual(r['metrics']['split_4_2_pct'],2);self.assertEqual(r['metrics']['split_3_3_pct'],98)
    def test_salary_metadata_conflict_does_not_use_cached_construction_summary(self):
        f=self.fixture();f.change("UPDATE opponent_entry_slots SET salary=1 WHERE entry_id='1050' AND ordinal=1")
        self.assertEqual(self.audit(f)['unknown'],1)
    def test_classic_metrics_are_unavailable_instead_of_false_showdown_percentages(self):
        f=self.fixture('classic');c=review_winners(f.db,'2026-09-22',contest_format='classic')['contests'][0];p=c['winners'][0]
        self.assertIsNone(c['peer_pattern_audit'])
        self.assertTrue(all(p['metrics'][k] is None and p['peer_medians'][k] is None for k in METRICS))
    def test_budget_failure_aborts_review_instead_of_excluding_an_unfavorable_contest(self):
        f=self.fixture()
        for limit in ('MAX_USERS','MAX_RESOLUTIONS'):
            with self.subTest(limit=limit),patch('portfolio_pattern_audit.'+limit,0):
                with self.assertRaises(PatternAuditLimit):review_winners(f.db,'2026-09-22')
    def test_expected_count_mismatch_and_cancellation_are_rejected(self):
        f=self.fixture();key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        with closing(_connect(f.db,True)) as c:
            with self.assertRaisesRegex(ValueError,'count differs'):audit_peers(c,key,f.salary['manifest']['players'],{'dk:other':49})
            with self.assertRaises(ai.ImportCancelled):audit_peers(c,key,f.salary['manifest']['players'],{'dk:other':50},cancelled=lambda:True)
    def test_cross_game_and_sensitivity_support_are_metric_specific(self):
        metric='qb_opposing_dst_pct';a={k:None for k in ALL_METRICS};b=dict(a);a[metric]=100;b[metric]=0
        p=dict(user_key='dk:user',peer_users=10,metric_peer_users={metric:1},metrics=a,peer_medians=b,current_top_cutoff=True,peer_top_rate=.5)
        c=dict(format='showdown',preset='150-Max',date='2026-09-21',slate=['showdown','2026-09-21',['A@B']],winners=[p])
        r=dict(review_mode='prior_qualified',contests=[c])
        self.assertEqual(pattern_checks(r,5),{})
        self.assertIsNone(comparisons(r,5)['showdown / 150-Max']['metrics'][metric]['winner'])
        self.assertIsNone(peer_support_check(r,5)['comparisons']['showdown / 150-Max']['metrics'][metric]['winner'])

    def test_captain_on_minority_team_still_counts_as_five_one_construction(self):
        f=self.fixture(split=5)
        # Swap only the Captain/FLEX roles of athletes A and F in one peer copy,
        # retaining the exact role-specific IDs and salaries from the catalog.
        f.change("UPDATE opponent_entry_slots SET role='FLEX',player_id='100',salary=6000 WHERE entry_id='1050' AND ordinal=0")
        f.change("UPDATE opponent_entry_slots SET role='CPT',player_id='1105',salary=9000 WHERE entry_id='1050' AND ordinal=5")
        self.assertEqual(self.audit(f)['metrics']['split_5_1_pct'],100)
    def test_older_reports_without_new_metrics_do_not_invent_construction_percentages(self):
        f=self.fixture();r=review_winners(f.db,'2026-09-22')
        for p in r['contests'][0]['winners']:
            for metric in METRICS:
                p['metrics'].pop(metric);p['peer_medians'].pop(metric);p['metric_peer_users'].pop(metric)
        for metric in METRICS:self.assertIsNone(comparisons(r)['showdown / Unclassified']['metrics'][metric]['winner'])
        self.assertIn('CONTEST WINNERS',render_overview(r))

    def test_matching_explicit_ids_preserve_identical_copy_weights(self):
        f=self.fixture();f.change("UPDATE opponent_entry_slots SET original_label='A (1100)' WHERE role='CPT'")
        r=self.audit(f);self.assertEqual(r['known'],50);self.assertEqual(r['unknown'],0)
        self.assertEqual(r['metrics']['qb_opposing_dst_pct'],100)

if __name__=='__main__':unittest.main()
