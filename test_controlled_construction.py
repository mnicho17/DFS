"""Exact-role historical controls; missing evidence is never zero or a tuning rule."""
from test_environment import install
install()
from contextlib import closing
import copy,json,sqlite3,unittest
from unittest.mock import patch
import controlled_construction as cc
from test_captain_defense_comparison import salaries


def slots(captain='Bravo',without=False):
    manifest=salaries();players=manifest['players']
    extra=dict(name='Golf',position='WR',team='BBB',role='FLEX',id='888',salary=4000)
    names=['Alpha','Bravo','Charlie','Delta','Echo','Foxtrot']
    result=[]
    for name in names:
        role='CPT' if name==captain else 'FLEX'
        p=next(p for p in players if p['name']==name and p['role']==role)
        if without and name=='Foxtrot':p=extra
        result.append((p['name'],role,p))
    return dict(format='showdown',players=players+[extra]),result


def payload(source='a',field=20,top=3):
    key='1001|4|3';f={key:[100,field,field,0,100]};c={key:[10,top,top,0,10]}
    return dict(version=1,complete=True,accepted=100,missing_rank=0,cutoff=100,
        rank_basis='observed accepted-entry rank cutoff; completeness unverified',
        captains={'1001':['Bravo','AAA','RB']},groups=dict(field=f,top1=copy.deepcopy(c),top5=c,rank1={}),source_hash=source,salary_hash='s')


def contest(source='a',day='2026-09-21',field=20,top=3):
    return dict(source=source,name=source,date=day,games=['AAA@BBB'],controls=payload(source,field,top))


