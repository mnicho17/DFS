"""Synthetic original CSV/SQLite/ZIP evidence, never account or live sports data."""
from test_environment import install
install()
import copy
import csv
from contextlib import closing, contextmanager
import hashlib
import json
import itertools
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import analysis_imports as ai
import historical_identity as hi
import review_build_evidence as be
from build_snapshots import create_snapshot,save_snapshot,fingerprint
import hindsight_evidence as he
import hindsight_solver as hs
from hindsight_contract import SCALE,Cancelled
from hindsight_rules import classify
from test_hindsight_solver import athletes
from test_portfolio_risk_evidence import logical_db,source_bytes


class SourceFixture:
    def __init__(self,kind='showdown',pool=None,edit_results=None):
        self.tmp=tempfile.TemporaryDirectory(prefix='rl07a-');self.root=Path(self.tmp.name);self.db=self.root/'history.sqlite'
        self.results=self.root/'results';self.results.mkdir();self.salaries=self.root/'salaries';self.salaries.mkdir()
        self.kind=kind;self.pool=pool or athletes(kind);self.raw_result=self.results/'synthetic.csv'
        self.raw_salary=self.salaries/'salary.csv';self.game='NE@SEA 09/21/2026 08:15PM ET'
        with self.raw_salary.open('w',newline='',encoding='utf-8-sig') as handle:
            w=csv.writer(handle);w.writerow(['Position','Name + ID','Name','ID','Roster Position','Salary','Game Info','TeamAbbrev'])
            for p in self.pool:
                roles=list(p['roles']) if kind=='showdown' else ['/'.join(p['roles'])]
                for role in roles:
                    r=p['roles'][role if kind=='showdown' else p['position']]
                    w.writerow([p['position'],p['name']+' ('+r['id']+')',p['name'],r['id'],role,r['salary'],self.game,p['team']])
        indices=list(range(6)) if kind=='showdown' else [0,2,3,5,6,7,9,4,11]
        self.roles=['CPT']+['FLEX']*5 if kind=='showdown' else ['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']
        chosen=[self.pool[i] for i in indices];self.lineup=' '.join(r+' '+p['name'] for r,p in zip(self.roles,chosen))
        total=sum(p['roles'][r]['score_units'] for r,p in zip(self.roles,chosen))/SCALE
        with self.raw_result.open('w',newline='',encoding='utf-8-sig') as handle:
            w=csv.writer(handle);w.writerow(['Rank','EntryId','EntryName','Points','Lineup','ContestId','SlateDate','Player','Roster Position','FPTS','PlayerID','FieldSize'])
            for n,p in enumerate(self.pool):
                role='FLEX' if kind=='showdown' else p['position'];r=p['roles'][role]
                w.writerow([1 if n==0 else '',1000 if n==0 else '','Synthetic_User' if n==0 else '',
                    total if n==0 else '',self.lineup if n==0 else '','synthetic' if n==0 else '',
                    '2026-09-21' if n==0 else '',p['name'],role,str(r['score_units']/SCALE),r['id'],1000 if n==0 else ''])
        if edit_results:
            with self.raw_result.open(newline='',encoding='utf-8-sig') as handle:rows=list(csv.reader(handle))
            edit_results(rows)
            with self.raw_result.open('w',newline='',encoding='utf-8-sig') as handle:csv.writer(handle).writerows(rows)
        result=ai.import_folders(self.results,self.salaries,db_path=str(self.db),username='Synthetic_User')
        assert not result['errors'],result
        with closing(sqlite3.connect(self.db)) as conn:sources=ai._sources(conn)
        self.source=next(s for s in sources if s['kind']=='results');self.salary=next(s for s in sources if s['kind']=='salary')
        players=[]
        for p in self.pool:
            base=p['roles']['FLEX' if kind=='showdown' else p['position']];cpt=p['roles'].get('CPT')
            players.append(dict(Name=p['name'],Position=p['position'],Team=p['team'],GameInfo=self.game,
                FlexID=p['key'],CptID=cpt['id'] if cpt else None,FlexSalary=base['salary'],CptSalary=cpt['salary'] if cpt else None,
                FlexProjection=8,CptProjection=12,ProjectionSource='Synthetic frozen forecast'))
        self.snap=create_snapshot(players,dict(sport='NFL',contest_kind=kind),{})
        self.snap['created_at']='2026-09-21T18:00:00-04:00'

    def close(self):self.tmp.cleanup()
    def query(self,sql,args=()):
        with closing(sqlite3.connect(self.db)) as conn:return conn.execute(sql,args).fetchall()
    def change(self,sql,args=()):
        with closing(sqlite3.connect(self.db)) as conn,conn:conn.execute(sql,args)
    def one(self):return hi.qualified_contests(self.db)[0].data
    def selection(self,restricted=False,archive=None):
        d=self.one();return dict(contest_id=d['identity_id'],snapshot_digest=be.digest(self.snap) if restricted else None,archive_id=archive)
    def snapshot(self,name='snapshot.json'):
        self.snap['input_id']=fingerprint(self.snap['inputs']);p=self.root/'snapshots'/name;save_snapshot(str(p),self.snap);return p
    def archive(self,name='archive.zip'):
        self.snapshot();folder=self.root/'build-archives';folder.mkdir(exist_ok=True)
        meta=dict(schema_version=1,record_type='generated_outputs',sport='NFL',kind=self.kind,
            input_id=self.snap['input_id'],output_count=1,created_at='2026-09-21T18:10:00-04:00',build_status='completed')
        ids=list(range(6)) if self.kind=='showdown' else [0,2,3,5,6,7,9,4,11]
        rows=[dict(slots=[dict(slot=role,player=self.snap['inputs']['players'][i]) for role,i in zip(self.roles,ids)])]
        files={'input-snapshot.json':json.dumps(self.snap).encode(),'lineups.json':json.dumps(dict(metadata=meta,lineups=rows)).encode()}
        files['manifest.json']=json.dumps(dict(metadata=meta,sha256={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})).encode()
        path=folder/name
        with zipfile.ZipFile(path,'w') as z:
            for k,v in files.items():z.writestr(k,v)
        return path
    def edit_source(self,kind,edit):
        source=self.source if kind=='results' else self.salary;path=Path(source['snapshot'])
        with path.open(newline='',encoding='utf-8-sig') as handle:rows=list(csv.reader(handle))
        edit(rows)
        with path.open('w',newline='',encoding='utf-8-sig') as handle:csv.writer(handle).writerows(rows)
        old=source['hash'];source['hash']=ai._hash(path)
        source['manifest']=(ai._results_manifest if kind=='results' else ai._salary_manifest)(path,lambda:False)
        self.change('UPDATE analysis_sources SET hash=?,manifest=? WHERE hash=?',(source['hash'],json.dumps(source['manifest']),old))
        self.change('UPDATE analysis_salary_pairs SET '+('result_hash' if kind=='results' else 'salary_hash')+'=? WHERE '+('result_hash' if kind=='results' else 'salary_hash')+'=?',(source['hash'],old))


