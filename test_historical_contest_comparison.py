"""Matched denominators, conservative lock context and overlap-safe weighting."""
import copy
from contextlib import closing
import itertools
import json
import sqlite3
import unittest

from historical_contest_comparison import contest_record, context, matched_summary, personal_observations, report_lines, slate_blocks


def contest(source='a',day='2026-09-21',games=('NE@SEA',),yours=10,field=20,top1=5,n=100):
    feature='Kicker/defense slots: 2'
    cohorts={key:dict(counts=dict(entry_rows=n,supported_rows=n),features={feature:value*n/100})
             for key,value in [('yours',yours),('field',field),('top1',top1)]}
    return dict(source=source,name=source,date=day,games=games,cohorts=cohorts,
                context='Multiple Captains; settings unknown',captain_pool=2,version=2,rank_basis='observed source-row rank cutoff; completeness unverified')


class ContestComparisonTests(unittest.TestCase):
    def test_equal_slate_weight_avoids_large_contest_and_repeated_contest_dominance(self):
        records=[contest('large',yours=0,field=100,n=1000000),
                 contest('same-slate',yours=100,field=0,n=10),
                 contest('new-slate',day='2026-09-28',yours=100,field=0,n=10)]
        result=matched_summary(records,'Kicker/defense slots: 2','field')
        self.assertEqual(result['slates'],2)
        self.assertEqual(result['contests'],3)
        self.assertEqual(result['yours'],75)
        self.assertEqual(result['comparator'],25)
        self.assertEqual(result['gap'],50)

    def test_overlapping_game_sets_merge_transitively_and_order_independently(self):
        records=[contest('a',games=('A@B','C@D')),contest('b',games=('E@F',)),
                 contest('bridge',games=('C@D','E@F')),contest('other-date',day='2026-09-28',games=('A@B',))]
        for sequence in itertools.permutations(records):
            blocks,excluded=slate_blocks(sequence)
            self.assertEqual(len(blocks),2)
            self.assertEqual(excluded,0)
            self.assertEqual(sorted(len(b['contests']) for b in blocks),[1,3])

    def test_missing_date_or_game_identity_is_excluded_without_inventing_blocks(self):
        result=matched_summary([contest('a',day=None),contest('b',games=()),contest('c')],'Kicker/defense slots: 2','field')
        self.assertEqual(result['slates'],1)
        self.assertEqual(result['contests'],1)
        self.assertEqual(result['excluded'],2)

    def test_comparator_and_user_must_share_contributing_contests(self):
        records=[contest('a',yours=30,field=10),contest('b',field=100)]
        records[1]['cohorts'].pop('yours')
        result=matched_summary(records,'Kicker/defense slots: 2','field')
        self.assertEqual(result['contests'],1)
        self.assertEqual(result['gap'],20)
        records[0]['cohorts'].pop('top1')
        self.assertIsNone(matched_summary(records,'Kicker/defense slots: 2','top1')['gap'])

    def test_single_captain_assumption_requires_complete_matching_personal_coverage(self):
        group=dict(counts=dict(entry_rows=20,supported_rows=20))
        observation=dict(rows=20,readable=20,captains={'name:a'})
        self.assertEqual(context('showdown',group,observation,verified=True),('Single Captain; user-assumed lock',1))
        for change in [dict(rows=19),dict(readable=19)]:
            self.assertEqual(context('showdown',group,dict(observation,**change),verified=True)[0],'Captain coverage/settings unknown')
        self.assertEqual(context('showdown',group,observation,False,verified=True)[0],'Captain coverage/settings unknown')
        group['counts']['supported_rows']=19
        self.assertEqual(context('showdown',group,observation,verified=True)[0],'Captain coverage/settings unknown')

    def test_multiple_captains_and_single_entry_never_establish_normal_sim(self):
        group=dict(counts=dict(entry_rows=3,supported_rows=3))
        observation=dict(rows=3,readable=3,captains={'a','b','c'})
        self.assertEqual(context('showdown',group,observation,verified=True),('Multiple Captains; settings unknown',3))
        self.assertEqual(context('showdown',dict(counts=dict(entry_rows=1,supported_rows=1)),dict(rows=1,readable=1,captains={'a'}),verified=True),('Single submitted lineup; settings unknown',1))

    def test_raw_personal_rosters_never_establish_verified_context(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE historical_results(result_id INTEGER,import_id TEXT,entry_name TEXT,raw_json TEXT)')
            roster='CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'
            conn.executemany('INSERT INTO historical_results VALUES(?,?,?,?)',[(1,'a','ME (1/20)',json.dumps({'Lineup':roster})),(2,'a','me',json.dumps({'lineup':roster})),(3,'a','other','{}')])
            value=personal_observations(conn,'Me',limit=2)
            self.assertTrue(value['complete']);self.assertEqual(value['imports'],{});self.assertEqual(value['receipts'],{})

    def test_report_stratifies_context_and_labels_observed_cutoffs_and_no_causal_claim(self):
        a=contest('a');b=contest('b',yours=90)
        b['context']='Single Captain; user-assumed lock'
        text='\n'.join(report_lines(dict(format='showdown',contests=[a,b])))
        self.assertIn('Context: Multiple Captains; settings unknown',text)
        self.assertIn('Context: Single Captain; user-assumed lock',text)
        self.assertIn('observed source-row rank cutoff; completeness unverified',text)
        self.assertIn('not a claim of statistical independence',text)
        self.assertIn('No generation limits are changed',text)

    def test_classic_report_does_not_include_captain_or_showdown_team_split_metrics(self):
        text='\n'.join(report_lines(dict(format='classic',contests=[contest()])))
        self.assertNotIn('| Captain position:',text)
        self.assertNotIn('| Team split: 3–3',text)
        self.assertIn('| QB same-team WR/TE:',text)

    def test_unverified_stored_rosters_cannot_establish_lock_context(self):
        group=dict(counts=dict(entry_rows=20,supported_rows=20))
        self.assertEqual(context('showdown',group,dict(rows=20,readable=20,captains={'a'}))[0],'Captain coverage/settings unknown')

    def test_context_receipt_is_bound_to_exact_results_and_salary_revisions(self):
        data=dict(import_id='import',results_evidence=dict(source_hash='r'),salary_evidence=dict(revision_hash='s',format='showdown',dates=['2026-09-21'],games=['NE@SEA']))
        payload=dict(version=2,cohorts={'user:me':dict(counts=dict(entry_rows=20,supported_rows=20),features={})})
        observed=dict(complete=True,receipts={('r','s'):dict(rows=20,readable=20,captain_pool=1)})
        self.assertEqual(contest_record(data,{},payload,'ME',observed)['context'],'Single Captain; user-assumed lock')
        data['salary_evidence']['revision_hash']='different'
        self.assertEqual(contest_record(data,{},payload,'me',observed)['context'],'Captain coverage/settings unknown')

    def test_normalized_receipts_reject_conflicts_incomplete_metadata_and_old_versions(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE historical_results(result_id INTEGER,import_id TEXT,entry_name TEXT,raw_json TEXT)')
            conn.execute("CREATE TABLE opponent_contests(contest_key TEXT,result_hash TEXT,salary_hash TEXT,audit_json TEXT,version INTEGER,format TEXT DEFAULT 'showdown')")
            conn.execute('CREATE TABLE opponent_user_contest_stats(contest_key TEXT,user_key TEXT,entries INTEGER,readable INTEGER,stats_json TEXT)')
            stats=json.dumps(dict(entries=20,readable_rosters=20,captain_pool=1,constructions=dict(known_entries=20,unknown_entries=0)))
            conn.execute('INSERT INTO opponent_contests(contest_key,result_hash,salary_hash,audit_json,version) VALUES(?,?,?,?,?)',('a','r','s',json.dumps(dict(conflicting_entry_ids_excluded=0)),2))
            conn.execute('INSERT INTO opponent_user_contest_stats VALUES(?,?,?,?,?)',('a','dk:me',20,20,stats))
            self.assertIn(('r','s'),personal_observations(conn,'ME')['receipts'])
            conn.execute("UPDATE opponent_contests SET audit_json=?",(json.dumps(dict(conflicting_entry_ids_excluded=1)),))
            self.assertEqual(personal_observations(conn,'me')['receipts'],{})
            conn.execute('UPDATE opponent_contests SET audit_json=?,version=1',(json.dumps(dict(conflicting_entry_ids_excluded=0)),))
            self.assertEqual(personal_observations(conn,'me')['receipts'],{})
            conn.execute('UPDATE opponent_contests SET version=2')
            conn.execute('UPDATE opponent_user_contest_stats SET readable=19')
            self.assertEqual(personal_observations(conn,'me')['receipts'],{})
            conn.execute('UPDATE opponent_user_contest_stats SET readable=20,stats_json=?',(json.dumps(dict(captain_pool=1,constructions=dict(known_entries=19))),))
            self.assertEqual(personal_observations(conn,'me')['receipts'],{})


if __name__=='__main__':
    unittest.main()