class ControlledConstructionTests(unittest.TestCase):
    def test_collector_counts_repeats_captain_identity_specialists_and_low_salary(self):
        manifest,s=slots();b=cc.Builder(manifest['players'],3,100)
        b.add(s,1);b.add(s,1);_,other=slots(without=True);b.add(other,2)
        r=cc.validate(b.payload());v=r['groups']['field']['1001|0|3']
        self.assertEqual(v,[3,2,2,0,2]);self.assertEqual(r['groups']['top1']['1001|0|3'],[2,2,2,0,1])
        self.assertEqual(r['captains']['1001'],['Bravo','AAA','RB'])
    def test_missing_rank_stays_field_only_and_ties_are_included(self):
        manifest,s=slots();b=cc.Builder(manifest['players'],3,100);b.add(s,None);b.add(s,1);b.add(s,1)
        r=b.payload();self.assertEqual(r['missing_rank'],1)
        self.assertEqual(r['groups']['field']['1001|0|3'][0],3);self.assertEqual(r['groups']['rank1']['1001|0|3'][0],2)
    def test_wrong_explicit_role_id_partial_and_ambiguous_salary_identity_are_excluded(self):
        manifest,s=slots();bad=copy.deepcopy(s);bad[1]=('Bravo (101)','CPT',bad[1][2])
        partial=copy.deepcopy(s);partial[0]=('Alpha','FLEX',None)
        b=cc.Builder(manifest['players'],3,100)
        for v in (s,bad,partial):b.add(v,1)
        self.assertEqual(b.payload()['groups']['field']['1001|0|3'][0],1)
        duplicated=copy.deepcopy(manifest['players']);duplicated.append(copy.deepcopy(next(p for p in duplicated if p['id']=='1001')))
        b=cc.Builder(duplicated,1,100);b.add(s,1);self.assertEqual(b.payload()['groups']['field'],{})
    def test_readability_cache_is_independent_of_entry_order(self):
        manifest,s=slots();out=[]
        for order in ((False,True),(True,False)):
            b=cc.Builder(manifest['players'],2,100)
            for readable in order:b.add(s,1,readable)
            out.append(b.payload())
        self.assertEqual(out[0],out[1]);self.assertEqual(out[0]['groups']['field']['1001|0|3'][0],1)
    def test_salary_boundaries_team_split_and_specialist_captains(self):
        for salary,band in ((39999,0),(40000,1),(45000,2),(47500,3),(49000,4),(50000,4)):
            manifest,s=slots();manifest=copy.deepcopy(manifest);s=copy.deepcopy(s)
            s[1][2]['salary']+=salary-26000
            next(p for p in manifest['players'] if p['id']=='1001')['salary']=s[1][2]['salary']
            b=cc.Builder(manifest['players'],1,100);b.add(s,1)
            self.assertIn(f'1001|{band}|3',b.payload()['groups']['field'])
        manifest,s=slots(captain='Foxtrot');b=cc.Builder(manifest['players'],1,100);b.add(s,1)
        v=next(iter(b.payload()['groups']['field'].values()));self.assertEqual(v[1],0);self.assertEqual(v[2],1)
    def test_over_cap_same_team_and_duplicate_athlete_are_not_supported(self):
        for which in ('cap','team','athlete'):
            manifest,s=slots();manifest=copy.deepcopy(manifest);s=copy.deepcopy(s)
            if which=='cap':
                for p in manifest['players']:p['salary']=10000
                for _,_,p in s:p['salary']=10000
            elif which=='team':
                for p in manifest['players']:p['team']='AAA'
                for _,_,p in s:p['team']='AAA'
            else:s[0]=s[2]
            b=cc.Builder(manifest['players'],1,100);b.add(s,1);self.assertEqual(b.payload()['groups']['field'],{})
    def test_budget_or_incomplete_scan_never_publishes_partial_controls(self):
        manifest,s=slots();b=cc.Builder(manifest['players'],2,100);b.add(s,1)
        self.assertFalse(b.payload()['complete'])
        with patch.object(cc,'MAX_SIGNATURES',0):
            b=cc.Builder(manifest['players'],1,100);b.add(s,1);self.assertFalse(b.payload()['complete']);self.assertNotIn('groups',b.payload())
    def test_contradictory_supplied_size_withholds_controls(self):
        manifest,s=slots();b=cc.Builder(manifest['players'],2,1);b.add(s,1);b.add(s,2)
        self.assertFalse(b.payload()['complete']);self.assertIn('field size',b.payload()['reason'])
    def test_exact_captain_controls_remove_mixture_only_gap(self):
        r=payload();a,b='1001|4|3','1002|4|3'
        r['accepted']=r['cutoff']=1000;r['captains']['1002']=['Hotel','AAA','RB']
        r['groups']['field']={a:[900,90,90,0,900],b:[100,90,90,0,100]}
        r['groups']['top1']={a:[10,1,1,0,10],b:[90,81,81,0,90]};r['groups']['top5']=copy.deepcopy(r['groups']['top1'])
        v=cc.matched(r,'RB','opposing_dst','top1')
        self.assertAlmostEqual(v['field'],18);self.assertAlmostEqual(v['comparator'],18)
        self.assertAlmostEqual(100*(82/100-180/1000),64)
    def test_common_strata_coverage_and_true_zero_are_not_missing(self):
        r=payload();r['accepted']=200;r['groups']['field']['1001|3|3']=[100,0,0,0,100]
        v=cc.matched(r,'RB','opposing_dst','top1');self.assertEqual((v['field_rows'],v['field_total']),(100,200))
        r['groups']['top1']['1001|4|3'][1]=0;r['groups']['top5']['1001|4|3'][1]=0
        self.assertEqual(cc.matched(r,'RB','opposing_dst','top1')['comparator'],0)
        self.assertIsNone(cc.matched(r,'WR','opposing_dst','top1'))
    def test_equal_overlap_blocks_and_omit_one_ranges(self):
        records=[contest('a',top=3),contest('b',top=1),contest('c',day='2026-09-28',top=0)]
        r=cc.comparison(records,'RB','opposing_dst','top1')
        self.assertEqual((r['slates'],r['sources']),(2,3));self.assertEqual(r['gap'],-10)
        self.assertEqual(r['slate_sensitivity']['omit_one_min'],-20);self.assertEqual(r['slate_sensitivity']['omit_one_max'],0)
    def test_duplicate_receipts_do_not_inflate_and_conflicting_copies_withheld_in_both_orders(self):
        a=contest();b=copy.deepcopy(a);b['controls']['groups']['top1']['1001|4|3'][1]=2
        b['controls']['groups']['top5']['1001|4|3'][1]=2
        r=cc.comparison([a,a],'RB','opposing_dst','top1');self.assertEqual((r['sources'],r['field_rows']),(1,100))
        first=cc.comparison([a,b],'RB','opposing_dst','top1');self.assertEqual(first,cc.comparison([b,a],'RB','opposing_dst','top1'))
        self.assertEqual((first['sources'],first['conflicts']),(0,1))
    def test_missing_identity_or_receipts_never_supply_zero_rates(self):
        a=contest();a['date']=None;b=contest('b');b['controls']={'reason':'Old receipt'}
        r=cc.comparison([a,b],'RB','opposing_dst','top1')
        self.assertIsNone(r['field']);self.assertEqual((r['excluded_identity'],r['unavailable']),(1,1))
    def test_malformed_boolean_negative_and_nested_counts_withheld(self):
        for which in ('bool','negative','unique','nested','catalog','version'):
            r=payload()
            if which=='bool':r['groups']['field']['1001|4|3'][0]=True
            elif which=='negative':r['groups']['field']['1001|4|3'][1]=-1
            elif which=='unique':r['groups']['field']['1001|4|3'][4]=101
            elif which=='nested':r['groups']['top5']={}
            elif which=='catalog':r['captains']={}
            else:r['version']=True
            with self.assertRaises(ValueError):cc.validate(r)
    def test_old_source_row_comparisons_unchanged_by_new_receipt(self):
        from historical_contest_comparison import field_comparison
        # The existing construction fixture is independent of controlled evidence.
        from test_historical_contest_comparison import contest as old_contest
        c=old_contest();c['version']=3
        before=field_comparison([c],'Kicker/defense slots: 2','top1')
        c['controls']=payload();self.assertEqual(field_comparison([c],'Kicker/defense slots: 2','top1'),before)
    def test_report_labels_controls_named_captain_scope_repeats_and_limits(self):
        c=contest();before=copy.deepcopy(c);text='\n'.join(cc.report_lines(dict(format='showdown',contests=[c])))
        for label in ('Prepared receipt coverage','lack a valid rank','exact historical Captain ID','Bravo','Repeated matched','accepted unique EntryIds','no common strata','not confidence intervals','source-row'):
            self.assertIn(label,text)
        self.assertEqual(c,before);self.assertEqual(cc.report_lines(dict(format='classic',contests=[c])),[])
        text='\n'.join(cc.report_lines(dict(format='showdown',contests=[dict(c,controls=None)])))
        self.assertIn('unavailable',text);self.assertNotIn('| RB |',text)