EXPLICIT_SHOWDOWN='CPT A (1100) FLEX B (101) FLEX C (102) FLEX D (103) FLEX E (104) FLEX F (105)'
CLASSIC_ROSTER='QB Q1 RB R1 RB R2 WR W1 WR W2 WR W3 TE T1 FLEX R3 DST D1'


def standings_entries(rows,entries,kind='showdown'):
    """Write entry copies separately from the original per-player actuals."""
    # Explicit format keeps observed-entry interpretation available even when
    # original salary pairing correctly refuses a contradictory ID/role.
    for row in rows:del row[12:]
    rows[0].extend(['ContestType','Sport'])
    for row in rows[1:]:row[:7]=['']*7;row[11]='';row.extend(['',''])
    for ident,total,lineup in entries:
        rows.append(['1',ident,'Synthetic_User',total,lineup,'synthetic','2026-09-21','','','','','1000',kind,'NFL'])


class ObservedEntryIdentityTests(unittest.TestCase):
    def report(self,entries,kind='showdown',qualified=True):
        f=SourceFixture(kind,edit_results=lambda rows:standings_entries(rows,entries,kind));self.addCleanup(f.close)
        selection=f.selection();authority=f.one();before=logical_db(f.db);files=source_bytes(f.root)
        original=he.read_database;revalidated=[];revalidate=he.CaptureReader.revalidate
        @contextmanager
        def readonly(path):
            with original(path) as conn:
                self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0],1)
                yield conn
        def checked(reader):
            revalidate(reader);revalidated.append(dict(reader.receipts))
        with patch.object(he,'read_database',readonly),patch.object(he.CaptureReader,'revalidate',checked):
            captured=he.capture(f.db,selection)
        self.assertEqual(len(revalidated),1)
        self.assertIn(f.source['hash'],revalidated[0].values())
        report=hs.calculate(captured,seconds=5).data
        self.assertEqual(before,logical_db(f.db));self.assertEqual(files,source_bytes(f.root));self.assertEqual(authority,f.one())
        if qualified:
            self.assertIn(f.salary['hash'],revalidated[0].values())
            self.assertEqual(report['capture']['gates']['supplied'],dict(status='ready',blockers=[]))
            self.assertEqual(report['scopes']['supplied']['status'],'optimal')
            self.assertEqual(report['scopes']['supplied']['points'],'70' if kind=='showdown' else '111')
            self.assertEqual(report['capture']['actual_coverage']['unknown'],0)
        else:
            self.assertEqual(report['capture']['gates']['supplied'],dict(status='unavailable_evidence',
                blockers=['qualified_exact_salary_revision_required']))
            self.assertIn('no_compatible_salary_revision',authority['blockers'])
            self.assertEqual(report['scopes']['supplied']['status'],'unavailable_evidence')
            self.assertIsNone(report['scopes']['supplied']['points']);self.assertFalse(report['capture']['pool'])
        return report

    def conflict(self,lineups,kind='showdown',extra=(),qualified=False):
        reports=[]
        for order in sorted(set(itertools.permutations(lineups))):
            with self.subTest(kind=kind,order=order):
                d=self.report([('1000','70' if kind=='showdown' else '111',text) for text in order]+list(extra),kind,qualified)
                o=d['capture']['observed'];coverage=o['coverage']
                self.assertEqual(coverage['conflicting_entry_ids'],1)
                self.assertEqual(coverage.get('identical_duplicate_rows',0),0)
                self.assertEqual(coverage['accepted_entries'],len(extra))
                if not extra:
                    self.assertIsNone(o['highest']);self.assertFalse(o['highest_witnesses'])
                    self.assertFalse(o['highest_exact_validated']);self.assertIsNone(d['gaps']['observed_units'])
                    self.assertEqual(o['highest_tied_entries'],0);self.assertEqual(d['gaps']['issues'],[])
                reports.append(d)
        return reports

    def test_explicit_captain_id_conflict_in_both_orders(self):
        self.conflict([EXPLICIT_SHOWDOWN,EXPLICIT_SHOWDOWN.replace('(1100)','(9999)')])

    def test_explicit_flex_id_conflict_in_both_orders(self):
        self.conflict([EXPLICIT_SHOWDOWN,EXPLICIT_SHOWDOWN.replace('(101)','(9999)')])

    def test_classic_individual_roles_conflict_in_both_orders(self):
        self.conflict([CLASSIC_ROSTER,CLASSIC_ROSTER.replace('QB Q1 RB R1','RB Q1 QB R1')],'classic')

    def test_conflicting_copies_never_count_as_identical_in_any_order(self):
        self.conflict([EXPLICIT_SHOWDOWN,EXPLICIT_SHOWDOWN,EXPLICIT_SHOWDOWN.replace('(1100)','(9999)')])

    def test_conflict_exclusion_recomputes_highest_from_independent_entry(self):
        lower=EXPLICIT_SHOWDOWN.replace('FLEX F (105)','FLEX G (106)')
        other=EXPLICIT_SHOWDOWN.replace('CPT A (1100) FLEX B (101)','FLEX A (100) CPT B (1101)')
        for d in self.conflict([EXPLICIT_SHOWDOWN,other],extra=[('1001','68',lower)],qualified=True):
            o=d['capture']['observed'];self.assertEqual(o['highest'],'68');self.assertTrue(o['highest_exact_validated'])
            self.assertEqual(o['highest_tied_entries'],1);self.assertEqual(d['gaps']['observed_units'],2*SCALE)
            self.assertEqual(o['highest_witnesses'][0]['points'],'68')

    def test_classic_eligible_role_changes_still_conflict_with_independent_solver(self):
        baseline=self.report([('1000','111',CLASSIC_ROSTER)],'classic')
        for d in self.conflict([CLASSIC_ROSTER,CLASSIC_ROSTER.replace('RB R1','FLEX R1').replace('FLEX R3','RB R3')],
                               'classic',qualified=True):
            for field in ('pool','universe_digest','actual_coverage','salary_coverage'):
                self.assertEqual(d['capture'][field],baseline['capture'][field])

    def test_identical_copies_and_decimal_equivalence_preserve_distinct_entries(self):
        d=self.report([('1000','70',EXPLICIT_SHOWDOWN),('1000','70.0',EXPLICIT_SHOWDOWN),
                       ('1000','70.00',EXPLICIT_SHOWDOWN),('1001','70',EXPLICIT_SHOWDOWN)])
        o=d['capture']['observed'];self.assertEqual(o['coverage']['identical_duplicate_rows'],2)
        self.assertEqual(o['coverage']['conflicting_entry_ids'],0);self.assertEqual(o['coverage']['accepted_entries'],2)
        self.assertEqual((o['highest_tied_entries'],o['highest_tied_unique_valid_rosters']),(2,1))
        self.assertEqual(d['gaps']['observed_units'],0)

    def test_exact_decimal_difference_remains_conflicting_in_both_orders(self):
        for scores in [('70','70.01'),('70.00000000000000000000000000001','70.00000000000000000000000000002')]:
            for order in (scores,scores[::-1]):
                with self.subTest(scores=order):
                    d=self.report([('1000',value,EXPLICIT_SHOWDOWN) for value in order]);o=d['capture']['observed']
                    self.assertEqual(o['coverage']['conflicting_entry_ids'],1);self.assertIsNone(o['highest'])
                    self.assertIsNone(d['gaps']['observed_units'])

    def test_role_aliases_and_slot_order_preserve_identical_copies(self):
        for kind,a,b in [('showdown',EXPLICIT_SHOWDOWN,
                'flex F (105) FLEX E (104) CAPTAIN A (1100) FLEX D (103) FLEX C (102) FLEX B (101)'),
                ('classic',CLASSIC_ROSTER,'D/ST D1 FLEX R3 TE T1 WR W3 RB R2 QB Q1 WR W2 WR W1 RB R1')]:
            for order in ((a,b),(b,a)):
                with self.subTest(kind=kind,order=order):
                    d=self.report([('1000','70' if kind=='showdown' else '111',text) for text in order],kind)
                    o=d['capture']['observed'];self.assertEqual(o['coverage']['identical_duplicate_rows'],1)
                    self.assertEqual(o['coverage']['conflicting_entry_ids'],0);self.assertTrue(o['highest_exact_validated'])
                    self.assertEqual(d['gaps']['observed_units'],0)

    def test_single_bad_id_retains_reported_score_without_lower_witness_substitution(self):
        for highest in (EXPLICIT_SHOWDOWN.replace('(1100)','(9999)'),'hidden'):
            with self.subTest(highest=highest):
                d=self.report([('1000','70',highest),('1001','68',EXPLICIT_SHOWDOWN.replace('FLEX F (105)','FLEX G (106)'))],
                              qualified=highest=='hidden')
                o=d['capture']['observed'];self.assertEqual(o['highest'],'70');self.assertFalse(o['highest_witnesses'])
                self.assertEqual(o['coverage']['accepted_entries'],2);self.assertEqual(o['coverage']['conflicting_entry_ids'],0)
                self.assertFalse(o['highest_exact_validated']);self.assertIsNone(d['gaps']['observed_units'])
                self.assertEqual(d['gaps']['issues'],[])


