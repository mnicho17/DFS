"""Personal context uses targeted indexed receipts, never an all-results scan."""
from test_environment import install
install()
from contextlib import closing
import copy,json,sqlite3,unittest
from unittest.mock import patch
from opponent_history import ensure_tables,VERSION
from historical_contest_comparison import personal_observations,contest_record


def stats(pool=1):
    return dict(entries=20,readable_rosters=20,captain_pool=pool,constructions=dict(known_entries=20,unknown_entries=0))


class PersonalContextLookupTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.addCleanup(self.conn.close);ensure_tables(self.conn)
    def add(self,key='a',source='r',salary='s',user='me',data=None,audit=None,version=VERSION,kind='showdown'):
        self.conn.execute('INSERT INTO opponent_contests(contest_key,platform,result_hash,salary_hash,name,format,complete,version,audit_json) VALUES(?,?,?,?,?,?,?,?,?)',
            (key,'draftkings',source,salary,'Example',kind,0,version,json.dumps(audit if audit is not None else dict(conflicting_entry_ids_excluded=0))))
        self.conn.execute('INSERT INTO opponent_user_contest_stats VALUES(?,?,?,?,?,?,?)',
            (key,'dk:'+user,20,20,1,None,json.dumps(data if data is not None else stats())))
    def read(self,user='me',**kwargs):return personal_observations(self.conn,user,**kwargs)
    def test_exact_username_normalization_and_parameterized_lookup(self):
        self.add();self.add(key='b',source='other',user='someone else')
        r=self.read(' ME (2/20) ');self.assertEqual(set(r['receipts']),{('r','s')})
        self.assertEqual(self.read("me' OR 1=1 --")['receipts'],{})
        self.assertEqual(self.read('')['receipts'],{})
    def test_large_unrelated_history_cannot_exhaust_personal_receipt_budget(self):
        self.add();self.conn.execute('CREATE TABLE historical_results(result_id INTEGER,import_id TEXT,entry_name TEXT,raw_json TEXT)')
        self.conn.execute("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<200001) INSERT INTO historical_results SELECT n,'other','Other','{}' FROM x")
        def authorize(action,table,*args):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table in ('historical_results','opponent_entries','opponent_entry_slots') else sqlite3.SQLITE_OK
        self.conn.set_authorizer(authorize)
        try:r=self.read(limit=1)
        finally:self.conn.set_authorizer(None)
        self.assertTrue(r['complete']);self.assertEqual(r['receipts'][('r','s')]['captain_pool'],1)
    def test_indexed_receipts_work_without_a_raw_results_table(self):
        self.add();self.assertEqual(self.read()['receipts'][('r','s')]['rows'],20)
    def test_bound_counts_matching_username_receipts_only_and_discards_partial(self):
        self.add();self.add(key='b',source='b');self.add(key='c',source='c',user='another')
        self.assertTrue(self.read(limit=2)['complete'])
        r=self.read(limit=1);self.assertFalse(r['complete']);self.assertEqual(r['receipts'],{});self.assertEqual(r['imports'],{})
    def test_ambiguous_same_revision_receipts_withheld_even_if_identical(self):
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                self.conn.execute('DELETE FROM opponent_user_contest_stats');self.conn.execute('DELETE FROM opponent_contests')
                for key in (('a','b') if not reverse else ('b','a')):self.add(key=key)
                self.assertEqual(self.read()['receipts'],{})
    def test_invalid_duplicate_receipt_cannot_leave_the_valid_copy_trusted(self):
        self.add();self.add(key='b',data=stats(pool=True));self.assertEqual(self.read()['receipts'],{})
    def test_old_versions_and_classic_do_not_supply_showdown_context(self):
        self.add(version=VERSION-1);self.add(key='b',source='b',kind='classic');self.assertEqual(self.read()['receipts'],{})
    def test_missing_partial_or_noninteger_identity_counts_stay_unknown(self):
        for change in ('pool_bool','pool_float','known_bool','entries_float','missing','unknown','partial'):
            with self.subTest(change=change):
                self.conn.execute('DELETE FROM opponent_user_contest_stats');self.conn.execute('DELETE FROM opponent_contests')
                s=stats()
                if change=='pool_bool':s['captain_pool']=True
                elif change=='pool_float':s['captain_pool']=1.0
                elif change=='known_bool':s['constructions']['known_entries']=True
                elif change=='entries_float':s['entries']=20.0
                elif change=='missing':s.pop('entries')
                elif change=='unknown':s['constructions']['unknown_entries']=1
                else:s['constructions']['known_entries']=19
                self.add(data=s);self.assertEqual(self.read()['receipts'],{})
    def test_conflicting_or_missing_entry_validation_receipt_stays_unknown(self):
        for a in ({},{'conflicting_entry_ids_excluded':1},{'conflicting_entry_ids_excluded':False}):
            self.conn.execute('DELETE FROM opponent_user_contest_stats');self.conn.execute('DELETE FROM opponent_contests')
            self.add(audit=a);self.assertEqual(self.read()['receipts'],{})
    def test_malformed_and_oversized_payloads_are_not_parsed_as_context(self):
        self.add()
        for encoded in ('bad','[]',json.dumps({'pad':'x'*65536})):
            self.conn.execute('UPDATE opponent_user_contest_stats SET stats_json=?',(encoded,));self.assertEqual(self.read()['receipts'],{})
    def test_context_stays_bound_to_exact_result_salary_and_personal_denominator(self):
        self.add();r=self.read()
        data=dict(results_evidence=dict(source_hash='r'),salary_evidence=dict(revision_hash='s',format='showdown',dates=['2026-09-21'],games=['AAA@BBB']))
        payload=dict(version=3,cohorts={'user:me':dict(counts=dict(entry_rows=20,supported_rows=20),features={})})
        self.assertEqual(contest_record(data,{},payload,'me',r)['context'],'Single Captain; user-assumed lock')
        for key in ('source','salary','count'):
            d=copy.deepcopy(data);p=copy.deepcopy(payload)
            if key=='source':d['results_evidence']['source_hash']='other'
            elif key=='salary':d['salary_evidence']['revision_hash']='other'
            else:p['cohorts']['user:me']['counts']['entry_rows']=21
            self.assertEqual(contest_record(d,{},p,'me',r)['context'],'Captain coverage/settings unknown')
    def test_reader_preserves_transaction_and_database_without_csv_access(self):
        self.add();before=list(self.conn.iterdump());in_transaction=self.conn.in_transaction
        with patch('builtins.open',side_effect=AssertionError('No source scan')):self.read()
        self.assertEqual(self.conn.in_transaction,in_transaction);self.assertEqual(list(self.conn.iterdump()),before)
    def test_missing_index_tables_leave_context_unknown_without_creating_schema(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            self.assertEqual(personal_observations(conn,'me'),dict(complete=True,imports={},receipts={}))
            self.assertEqual(list(conn.execute('SELECT name FROM sqlite_master')),[])

if __name__=='__main__':unittest.main()
