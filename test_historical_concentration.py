"""Revision-bound cached concentration must never imply independent outcomes."""
from test_environment import install
install()
import copy,json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
from historical_concentration import observations,attach,report_lines,build_receipt,CACHE_KEY,cached_receipt
from opponent_history import ensure_tables,VERSION


def audit(entries=3,readable=3):
    return dict(entry_rows=entries,accepted_entries=entries,readable_rosters=readable,
                identical_duplicate_rows=0,conflicting_entry_ids_excluded=0)


def stats(entries=3,readable=3,unique=2,shared=1,known=None):
    return dict(entries=entries,readable_rosters=readable,unique_lineups=unique if readable else None,
        repeated_entries=readable-unique if readable else None,entries_shared_with_other_users=shared if readable else None,
        constructions=dict(known_entries=readable if known is None else known,unknown_entries=entries-(readable if known is None else known)))


class ConcentrationTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');ensure_tables(self.conn);self.addCleanup(self.conn.close)
    def add(self,key='one',source='r',salary='s',version=VERSION,a=None,members=None,kind='showdown'):
        a=copy.deepcopy(a if a is not None else audit())
        a[CACHE_KEY]=build_receipt(a,members if members is not None else [stats()])
        self.conn.execute('INSERT INTO opponent_contests(contest_key,platform,result_hash,salary_hash,name,format,complete,version,audit_json) VALUES(?,?,?,?,?,?,?,?,?)',
            (key,'draftkings',source,salary,'Example',kind,0,version,json.dumps(a)))
        for i,s in enumerate(members if members is not None else [stats()]):
            self.conn.execute('INSERT INTO opponent_user_contest_stats VALUES(?,?,?,?,?,?,?)',
                (key,str(i),s['entries'],s['readable_rosters'],1,None,json.dumps(s)))
    def receipt(self):return observations(self.conn)['receipts'][('r','s')]
    def test_valid_repeats_remain_accepted_and_counts_overlap(self):
        self.add();r=self.receipt()
        self.assertIsNone(r['reason']);self.assertEqual((r['entries'],r['lineup_groups'],r['repeated'],r['shared']),(3,2,1,1))
    def test_entrant_groups_are_summed_without_claiming_global_uniqueness(self):
        self.add(a=audit(6,6),members=[stats(),stats()]);r=self.receipt()
        self.assertEqual((r['entries'],r['users'],r['lineup_groups']),(6,2,4))
    def test_readable_but_unmatched_salary_metadata_is_separate(self):
        self.add(members=[stats(known=1)]);r=self.receipt()
        self.assertEqual((r['readable'],r['metadata']),(3,1));self.assertIsNone(r['reason'])
    def test_unreadable_is_unknown_concentration_and_true_zero_is_retained(self):
        self.add(a=audit(2,0),members=[stats(2,0,0,0)]);r=self.receipt()
        self.assertEqual((r['readable'],r['repeated']),(0,0));self.assertIsNone(r['reason'])
        self.conn.execute('DELETE FROM opponent_user_contest_stats');self.conn.execute('DELETE FROM opponent_contests')
        self.add(members=[stats(unique=3,shared=0)]);self.assertEqual(self.receipt()['repeated'],0)
    def test_invalid_partial_and_boolean_counts_withhold_entire_source(self):
        for field,value in [('unique_lineups',True),('repeated_entries',-1),('entries_shared_with_other_users',4),('entries',3.0)]:
            with self.subTest(field=field):
                self.conn.execute('DELETE FROM opponent_user_contest_stats');self.conn.execute('DELETE FROM opponent_contests')
                s=stats();s[field]=value;self.add(members=[s]);self.assertTrue(self.receipt()['reason'])
    def test_missing_and_inconsistent_metadata_or_receipt_stays_unavailable(self):
        for change in ('missing','metadata','audit','partial'):
            with self.subTest(change=change):
                self.conn.execute('DELETE FROM opponent_user_contest_stats');self.conn.execute('DELETE FROM opponent_contests')
                s=stats();a=audit()
                if change=='missing':s.pop('constructions')
                elif change=='metadata':s['constructions']['known_entries']=4
                elif change=='audit':a['accepted_entries']=2
                else:a=audit(6,6)
                self.add(a=a,members=[s]);self.assertTrue(self.receipt()['reason'])
    def test_old_and_missing_stats_are_explained(self):
        self.add(version=VERSION-1);self.assertIn('Older',self.receipt()['reason'])
        self.conn.execute('DELETE FROM opponent_user_contest_stats');self.conn.execute('DELETE FROM opponent_contests')
        self.add(members=[]);self.assertIn('totals',self.receipt()['reason'])
    def test_conflicting_indexed_receipts_are_withheld(self):
        self.add();self.add(key='two');self.assertIn('Conflicting',self.receipt()['reason'])
    def test_read_bound_discards_all_partial_totals(self):
        self.add();self.add(key='two',source='other');r=observations(self.conn,limit=1)
        self.assertFalse(r['complete']);self.assertEqual(r['receipts'],{});self.assertIn('no partial',r['reason'])
    def test_cancellation_propagates_without_partial_publication(self):
        import analysis_imports as ai
        self.add()
        with self.assertRaises(ai.ImportCancelled):observations(self.conn,cancelled=lambda:True)
    def test_no_tables_no_schema_creation_and_no_entry_or_csv_scan(self):
        other=sqlite3.connect(':memory:');self.addCleanup(other.close)
        self.assertEqual(observations(other)['receipts'],{})
        self.assertEqual(list(other.execute("SELECT name FROM sqlite_master")),[])
        self.add();before=list(self.conn.iterdump());queries=[];self.conn.set_trace_callback(queries.append)
        with patch('builtins.open',side_effect=AssertionError('No CSV scan')):r=observations(self.conn)
        self.conn.set_trace_callback(None)
        self.assertEqual(list(self.conn.iterdump()),before)
        self.assertFalse(any('FROM opponent_entries' in q or 'opponent_entry_slots' in q or 'opponent_user_contest_stats' in q for q in queries))
        self.assertIsNone(r['receipts'][('r','s')]['reason'])
    def test_attach_requires_exact_revision_and_format(self):
        self.add();obs=observations(self.conn)
        data=dict(results_evidence=dict(source_hash='r'),salary_evidence=dict(revision_hash='different',format='showdown'))
        c={};attach(c,data,obs);self.assertIn('No current',c['concentration']['reason'])
        data['salary_evidence']['revision_hash']='s';attach(c,data,obs);self.assertIsNone(c['concentration']['reason'])
        data['salary_evidence']['format']='classic';attach(c,data,obs);self.assertIn('format differs',c['concentration']['reason'])
    def test_panel_labels_scope_denominators_repeat_semantics_and_sparse_blocks(self):
        self.add();r=self.receipt()
        c=dict(name='Example | source',source='r',date='2026-09-21',games=['AAA@BBB'],concentration=r,
               cohorts=dict(field=dict(counts=dict(supported_rows=3,entry_rows=4))))
        before=copy.deepcopy(c);text='\n'.join(report_lines(dict(contests=[c])))
        for phrase in ('3/4','3/3','not globally unique','Fewer than two','whole-source','not deduplicated','overlap'):
            self.assertIn(phrase,text)
        self.assertEqual(c,before)
        c['concentration']=dict(reason='Needs current indexing')
        self.assertIn('unavailable | unavailable', '\n'.join(report_lines(dict(contests=[c]))))
    def test_real_indexed_history_integrates_readonly_with_existing_report(self):
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest
        from historical_identity import reconcile
        from learning_db import generate_learning_report
        from test_portfolio_risk_evidence import logical_db,source_bytes
        f=SourceFixture(edit_results=results(duplicate=True));self.addCleanup(f.close)
        index_contest(f.db,f.source['hash']);reconcile(f.db)
        generate_learning_report(db_path=f.db)  # Existing report-table initialization.
        before=logical_db(f.db);sources=source_bytes(f.db)
        report=generate_learning_report(db_path=f.db)
        self.assertIn('Evidence coverage and lineup concentration',report['text'])
        self.assertIn('1 identical duplicate rows removed',report['text'])
        self.assertEqual(logical_db(f.db),before);self.assertEqual(source_bytes(f.db),sources)

    def test_missing_receipt_explains_one_time_indexing_and_never_reads_users(self):
        self.add();a=audit();self.conn.execute('UPDATE opponent_contests SET audit_json=?',(json.dumps(a),))
        self.assertIn('refreshes it once',self.receipt()['reason'])
    def test_backfill_bound_never_publishes_partial_totals(self):
        self.add(a=audit(6,6),members=[stats(),stats()])
        r=cached_receipt(self.conn,'one',audit(6,6),limit=1)
        self.assertIn('no partial',r['reason']);self.assertNotIn('entries',r)
    def test_real_old_index_backfills_once_without_csv_or_entry_rebuild(self):
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest
        from test_portfolio_risk_evidence import logical_db
        f=SourceFixture(edit_results=results());self.addCleanup(f.close)
        index_contest(f.db,f.source['hash'])
        with closing(sqlite3.connect(f.db)) as conn,conn:
            a=json.loads(conn.execute('SELECT audit_json FROM opponent_contests').fetchone()[0]);a.pop(CACHE_KEY)
            conn.execute('UPDATE opponent_contests SET audit_json=?',(json.dumps(a),))
            old=list(conn.execute('SELECT indexed_at FROM opponent_contests'))
            entries=list(conn.execute('SELECT * FROM opponent_entries'));slots=list(conn.execute('SELECT * FROM opponent_entry_slots'))
        with patch('opponent_history.analyze_saved_contest',side_effect=AssertionError('No CSV reanalysis')):
            self.assertTrue(index_contest(f.db,f.source['hash'])['unchanged'])
        with closing(sqlite3.connect(f.db)) as conn,conn:
            r=observations(conn);self.assertIsNone(r['receipts'][(f.source['hash'],f.salary['hash'])]['reason'])
            self.assertEqual(list(conn.execute('SELECT indexed_at FROM opponent_contests')),old)
            self.assertEqual(list(conn.execute('SELECT * FROM opponent_entries')),entries)
            self.assertEqual(list(conn.execute('SELECT * FROM opponent_entry_slots')),slots)
        before=logical_db(f.db)
        with patch('historical_concentration.cached_receipt',side_effect=AssertionError('No repeat stats scan')):
            index_contest(f.db,f.source['hash'])
        self.assertEqual(logical_db(f.db),before)
    def test_backfill_changed_source_or_cancellation_leaves_cache_unchanged(self):
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest,_refresh_concentration_receipt
        from test_portfolio_risk_evidence import logical_db
        import analysis_imports as ai
        f=SourceFixture(edit_results=results());self.addCleanup(f.close);index_contest(f.db,f.source['hash'])
        with closing(sqlite3.connect(f.db)) as conn,conn:
            key,a=conn.execute('SELECT contest_key,audit_json FROM opponent_contests').fetchone();a=json.loads(a);a.pop(CACHE_KEY)
            conn.execute('UPDATE opponent_contests SET audit_json=?',(json.dumps(a),))
        before=logical_db(f.db)
        with self.assertRaises(ai.ImportCancelled):_refresh_concentration_receipt(f.db,key,f.source,f.salary,lambda:True)
        self.assertEqual(logical_db(f.db),before)
        path=__import__('pathlib').Path(f.source['snapshot']);path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(ValueError):_refresh_concentration_receipt(f.db,key,f.source,f.salary,lambda:False)
        self.assertEqual(logical_db(f.db),before)

    def test_backfill_changed_mapping_leaves_all_rows_unchanged(self):
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest,_refresh_concentration_receipt
        from test_portfolio_risk_evidence import logical_db
        f=SourceFixture(edit_results=results());self.addCleanup(f.close);index_contest(f.db,f.source['hash'])
        with closing(sqlite3.connect(f.db)) as conn,conn:
            key,a=conn.execute('SELECT contest_key,audit_json FROM opponent_contests').fetchone();a=json.loads(a);a.pop(CACHE_KEY)
            conn.execute('UPDATE opponent_contests SET audit_json=?',(json.dumps(a),))
            conn.execute('UPDATE analysis_salary_pairs SET salary_hash=?',(f.source['hash'],))
        before=logical_db(f.db)
        with self.assertRaisesRegex(ValueError,'Salary association changed'):
            _refresh_concentration_receipt(f.db,key,f.source,f.salary,lambda:False)
        self.assertEqual(logical_db(f.db),before)
    def test_cancellation_after_backfill_update_rolls_back_receipt(self):
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        import opponent_history as history
        import analysis_imports as ai
        from test_portfolio_risk_evidence import logical_db
        f=SourceFixture(edit_results=results());self.addCleanup(f.close);history.index_contest(f.db,f.source['hash'])
        with closing(sqlite3.connect(f.db)) as conn,conn:
            key,a=conn.execute('SELECT contest_key,audit_json FROM opponent_contests').fetchone();a=json.loads(a);a.pop(CACHE_KEY)
            conn.execute('UPDATE opponent_contests SET audit_json=?',(json.dumps(a),))
        before=logical_db(f.db);updated=[];original=history._connect
        def connect(path,readonly=False):
            conn=original(path,readonly)
            if not readonly:conn.set_trace_callback(lambda q:updated.append(q) if q.startswith('UPDATE opponent_contests') else None)
            return conn
        with patch.object(history,'_connect',side_effect=connect):
            with self.assertRaises(ai.ImportCancelled):history._refresh_concentration_receipt(f.db,key,f.source,f.salary,lambda:bool(updated))
        self.assertTrue(updated);self.assertEqual(logical_db(f.db),before)

if __name__=='__main__':unittest.main()