class HindsightEvidenceTests(unittest.TestCase):
    def setUp(self):self.f=SourceFixture();self.addCleanup(self.f.close)
    def capture(self,selection=None):
        before=logical_db(self.f.db);files=source_bytes(self.f.root)
        result=he.capture(self.f.db,selection or self.f.selection())
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))
        return result
    def report(self,selection=None):return hs.calculate(self.capture(selection),seconds=5).data
    def blocked(self):
        d=self.capture().data;self.assertEqual(d['gates']['supplied']['status'],'unavailable_evidence',d);return d

    def test_full_universe_no_snapshot_or_archive_solves_70(self):
        d=self.report();self.assertEqual(d['scopes']['supplied']['points'],'70')
        self.assertEqual(d['scopes']['snapshot']['status'],'not_requested')
        self.assertEqual(d['capture']['salary_coverage']['rows_read'],16)
        self.assertEqual(d['capture']['actual_coverage']['known'],8)
        self.assertEqual(d['capture']['contest_pool_completeness']['status'],'unverified')

    def test_classic_original_eligibility_and_readonly_111(self):
        f=SourceFixture('classic');self.addCleanup(f.close);self.f=f
        d=self.report();self.assertEqual(d['scopes']['supplied']['points'],'111')
        self.assertEqual(d['capture']['pool'][2]['roles'].keys(),{'RB','FLEX'})

    def test_snapshot_without_archive_qualifies_local_comparison(self):
        self.f.snap['inputs']['players'][0].update(FadeFlex=True,FadeCpt=True);self.f.snapshot()
        d=self.report(self.f.selection(True));self.assertEqual(d['scopes']['snapshot']['points'],'46')
        self.assertEqual(d['capture']['restricted']['label'],'Snapshot-local restricted optimum')
        self.assertEqual(d['capture']['restricted']['provenance']['original_or_submitted_build'],'not_established')

    def test_archive_full_ladder_cannot_hide_missing_unobserved_actual(self):
        self.f.edit_source('results',lambda rows:rows[-1].__setitem__(9,''));self.f.archive()
        d=self.blocked();self.assertEqual(d['actual_coverage']['unknown'],1)
        self.assertIn('unknown_eligible_actual_scores',d['gates']['supplied']['blockers'])

    def test_partial_field_does_not_block_scoped_solve(self):
        d=self.report();o=d['capture']['observed'];self.assertEqual(o['field_size'],1000)
        self.assertEqual(o['field_completeness'],'partial_or_unverified');self.assertEqual(d['scopes']['supplied']['status'],'optimal')

    def test_outside_salary_high_scorer_does_not_establish_original_membership(self):
        self.f.edit_source('results',lambda rows:rows.append(['','','','','','','','Omitted player','FLEX','900','9999','']))
        d=self.report();self.assertEqual(d['scopes']['supplied']['points'],'70')
        self.assertEqual(d['capture']['actual_coverage']['outside_salary_observations'],1)
        self.assertEqual(d['capture']['contest_pool_completeness']['status'],'unverified')

    def test_exact_duplicate_10_00_vs_10_01_keeps_old_authority_tolerance(self):
        self.f.edit_source('results',lambda rows:(rows[1].__setitem__(9,'20.00'),rows.append(['','','','','','','','A','FLEX','20.01','100',''])))
        old=self.f.one();self.assertEqual(old['actual_score_evidence']['conflicting_scores'],[])
        d=self.blocked();self.assertIn('exact_actual_score_conflict',d['gates']['supplied']['blockers'])
        self.assertEqual(self.f.one(),old)

    def test_decimal_equivalent_observations_are_one_exact_value(self):
        self.f.edit_source('results',lambda rows:rows.extend([['','','','','','','','A','FLEX',v,'100',''] for v in ('20','20.0','20.00')]))
        self.assertEqual(self.report()['scopes']['supplied']['points'],'70')

    def test_blank_nonfinite_boolean_precision_and_magnitude_are_not_zero(self):
        original=Path(self.f.source['snapshot']).read_text(encoding='utf-8-sig')
        for value in ('','NaN','Inf','true','0.00001','10001'):
            self.f.edit_source('results',lambda rows:rows[2].__setitem__(9,value))
            with self.subTest(value=value):self.blocked()
        self.assertTrue(original)

    def test_captain_observation_is_already_weighted_and_optional(self):
        self.f.edit_source('results',lambda rows:rows.append(['','','','','','','','A',' captain ','30.00000','1100','']))
        self.assertEqual(self.report()['scopes']['supplied']['points'],'70')
        self.f.edit_source('results',lambda rows:rows[-1].__setitem__(9,'30.01'))
        before=self.f.one();self.assertEqual(before['actual_score_evidence']['conflicting_scores'],[])
        self.blocked();self.assertEqual(before,self.f.one())

    def test_near_equal_entry_total_preserves_reported_and_reconstructed_scores(self):
        self.f.edit_source('results',lambda rows:rows[1].__setitem__(3,'69.99'))
        old=self.f.one();self.assertEqual(old['actual_score_evidence']['conflicting_roster_totals'],0)
        d=self.report();o=d['capture']['observed'];self.assertEqual(o['highest'],'69.99')
        self.assertEqual(o['highest_witnesses'][0]['points'],'70');self.assertFalse(o['highest_exact_validated'])
        self.assertIsNone(d['gaps']['observed_units']);self.assertEqual(old,self.f.one())

    def test_wrong_player_id_is_not_overridden_by_matching_name(self):
        self.f.edit_source('results',lambda rows:rows[1].__setitem__(10,'99999'))
        self.assertIn('actual_explicit_id_conflict',self.blocked()['gates']['supplied']['blockers'])

    def test_captain_price_and_id_conflicts_block(self):
        self.f.edit_source('salary',lambda rows:rows[2].__setitem__(5,'9001'))
        self.assertIn('captain_salary_ratio_conflict',self.blocked()['gates']['supplied']['blockers'])

    def test_highest_unreadable_stays_highest_and_no_next_roster_substitution(self):
        self.f.edit_source('results',lambda rows:rows.append(['2','1001','Synthetic_User','80','hidden','synthetic','2026-09-21','','','','','1000']))
        d=self.report();o=d['capture']['observed'];self.assertEqual(o['highest'],'80');self.assertFalse(o['highest_witnesses'])
        self.assertFalse(o['highest_exact_validated']);self.assertIn('reported_entry_exceeds_supplied_optimum',d['gaps']['issues'])

    def test_entry_duplicates_conflicts_and_tied_roster_count_are_separate(self):
        def edit(rows):
            rows.append(rows[1].copy());second=rows[1].copy();second[1]='1001';rows.append(second)
            conflicting=rows[1].copy();conflicting[3]='100';rows.append(conflicting)
        self.f.edit_source('results',edit);o=self.report()['capture']['observed']
        self.assertEqual(o['coverage']['conflicting_entry_ids'],1);self.assertEqual(o['highest'],'70.0')
        self.assertEqual(o['highest_tied_entries'],1);self.assertEqual(o['highest_tied_unique_valid_rosters'],1)

    def test_validated_same_score_entries_keep_separate_entry_and_roster_ties(self):
        def edit(rows):
            second=rows[1].copy();second[1]='1001';rows.append(second)
        self.f.edit_source('results',edit);o=self.report()['capture']['observed']
        self.assertEqual((o['highest_tied_entries'],o['highest_tied_unique_valid_rosters']),(2,1))

    def test_cross_contest_exact_conflict_no_score_borrowing(self):
        path=Path(self.f.source['snapshot'])
        with path.open(newline='',encoding='utf-8-sig') as f:rows=list(csv.reader(f))
        rows[1][9]='20.01';rows[1][5]='second';rows[1][1]='2000'
        other=self.f.results/'other.csv'
        with other.open('w',newline='',encoding='utf-8-sig') as f:csv.writer(f).writerows(rows)
        ai.import_folders(self.f.results,self.f.salaries,db_path=str(self.f.db))
        self.assertIn('exact_cross_contest_score_conflict',self.blocked()['gates']['supplied']['blockers'])

    def test_cross_contest_captain_conflicts_with_selected_base_without_redundant_captain(self):
        selection=self.f.selection()
        with Path(self.f.source['snapshot']).open(newline='',encoding='utf-8-sig') as handle:
            rows=list(csv.reader(handle))
        rows[1][9]='';rows[1][5]='second';rows[1][1]='2000'
        rows.append(['','','','','','','','A','CPT','30.01','1100',''])
        with (self.f.results/'other.csv').open('w',newline='',encoding='utf-8-sig') as handle:
            csv.writer(handle).writerows(rows)
        ai.import_folders(self.f.results,self.f.salaries,db_path=str(self.f.db))
        captured=self.capture(selection).data
        self.assertIn('exact_cross_contest_score_conflict',captured['gates']['supplied']['blockers'])
        self.assertEqual(captured['actual_coverage']['unknown'],0)

    def test_observed_entry_deduplication_never_rounds_long_reported_values(self):
        def edit(rows):
            rows[1][3]='70.00000000000000000000000000001'
            duplicate=rows[1].copy();duplicate[3]='70.00000000000000000000000000002';rows.append(duplicate)
        self.f.edit_source('results',edit)
        observed=self.report()['capture']['observed']
        self.assertEqual(observed['coverage']['conflicting_entry_ids'],1)
        self.assertIsNone(observed['highest'])

    def test_saved_salary_is_preserved_when_source_changes(self):
        ai.save_pair(self.f.source['hash'],self.f.salary['hash'],db_path=str(self.f.db),confirm_date=True)
        chosen=self.f.selection();Path(self.f.salary['snapshot']).write_text('changed')
        before=logical_db(self.f.db)
        d=he.capture(self.f.db,chosen).data;self.assertEqual(d['gates']['supplied']['status'],'unavailable_evidence')
        self.assertEqual(before,logical_db(self.f.db))

    def test_snapshot_selection_does_not_retarget_newer_revision(self):
        self.f.snapshot();selection=self.f.selection(True)
        self.f.snap['inputs']['players'][0]['FlexProjection']=99;self.f.snap['created_at']='2026-09-21T19:00:00-04:00';self.f.snapshot('newer.json')
        d=self.report(selection);self.assertEqual(d['scopes']['supplied']['status'],'optimal')
        self.assertEqual(d['scopes']['snapshot']['status'],'unavailable_evidence')

    def test_read_limits_source_change_missing_db_and_linked_roots_fail_without_writes(self):
        chosen=self.f.selection();before=logical_db(self.f.db);files=source_bytes(self.f.root)
        with patch.object(be,'MAX_TOTAL_BYTES',10),self.assertRaises(OverflowError):he.capture(self.f.db,chosen)
        with self.assertRaises(ValueError):he.capture(self.f.root/'absent.sqlite',chosen)
        self.assertFalse((self.f.root/'absent.sqlite').exists())
        with patch.object(Path,'is_symlink',return_value=True),self.assertRaises(ValueError):he.capture(self.f.db,chosen)
        with self.assertRaises(Cancelled):he.capture(self.f.db,chosen,cancelled=lambda:True)
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))

    def test_mid_capture_mutation_is_rejected_at_final_receipt_check(self):
        chosen=self.f.selection();original=he.observed_entries
        def changed(*a,**kw):
            result=original(*a,**kw);Path(self.f.source['snapshot']).write_text('changed');return result
        with patch.object(he,'observed_entries',side_effect=changed),self.assertRaises(ValueError):he.capture(self.f.db,chosen)

    def test_checksum_valid_archive_selection_and_corruption(self):
        path=self.f.archive();d=self.f.one();selection=self.f.selection(True,d['build_evidence'][0]['archive_id'])
        self.assertEqual(self.report(selection)['scopes']['snapshot']['status'],'optimal')
        path.write_bytes(b'broken zip')
        self.assertEqual(self.report(selection)['scopes']['snapshot']['status'],'unavailable_evidence')

    def test_unknown_raw_rules_disable_only_snapshot_and_keep_original_inputs(self):
        self.f.snap['inputs']['rules']['mystery_hard_limit']=2;self.f.snapshot();before=copy.deepcopy(self.f.snap)
        d=self.report(self.f.selection(True));self.assertEqual(d['scopes']['supplied']['status'],'optimal')
        self.assertEqual(d['scopes']['snapshot']['status'],'unsupported_rules');self.assertEqual(before,self.f.snap)

    def test_raw_groups_and_unknown_identity_are_not_sanitized_away(self):
        self.f.snap['inputs']['rules']['groups']=[dict(type='never_together',player_keys=['100','101'])];self.f.snapshot()
        self.assertEqual(self.report(self.f.selection(True))['scopes']['snapshot']['points'],'52')
        for group in ({'type':'unknown','player_keys':['100']},{'type':'at_least_one','player_keys':['unknown']}):
            self.f.snap['inputs']['rules']['groups']=[group];self.f.snapshot()
            self.assertEqual(self.report(self.f.selection(True))['scopes']['snapshot']['status'],'unsupported_rules')

    def test_zero_caps_and_full_minima_have_distinct_role_semantics(self):
        for field,expected,role in (('MaxPct',46,None),('MaxCptPct',68,'FLEX'),('MaxFlexPct',70,'CPT'),('MinPct',70,'CPT'),('MinCptPct',70,'CPT')):
            self.f.snap['inputs']['players'][0]={**self.f.snap['inputs']['players'][0],field:100 if field.startswith('Min') else 0}
            self.f.snapshot();d=self.report(self.f.selection(True));r=d['scopes']['snapshot']
            self.assertEqual(r['points'],str(expected),field)
            if role:self.assertEqual(next(s['role'] for s in r['lineups'][0]['roster'] if s['key']=='100'),role)
            self.f.snap['inputs']['players'][0].pop(field)

    def test_portfolio_percentages_uniqueness_retained_and_recovery_are_unevaluated(self):
        self.f.snap['inputs']['players'][7]['MinPct']=1
        self.f.snap['inputs']['rules'].update(min_unique=8,max_team_pct=10,max_game_pct=0,retained_rows=['recorded'],exposure_recovery=True)
        self.f.snapshot();d=self.report(self.f.selection(True));r=d['scopes']['snapshot']
        self.assertEqual(r['points'],'70')
        self.assertEqual(sum(i['disposition']=='portfolio_only' for i in d['capture']['restricted']['rules']['inventory']),6)

    def test_platform_backup_missing_forecast_and_fade_do_not_shrink_universe(self):
        self.f.snap['inputs']['players'][0].update(NFLQBEligible=False,FlexProjection=None,ProjectionSource='Missing forecast',FadeFlex=True,FadeCpt=True)
        self.f.snapshot();d=self.report(self.f.selection(True))
        self.assertEqual(d['scopes']['supplied']['points'],'70');self.assertEqual(d['scopes']['snapshot']['points'],'46')

    def test_aggregate_summary_has_no_names_paths_source_or_contest_ids(self):
        capture=self.capture();report=hs.calculate(capture,seconds=5);text=hs.summary(report)
        for secret in ('Synthetic_User',str(self.f.root),self.f.source['hash'],self.f.salary['hash'],capture.data['provenance']['contest_id']):
            self.assertNotIn(secret,text)
        self.assertNotIn('CPT A',text);self.assertIn('CPT A',hs.summary(report,True))

    def test_cached_catalog_never_scans_source_files_or_solves(self):
        hi.reconcile(self.f.db)
        with patch.object(hi,'derive_contests',side_effect=AssertionError('scan')),patch.object(hs,'calculate',side_effect=AssertionError('solve')):
            self.assertEqual(len(he.source_catalog(self.f.db)),1)

    def test_known_zero_salary_and_negative_actuals_remain_known(self):
        pool=athletes();pool[0]['roles']['CPT']['salary']=0;pool[0]['roles']['FLEX']['salary']=0
        f=SourceFixture(pool=pool);self.addCleanup(f.close);self.f=f
        d=self.report();self.assertEqual(d['scopes']['supplied']['points'],'70')
        self.assertEqual(d['capture']['actual_coverage']['known'],8)

    def test_whole_pool_name_collision_outside_observed_rosters_blocks(self):
        def edit(rows):
            for row in rows[1:]:
                if row[2]=='H':row[2]='G Jr';row[1]='G Jr ('+row[3]+')'
        self.f.edit_source('salary',edit)
        self.assertIn('whole_pool_name_collision',self.blocked()['gates']['supplied']['blockers'])

    def test_salary_role_id_collision(self):
        def edit(rows):
            rows[-1][3]=rows[-2][3];rows[-1][1]=rows[-1][2]+' ('+rows[-1][3]+')'
        self.f.edit_source('salary',edit)
        self.assertIn('salary_role_id_collision',self.blocked()['gates']['supplied']['blockers'])

    def test_missing_captain_price_is_never_reconstructed_from_flex(self):
        selection=self.f.selection();path=Path(self.f.salary['snapshot'])
        with path.open(newline='',encoding='utf-8-sig') as handle:rows=list(csv.reader(handle))
        rows[2][5]=''
        with path.open('w',newline='',encoding='utf-8-sig') as handle:csv.writer(handle).writerows(rows)
        # Even an internally cataloged hash plus a stale, complete cached
        # manifest cannot supply a missing original role price.
        self.f.change('UPDATE analysis_sources SET hash=? WHERE hash=?',(ai._hash(path),self.f.salary['hash']))
        captured=self.capture(selection).data
        self.assertEqual(captured['gates']['supplied']['status'],'unavailable_evidence')
        self.assertFalse(captured['pool'])

    def test_unavailable_snapshot_request_does_not_disable_supplied_pool(self):
        selection=self.f.selection();selection['snapshot_digest']='unavailable'
        d=self.report(selection);self.assertEqual(d['scopes']['supplied']['status'],'optimal')
        self.assertEqual(d['scopes']['snapshot']['status'],'unavailable_evidence')

    def test_saved_snapshot_resolution_stays_fixed_and_missing_is_not_retargeted(self):
        self.f.snapshot('one.json');first=copy.deepcopy(self.f.snap)
        self.f.snap['inputs']['players'][0]['FlexProjection']=9;self.f.snapshot('two.json')
        data=self.f.one();hi.reconcile(self.f.db,snapshot_choices={data['identity_id']:be.digest(first)})
        selected=dict(contest_id=data['identity_id'],snapshot_digest=be.digest(first))
        self.assertEqual(self.report(selected)['scopes']['snapshot']['status'],'optimal')
        (self.f.root/'snapshots'/'one.json').unlink()
        before=logical_db(self.f.db);report=self.report(selected)
        self.assertEqual(report['scopes']['snapshot']['status'],'unavailable_evidence');self.assertEqual(before,logical_db(self.f.db))

    def test_period_variants_are_explicitly_unsupported(self):
        self.f.edit_source('results',lambda rows:(rows[0].append('ScoringPeriod'),[row.append('1st half') for row in rows[1:]]))
        self.assertIn('unsupported_scoring_period',self.blocked()['gates']['supplied']['blockers'])

    def test_snapshot_contradictory_flags_and_two_captain_locks_are_infeasible(self):
        for flags in ([{'LockCpt':True},{'LockCpt':True}],[{'LockFlex':True,'FadeFlex':True},{}]):
            original=copy.deepcopy(self.f.snap)
            for p,flag in zip(self.f.snap['inputs']['players'],flags):p.update(flag)
            self.f.snapshot();self.assertEqual(self.report(self.f.selection(True))['scopes']['snapshot']['status'],'infeasible')
            self.f.snap=original

    def test_classic_locks_fades_at_least_one_and_raw_policy_inventory(self):
        f=SourceFixture('classic');self.addCleanup(f.close);self.f=f
        f.snap['inputs']['players'][0]['FadeFlex']=True
        f.snap['inputs']['rules']['groups']=[dict(type='at_least_one',player_keys=['112'])]
        f.snap['inputs']['players'][1]['LockFlex']=True;f.snap['inputs']['recipe']['build_style']='Unrecorded style preferences are not hard rules'
        f.snapshot();d=self.report(f.selection(True));self.assertEqual(d['scopes']['snapshot']['status'],'optimal')
        keys=d['scopes']['snapshot']['lineups'][0]['signature'];self.assertNotIn('100',keys);self.assertIn('101',keys);self.assertIn('112',keys)
        self.assertTrue(any(i['disposition']=='strategy_policy' for i in d['capture']['restricted']['rules']['inventory']))

    def test_invalid_caps_and_nested_unknown_rules_only_block_restricted_scope(self):
        for recipe in ({'salary_cap':50001},{'deep_compute':{'unknown_local_minimum':4}}):
            original=copy.deepcopy(self.f.snap);self.f.snap['inputs']['recipe'].update(recipe);self.f.snapshot()
            d=self.report(self.f.selection(True));self.assertEqual(d['scopes']['supplied']['status'],'optimal')
            self.assertEqual(d['scopes']['snapshot']['status'],'unsupported_rules');self.f.snap=original

    def test_timeout_does_not_change_database_or_source_bytes(self):
        import bounded_solver
        captured=self.capture();before=logical_db(self.f.db);files=source_bytes(self.f.root)
        with patch.object(bounded_solver,'solve',side_effect=TimeoutError()):d=hs.calculate(captured,seconds=5).data
        self.assertEqual(d['scopes']['supplied']['status'],'time_limit')
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))