class ControlledIndexTests(unittest.TestCase):
    def fixture(self,**kwargs):
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest
        f=SourceFixture(edit_results=results(**kwargs));self.addCleanup(f.close);index_contest(f.db,f.source['hash']);return f
    def receipt(self,f):
        with closing(sqlite3.connect(f.db)) as conn:return cc.observations(conn)['receipts'][(f.source['hash'],f.salary['hash'])]
    def test_normal_index_captures_unique_entries_and_repeated_lineups(self):
        f=self.fixture(duplicate=True);r=self.receipt(f)
        self.assertEqual(r['accepted'],100);self.assertEqual(sum(v[0] for v in r['groups']['field'].values()),100)
        self.assertEqual(sum(v[4] for v in r['groups']['field'].values()),1)
    def test_conflicting_entry_copies_are_excluded_as_a_whole(self):
        f=self.fixture(duplicate=True,conflict=True);r=self.receipt(f)
        self.assertEqual(r['accepted'],99);self.assertEqual(sum(v[0] for v in r['groups']['top1'].values()),0)
    def test_report_reads_receipts_only_and_preserves_database_and_sources(self):
        from historical_identity import reconcile
        from learning_db import generate_learning_report
        from test_portfolio_risk_evidence import logical_db,source_bytes
        f=self.fixture();reconcile(f.db);generate_learning_report(db_path=f.db)
        before=logical_db(f.db);original=source_bytes(f.db)
        report=generate_learning_report(db_path=f.db)
        self.assertIn('Controlled Showdown construction review',report['text']);self.assertIn('Exact Captain detail',report['text'])
        self.assertEqual(logical_db(f.db),before);self.assertEqual(source_bytes(f.db),original)
        with closing(sqlite3.connect(f.db)) as conn:
            def authorize(action,table,*args):
                return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table in ('opponent_entries','opponent_entry_slots','historical_results','opponent_user_contest_stats') else sqlite3.SQLITE_OK
            conn.set_authorizer(authorize);r=cc.observations(conn);self.assertTrue(r['complete'])
    def test_bound_and_revision_mismatches_withhold_controls(self):
        f=self.fixture()
        with closing(sqlite3.connect(f.db)) as conn,conn:
            self.assertFalse(cc.observations(conn,byte_limit=1)['complete'])
            key,encoded=conn.execute(f'SELECT contest_key,payload_json FROM {cc.TABLE}').fetchone();r=json.loads(encoded);r['salary_hash']='different'
            conn.execute(f'UPDATE {cc.TABLE} SET payload_json=?',(json.dumps(r),))
            self.assertIn('revision differs',cc.observations(conn)['receipts'][(f.source['hash'],f.salary['hash'])]['reason'])
    def test_old_index_backfills_saved_slots_once_without_reanalysis_or_entry_changes(self):
        from opponent_history import index_contest
        from test_portfolio_risk_evidence import logical_db
        f=self.fixture();original_receipt=self.receipt(f);before_entries=f.query('SELECT * FROM opponent_entries');before_slots=f.query('SELECT * FROM opponent_entry_slots');stamp=f.query('SELECT indexed_at FROM opponent_contests')
        f.change(f'DROP TABLE {cc.TABLE}')
        with patch('opponent_history.analyze_saved_contest',side_effect=AssertionError('No CSV reanalysis')):index_contest(f.db,f.source['hash'])
        self.assertEqual(self.receipt(f),original_receipt);self.assertEqual(f.query('SELECT * FROM opponent_entries'),before_entries)
        self.assertEqual(f.query('SELECT * FROM opponent_entry_slots'),before_slots);self.assertEqual(f.query('SELECT indexed_at FROM opponent_contests'),stamp)
        before=logical_db(f.db)
        with patch('controlled_construction.from_index',side_effect=AssertionError('No repeat backfill')):index_contest(f.db,f.source['hash'])
        self.assertEqual(logical_db(f.db),before)
    def test_grouped_backfill_keeps_partial_metadata_separate_and_matches_initial_receipt(self):
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest
        def edit(rows):
            results()(rows);column=rows[0].index('Lineup')
            for row in rows[1:4]:row[column]=''
        f=SourceFixture(edit_results=edit);self.addCleanup(f.close);index_contest(f.db,f.source['hash'])
        first=self.receipt(f);self.assertEqual(sum(v[0] for v in first['groups']['field'].values()),97)
        self.assertEqual(first['groups']['top1'],{})
        f.change(f'DELETE FROM {cc.TABLE}');index_contest(f.db,f.source['hash'])
        self.assertEqual(self.receipt(f),first)
    def test_incomplete_metadata_never_borrows_from_identical_complete_signature_group(self):
        f=self.fixture();f.change('UPDATE opponent_entries SET metadata_complete=0 WHERE entry_id IN (?,?,?)',('1000','1001','1002'))
        key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        with closing(sqlite3.connect(f.db)) as conn:
            r=cc.from_index(conn,key,f.salary['manifest']['players'],100,100)
        self.assertTrue(r['complete']);self.assertEqual(sum(v[0] for v in r['groups']['field'].values()),97)
        self.assertEqual(r['groups']['top1'],{})

    def test_named_display_cap_does_not_limit_aggregate_evidence(self):
        records=[contest(f'source{i:03d}') for i in range(101)]
        r=cc.comparison(records,'RB','opposing_dst','top1');self.assertEqual(r['sources'],101)
        text='\n'.join(cc.report_lines(dict(format='showdown',contests=records)))
        self.assertIn('100/101 matched source/Captain groups',text)
        self.assertIn('aggregates above use all groups',text)

    def test_cancellation_after_receipt_insert_rolls_back_all_changes(self):
        import opponent_history as h
        import analysis_imports as ai
        from test_portfolio_risk_evidence import logical_db
        f=self.fixture();f.change(f'DELETE FROM {cc.TABLE}');key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        before=logical_db(f.db);inserted=[];original=h._connect
        def connect(path,readonly=False):
            c=original(path,readonly)
            if not readonly:c.set_trace_callback(lambda sql:inserted.append(sql) if sql.startswith('INSERT OR IGNORE INTO '+cc.TABLE) else None)
            return c
        with patch.object(h,'_connect',side_effect=connect):
            with self.assertRaises(ai.ImportCancelled):h._refresh_controlled_receipt(f.db,key,f.source,f.salary,lambda:bool(inserted))
        self.assertTrue(inserted);self.assertEqual(logical_db(f.db),before)
    def test_changed_source_or_mapping_cannot_publish_backfilled_receipt(self):
        import opponent_history as h
        from test_portfolio_risk_evidence import logical_db
        f=self.fixture();f.change(f'DELETE FROM {cc.TABLE}');key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        f.change('UPDATE analysis_salary_pairs SET salary_hash=?',(f.source['hash'],));before=logical_db(f.db)
        with self.assertRaisesRegex(ValueError,'association changed'):h._refresh_controlled_receipt(f.db,key,f.source,f.salary,lambda:False)
        self.assertEqual(logical_db(f.db),before)
        f.change('UPDATE analysis_salary_pairs SET salary_hash=?',(f.salary['hash'],));before=logical_db(f.db)
        p=__import__('pathlib').Path(f.source['snapshot']);p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ValueError):h._refresh_controlled_receipt(f.db,key,f.source,f.salary,lambda:False)
        self.assertEqual(logical_db(f.db),before)
    def test_import_report_is_generated_after_indexing_receipts(self):
        from analysis_imports_ui import CombinedImportWorker
        f=self.fixture();worker=CombinedImportWorker(str(f.results),str(f.salaries),db_path=f.db,refresh_history=True)
        order=[];finished=[];errors=[];worker.finished.connect(finished.append);worker.error.connect(errors.append)
        with patch('calibration_capture_discovery.refresh',return_value={'changed':True}), \
             patch('opponent_history.sync_saved',side_effect=lambda *a,**k:order.append('index') or {'contests':[],'errors':[]}), \
             patch('learning_db.generate_learning_report',side_effect=lambda **k:order.append('report') or {'text':'current'}):worker.run()
        self.assertEqual(errors,[]);self.assertEqual(order,['index','report']);self.assertEqual(finished[0]['report']['text'],'current')
    def test_cancelled_index_does_not_deliver_a_stale_report(self):
        from analysis_imports_ui import CombinedImportWorker
        f=self.fixture();worker=CombinedImportWorker(str(f.results),str(f.salaries),db_path=f.db,refresh_history=True)
        finished=[];worker.finished.connect(finished.append)
        def cancel(*a,**k):worker.cancelled.set();return {'contests':[],'errors':[]}
        with patch('calibration_capture_discovery.refresh',return_value={'changed':True}), \
             patch('opponent_history.sync_saved',side_effect=cancel), \
             patch('learning_db.generate_learning_report',side_effect=AssertionError('No stale report')):worker.run()
        self.assertTrue(finished[0]['cancelled']);self.assertNotIn('report',finished[0])

    def test_missing_receipt_table_does_not_create_schema(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            self.assertEqual(cc.observations(conn)['receipts'],{});self.assertEqual(list(conn.execute('SELECT name FROM sqlite_master')),[])

if __name__=='__main__':unittest.main()
