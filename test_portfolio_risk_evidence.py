"""Synthetic, real SQLite/ZIP provenance and side-effect checks for RL-06."""
from contextlib import closing, ExitStack
import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import historical_identity as hi
import review_build_evidence as be
import portfolio_risk_evidence as evidence
from portfolio_risk import calculate, Cancelled, EXPORT_UNAVAILABLE
import test_historical_identity as fixtures


def qualified_patterns(f, patterns=('AB','A','B','')):
    """Full 9-athlete salary/snapshot pool, legal six-slot output occurrences."""
    original=fixtures.salary_file
    def expanded(path,kind):
        original(path,kind)
        with path.open('a',newline='',encoding='utf-8') as handle:
            writer=csv.writer(handle)
            for i,name,pos,team in ((6,'Golf','QB','SEA'),(7,'Hotel','RB','NE'),(8,'India','WR','SEA')):
                for role in ('CPT','FLEX'):
                    ident=100+i+(1000 if role=='CPT' else 0)
                    writer.writerow([pos,f'{name} ({ident})',name,ident,role,7500 if role=='CPT' else 5000,
                                     'NE@SEA 09/21/2026 08:15PM ET',team,0])
        return path
    with patch.object(fixtures,'salary_file',side_effect=expanded):
        f.fixture(scores=[8]*6+[None]*3)
    from build_snapshots import fingerprint
    for i,p in enumerate(f.snap['inputs']['players']):
        value=20 if i==0 else 10 if i==1 else 5
        p.update(FlexProjection=value,CptProjection=1.5*value,NFLDepthOrder=1 if i<6 else 2,
            NFLAvailability='STARTER' if i<6 else 'BACKUP 2',InjurySource='Sleeper',
            LiveStatusUpdatedAt='2026-09-21T17:00:00-04:00',NFLQBEligible=i==0 if p['Position']=='QB' else None)
    f.snap['input_id']=fingerprint(f.snap['inputs']);f.snapshot();path=f.archive()
    def edit(payload):
        players=f.snap['inputs']['players']
        payload['lineups']=[dict(slots=[dict(slot=role,player=players[i]) for role,i in
            zip(['CPT']+['FLEX']*5,[2,3,4,5,0 if 'A' in pattern else 6,1 if 'B' in pattern else 7])]) for pattern in patterns]
        payload['metadata']['output_count']=len(patterns)
    rewrite_archive(path,edit)
    d=f.one(True)
    return dict(kind='archive',contest_id=d['identity_id'],archive_id=d['build_evidence'][0]['archive_id'])


def rewrite_archive(path, edit):
    with zipfile.ZipFile(path) as archive:
        files = {n:archive.read(n) for n in archive.namelist()}
    payload = json.loads(files['lineups.json'])
    edit(payload)
    files['lineups.json'] = json.dumps(payload).encode()
    files['manifest.json'] = json.dumps(dict(metadata=payload['metadata'],sha256={
        n:hashlib.sha256(raw).hexdigest() for n,raw in files.items() if n!='manifest.json'})).encode()
    with zipfile.ZipFile(path,'w') as archive:
        for name,raw in files.items():
            archive.writestr(name,raw)


def repeat_archive(path, count):
    def edit(payload):
        payload['lineups'] = [copy.deepcopy(payload['lineups'][0]) for _ in range(count)]
        payload['metadata']['output_count'] = count
    rewrite_archive(path,edit)


def logical_db(path):
    with closing(sqlite3.connect(path)) as conn:
        return tuple(conn.iterdump())


def source_bytes(root):
    # SQLite reader sidecars are separately owned by SQLite, not immutable inputs.
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()
            and p.suffix in ('.csv','.json','.zip')}


def export_fixture(path, kind='showdown', count=2):
    """Minimal legacy schema intentionally lacks optional modern columns."""
    with closing(sqlite3.connect(path)) as conn,conn:
        conn.executescript('''CREATE TABLE exports(export_id TEXT,created_at TEXT,sport TEXT,contest_type TEXT);
            CREATE TABLE lineups(lineup_id TEXT,export_id TEXT);
            CREATE TABLE lineup_players(lineup_id TEXT,player_key TEXT,player_id TEXT,slot TEXT,
                name TEXT,team TEXT,position TEXT,projection REAL,injury_status TEXT);''')
        conn.execute('INSERT INTO exports VALUES (?,?,?,?)',('private-export','2026-09-22T12:00:00','NFL',kind))
        size = 6 if kind=='showdown' else 9
        for i in range(count):
            ident = str(i)
            conn.execute('INSERT INTO lineups VALUES (?,?)',(ident,'private-export'))
            for j in range(size):
                # Rotate Captain while preserving the recorded athlete base key.
                captain = kind=='showdown' and j==i%size
                flex_order = j+1 if j<i%size else j
                slot = ('CPT' if captain else f'FLEX{flex_order}') if kind=='showdown' else f'SLOT{j+1}'
                conn.execute('INSERT INTO lineup_players VALUES (?,?,?,?,?,?,?,?,?)',
                    (ident,f'base-{j}',f'{"cpt" if captain else "flex"}-{j}',slot,f'Private Athlete {j}',
                     'AAA','QB' if j==0 else 'WR',0,'unknown'))


class RiskArchiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.HistoricalIdentityTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)

    def setup_source(self,kind='showdown',**kwargs):
        self.f.fixture(kind,**kwargs); self.snap_path=self.f.snapshot(); self.path=self.f.archive()
        return self.selection()

    def selection(self):
        d = self.f.one(True)
        return dict(kind='archive',contest_id=d['identity_id'],archive_id=d['build_evidence'][0]['archive_id'])

    def capture(self,selection,**kwargs):
        return evidence.capture(self.f.db,selection,**kwargs)

    def test_legacy_reader_api_and_occurrence_weighted_full_report(self):
        for kind in ('showdown','classic'):
            with self.subTest(kind=kind):
                f=fixtures.HistoricalIdentityTests();f.setUp();self.addCleanup(f.tearDown)
                f.fixture(kind);f.snapshot();path=f.archive();repeat_archive(path,7)
                old=be.Reader(lambda:False).archive(path)
                new=be.Reader(lambda:False).archive_details(path)
                self.assertEqual(len(old),4);self.assertEqual(old,new[:4]);self.assertEqual(len(new[4]),7)
                self.assertEqual(len(old[2]),1)
                d=f.one(True);selection=dict(kind='archive',contest_id=d['identity_id'],archive_id=d['build_evidence'][0]['archive_id'])
                report=calculate(evidence.capture(f.db,selection),['100']).data
                self.assertEqual((report['R'],report['N'],report['U'],report['M']),(7,7,1,7))
                self.assertEqual(report['target_exposures'][0]['any']['count'],7)

    def test_distinct_builds_and_copied_archives_never_combine(self):
        self.setup_source(); repeat_archive(self.path,3)
        second=self.f.archive('second.zip',created_at='2026-09-21T18:11:00-04:00');repeat_archive(second,5)
        shutil.copyfile(self.path,self.path.with_name('identical-copy.zip'))
        d=self.f.one(True);self.assertEqual(len(d['build_evidence']),2)
        for archive in d['build_evidence']:
            cap=self.capture(dict(kind='archive',contest_id=d['identity_id'],archive_id=archive['archive_id']))
            self.assertEqual(cap.data['source_count'],archive['output_count'])
        self.assertEqual({a['output_count'] for a in d['build_evidence']},{3,5})

    def test_incomplete_actuals_do_not_disable_qualified_forecasts(self):
        selection=self.setup_source(scores=[0,-1,None,10,10,10])
        self.assertEqual(self.f.one()['state'],'BUILD_QUALIFIED')
        d=calculate(self.capture(selection),['100']).data
        self.assertEqual(d['M'],1);self.assertEqual(len(d['scenarios']),5)
        self.assertNotIn('actual_score',d['capture']);self.assertEqual(d['capture']['pool']['100']['base_projection'],8)

    def test_invalid_selected_id_and_candidates_never_become_qualified(self):
        selection=self.setup_source()
        with self.assertRaisesRegex(ValueError,'not freshly qualified'):
            self.capture(dict(selection,archive_id='not-an-archive'))
        changed=copy.deepcopy(self.f.snap);changed['inputs']['recipe']['requested_lineups']=99
        from build_snapshots import fingerprint
        changed['input_id']=fingerprint(changed['inputs'])
        changed['created_at']='2026-09-21T18:20:00-04:00';self.f.snapshot(changed)
        d=self.f.one(True)
        self.assertEqual(len(d['build_candidates']),1);self.assertFalse(d['build_evidence'])
        with self.assertRaisesRegex(ValueError,'not freshly qualified'):
            self.capture(selection)

    def test_exact_saved_timestamp_resolution_wins_over_embedded_snapshot(self):
        self.setup_source()
        newer=copy.deepcopy(self.f.snap);newer['created_at']='2026-09-21T18:05:00-04:00'
        self.f.snapshot(newer)
        d=self.f.one();self.assertIn('snapshot_timestamp_conflict',d['conflicts'])
        hi.reconcile(self.f.db,snapshot_choices={d['identity_id']:be.digest(newer)})
        selection=self.selection();before=logical_db(self.f.db)
        cap=self.capture(selection)
        self.assertEqual(cap.data['provenance']['snapshot_time'],newer['created_at'])
        self.assertEqual(cap.data['provenance']['snapshot_digest'],be.digest(newer))
        self.snap_path.unlink()
        with self.assertRaisesRegex(ValueError,'Qualified salary'):
            self.capture(selection)
        self.assertEqual(logical_db(self.f.db),before)

    def test_changed_salary_invalidates_without_retargeting_saved_pair(self):
        selection=self.setup_source(dated=False)
        before=logical_db(self.f.db)
        Path(self.f.salary['snapshot']).write_text('changed source',encoding='utf-8')
        with self.assertRaises(ValueError):self.capture(selection)
        self.assertEqual(logical_db(self.f.db),before)

    def test_deleted_archive_invalidates_without_rewriting_cached_coverage(self):
        selection=self.setup_source();before=logical_db(self.f.db);self.path.unlink()
        with self.assertRaises(ValueError):self.capture(selection)
        self.assertEqual(logical_db(self.f.db),before)

    def test_mid_capture_snapshot_and_archive_changes_reject_mixed_report(self):
        selection=self.setup_source()
        for path in (self.snap_path,self.path):
            with self.subTest(path=path.name):
                original=path.read_bytes()
                def mutate(phase):
                    if phase=='Revalidating frozen source receipts':path.write_bytes(original+b' ')
                with self.assertRaisesRegex(ValueError,'changed during capture'):
                    self.capture(selection,progress=mutate)
                path.write_bytes(original)

    def test_archive_contradictions_fail_closed_against_full_frozen_pool(self):
        selection=self.setup_source();original=self.path.read_bytes()
        changes=[dict(FlexID='101'),dict(CptID='1101'),dict(Team='ZZZ'),dict(Position='DST'),
                 dict(FlexSalary=1),dict(CptSalary=1),dict(GameInfo='OTHER'),dict(Name='Not the archived athlete')]
        for change in changes:
            with self.subTest(change=change):
                self.path.write_bytes(original)
                rewrite_archive(self.path,lambda d:d['lineups'][0]['slots'][0]['player'].update(change))
                with self.assertRaises(ValueError):self.capture(selection)
        self.path.write_bytes(original)

    def test_absent_redundant_archive_fields_use_exact_ids_without_enrichment(self):
        selection=self.setup_source()
        def edit(d):
            for slot in d['lineups'][0]['slots']:
                slot['player']={k:v for k,v in slot['player'].items() if k in ('Name','FlexID')}
        rewrite_archive(self.path,edit)
        self.assertEqual(calculate(self.capture(selection)).data['N'],1)

    def test_same_archive_identity_conflicting_occurrence_payloads_block(self):
        selection=self.setup_source();repeat_archive(self.path,2)
        other=self.path.with_name('copy.zip');shutil.copyfile(self.path,other)
        rewrite_archive(other,lambda d:d['lineups'][0]['slots'][0]['player'].update(Team='ZZZ'))
        selection=self.selection()
        with self.assertRaisesRegex(ValueError,'conflicting occurrence'):
            self.capture(selection)

    def test_duplicate_or_ambiguous_athletes_and_classic_wrong_slot_are_blocked(self):
        selection=self.setup_source('classic');original=self.path.read_bytes()
        for mutate in (
            lambda d:d['lineups'][0]['slots'][1].update(player=d['lineups'][0]['slots'][0]['player']),
            lambda d:d['lineups'][0]['slots'][0]['player'].update(FlexID='101')):
            self.path.write_bytes(original);rewrite_archive(self.path,mutate)
            with self.assertRaises(ValueError):
                self.capture(selection)
        pool=copy.deepcopy(self.f.snap['inputs']['players']);pool[1]['FlexID']=pool[0]['FlexID']
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            evidence._archive_rosters([],pool,'classic',lambda:False)

    def test_full_snapshot_roles_survive_compact_archive_slot_omissions(self):
        self.f.fixture()
        for p in self.f.snap['inputs']['players']:
            p.update(NFLDepthOrder=2,NFLAvailability='BACKUP 2',InjurySource='Sleeper',
                     LiveStatusUpdatedAt='2026-09-21T17:00:00-04:00')
        from build_snapshots import fingerprint
        self.f.snap['input_id']=fingerprint(self.f.snap['inputs'])
        self.f.snapshot();self.path=self.f.archive()
        def edit(d):
            for s in d['lineups'][0]['slots']:
                for field in ('NFLDepthOrder','NFLAvailability','InjurySource','LiveStatusUpdatedAt'):
                    s['player'].pop(field,None)
        rewrite_archive(self.path,edit)
        cap=self.capture(self.selection())
        self.assertEqual(cap.data['pool']['100']['role']['state'],'timely')
        self.assertEqual(cap.data['pool']['100']['role']['raw']['NFLDepthOrder'],2)

    def test_read_budget_checksums_allowlist_and_row_limit_stay_enforced(self):
        selection=self.setup_source();original=self.path.read_bytes()
        with patch.object(be,'MAX_TOTAL_BYTES',10):
            with self.assertRaises((ValueError,OverflowError)):self.capture(selection)
        with patch.object(be,'MAX_LINEUPS',0):
            with self.assertRaises(ValueError):self.capture(selection)
        for name,content in (('untrusted.py',b'bad'),('lineups.json',b'bad hash')):
            self.path.write_bytes(original)
            with zipfile.ZipFile(self.path,'a') as archive:archive.writestr(name,content)
            with self.assertRaises(ValueError):self.capture(selection)
        self.path.write_bytes(original)
        with patch.object(be,'MAX_FILES',0):
            with self.assertRaises(ValueError):self.capture(selection)

    def test_cached_catalog_reads_no_sources_and_does_not_authorize_capture(self):
        selection=self.setup_source()
        with patch.object(be.Reader,'paths',side_effect=AssertionError('source scan')):
            catalog=evidence.source_catalog(self.f.db)
        self.assertEqual(catalog['contests'][0]['archives'][0]['archive_id'],selection['archive_id'])
        self.path.unlink()
        with self.assertRaises(ValueError):self.capture(selection)

    def test_changed_zip_member_hash_and_invalid_zip_fail_closed(self):
        selection=self.setup_source()
        with zipfile.ZipFile(self.path) as archive:files={name:archive.read(name) for name in archive.namelist()}
        files['lineups.json']+=b' '
        with zipfile.ZipFile(self.path,'w') as archive:
            for name,raw in files.items():archive.writestr(name,raw)
        with self.assertRaisesRegex(ValueError,'checksum'):be.Reader(lambda:False).archive_details(self.path)
        with self.assertRaises(ValueError):self.capture(selection)
        self.path.write_bytes(b'not a ZIP')
        with self.assertRaises(ValueError):self.capture(selection)

    def test_database_and_sources_immutable_on_success_cancel_and_failure(self):
        selection=self.setup_source(dated=False);before=logical_db(self.f.db);files=source_bytes(self.f.root)
        with ExitStack() as stack:
            for name in ('learning_db._connect','learning_db.history_db_path','historical_identity.reconcile',
                         'build_archives.save_build_archive','analysis_imports.import_folders',
                         'nfl_eligibility.apply_qb_eligibility','nfl_eligibility.eligible_players',
                         'nfl_simulation.simulate_nfl_contest','nfl_simulation.simulate_nfl_portfolio_contest',
                         'projection_sensitivity.run_projection','pulp.LpProblem.solve'):
                stack.enter_context(patch(name,side_effect=AssertionError('Forbidden writer/enrichment')))
            report=calculate(self.capture(selection),['100','101']);calculate(self.capture(selection),['100'])
            with self.assertRaises(Cancelled):self.capture(selection,cancelled=lambda:True)
            with self.assertRaises(ValueError):self.capture(dict(selection,archive_id='unknown'))
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))
        self.assertEqual(report.data['capture']['source_count'],1)

    def test_full_pool_qualified_archive_pair_oracle_zero_alternative_and_no_hindsight(self):
        selection=qualified_patterns(self.f)
        cap=self.capture(selection);d=calculate(cap,['100','101'],['106','108']).data
        self.assertEqual(d['pair']['buckets'],dict(both=1,a_only=1,b_only=1,neither=1))
        self.assertEqual(d['target_exposures'][0]['any']['pct'],50)
        absent=next(e for e in d['exposures'] if e['key']=='108')
        self.assertEqual(absent['any']['count'],0);self.assertEqual(absent['any']['denominator'],4)
        self.assertEqual(d['scenarios'][-1]['direct_change']['mean'],15)
        self.assertEqual(d['capture']['pool']['106']['role']['raw']['NFLQBEligible'],False)
        before=source_bytes(self.f.root);calculate(cap,['100'],['106'])
        self.assertEqual(before,source_bytes(self.f.root))

    def test_real_archive_captain_direct_change_27_5_with_unknown_unselected(self):
        self.f.fixture()
        from build_snapshots import fingerprint
        players=self.f.snap['inputs']['players']
        players[0].update(FlexProjection=20,CptProjection=30)
        players[1].update(FlexProjection=10,CptProjection=15)
        players[2].update(FlexProjection=None)
        self.f.snap['input_id']=fingerprint(self.f.snap['inputs']);self.f.snapshot();self.path=self.f.archive()
        d=calculate(self.capture(self.selection()),['100','101']).data
        case=next(s for s in d['scenarios'] if s['retention']==[.25,.5])
        self.assertEqual((case['M'],case['M_delta']),(0,1))
        self.assertEqual(case['direct_change']['mean'],27.5);self.assertIsNone(case['baseline_mean'])

    def test_cancellation_during_inventory_and_math_keeps_sources(self):
        selection=self.setup_source();before=logical_db(self.f.db);files=source_bytes(self.f.root)
        event=threading.Event()
        from analysis_imports import ImportCancelled
        with self.assertRaises((Cancelled,ImportCancelled)) as caught:
            self.capture(selection,cancelled=event.is_set,progress=lambda _:event.set())
        self.assertIsInstance(caught.exception,(Cancelled,ImportCancelled))
        cap=self.capture(selection)
        with self.assertRaises(Cancelled):calculate(cap,['100'],cancelled=lambda:True)
        self.assertEqual(before,logical_db(self.f.db));self.assertEqual(files,source_bytes(self.f.root))


class RiskSavedExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'exports.sqlite'
        self.selection=dict(kind='saved_export',export_id='private-export')

    def change(self,sql):
        with closing(sqlite3.connect(self.db)) as conn,conn:conn.execute(sql)

    def capture(self):return evidence.capture(self.db,self.selection)

    def test_missing_database_never_creates_storage(self):
        self.assertEqual(evidence.source_catalog(self.db)['exports'],[])
        with self.assertRaises(ValueError):self.capture()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_original_rows_captain_identity_legacy_zero_and_naive_time(self):
        export_fixture(self.db);before=logical_db(self.db)
        report=calculate(self.capture(),['base-0','base-1']).data
        self.assertEqual((report['R'],report['N'],report['U']),(2,2,2))
        self.assertEqual(report['target_exposures'][0]['any']['count'],2)
        self.assertEqual(report['target_exposures'][0]['captain']['count'],1)
        self.assertEqual((report['M'],report['M_delta']),(0,0))
        self.assertEqual(report['stress_reason'],EXPORT_UNAVAILABLE)
        self.assertIsNone(report['capture']['slate_date']);self.assertEqual(report['context']['game'],0)
        self.assertIsNone(report['capture']['pool']['base-0']['base_projection'])
        self.assertEqual(report['capture']['pool']['base-0']['legacy_metadata']['RecordedProjection'],0)
        self.assertEqual(report['capture']['pool']['base-0']['role']['state'],'unknown')
        self.assertEqual(logical_db(self.db),before)

    def test_classic_slot_labels_do_not_infer_exact_upload_slot(self):
        export_fixture(self.db,'classic');d=calculate(self.capture()).data
        self.assertEqual((d['N'],d['U']),(2,1))
        self.assertIn('do not establish',d['capture']['provenance']['upload_slots'])
        self.assertIsNone(d['exposures'][0]['captain']['pct'])

    def test_different_captain_regular_legacy_forecasts_remain_unqualified_metadata(self):
        export_fixture(self.db)
        self.change("UPDATE lineup_players SET projection=12 WHERE slot='CPT'")
        self.change("UPDATE lineup_players SET projection=8 WHERE slot!='CPT'")
        cap=self.capture();p=cap.data['pool']['base-0']
        self.assertEqual(p['legacy_metadata']['ByRole']['Captain'][0]['projection'],12)
        self.assertEqual(p['legacy_metadata']['ByRole']['Regular'][0]['projection'],8)
        self.assertIsNone(p['base_projection']);self.assertEqual(calculate(cap,['base-0']).data['M'],0)

    def test_optional_missing_context_columns_disable_only_context_metrics(self):
        export_fixture(self.db)
        for name in ('team','position','projection','injury_status'):
            self.change('ALTER TABLE lineup_players DROP COLUMN '+name)
        before=logical_db(self.db);d=calculate(self.capture()).data
        self.assertEqual(d['N'],2);self.assertEqual(d['context']['team'],0);self.assertFalse(d['qb_receivers'])
        self.assertEqual(logical_db(self.db),before)

    def test_required_missing_identity_column_fails_without_backfill(self):
        export_fixture(self.db);self.change('ALTER TABLE lineup_players DROP COLUMN player_key')
        before=logical_db(self.db)
        with self.assertRaisesRegex(ValueError,'identity columns'):self.capture()
        self.assertEqual(logical_db(self.db),before)

    def test_malformed_slots_and_duplicate_athlete_are_disclosed(self):
        export_fixture(self.db,count=3)
        self.change("UPDATE lineup_players SET slot='FLEX999' WHERE lineup_id='0' AND slot='FLEX1'")
        self.change("UPDATE lineup_players SET player_key='base-0' WHERE lineup_id='1' AND player_key='base-1'")
        d=calculate(self.capture()).data
        self.assertEqual((d['R'],d['N']),(3,1));self.assertEqual(sum(d['capture']['rejected'].values()),2)

    def test_cross_role_id_and_ambiguous_recorded_name_key_rejected(self):
        for sql in ("UPDATE lineup_players SET player_id='cpt-0' WHERE player_key='base-1'",
                    "UPDATE lineup_players SET name='Other athlete' WHERE player_key='base-0' AND lineup_id='1'",
                    "UPDATE lineup_players SET player_id='other-flex-id' WHERE player_key='base-2' AND lineup_id='1'"):
            with self.subTest(sql=sql):
                if self.db.exists():self.db.unlink()
                export_fixture(self.db);self.change(sql)
                d=calculate(self.capture()).data
                self.assertEqual(d['N'],0);self.assertIsNone(d['alternative_union']['pct'])

    def test_missing_rows_and_read_limit_report_R_separately_from_N(self):
        export_fixture(self.db,count=4)
        self.change("DELETE FROM lineup_players WHERE lineup_id='0' AND player_key='base-0'")
        with patch.object(be,'MAX_LINEUPS',2):d=calculate(self.capture()).data
        self.assertEqual((d['R'],d['N']),(4,1));self.assertEqual(d['capture']['limits'],{'occurrences':2})
        self.assertEqual(sum(d['capture']['rejected'].values()),3)

    def test_no_export_batch_combination_or_slate_inference(self):
        export_fixture(self.db)
        self.change("INSERT INTO exports VALUES ('other','2026-09-21','NFL','showdown')")
        self.change("INSERT INTO lineups VALUES ('other-row','other')")
        d=calculate(self.capture()).data
        self.assertEqual(d['R'],2);self.assertIn('Unknown or mixed',d['capture']['provenance']['slate_context'])

    def test_reader_transaction_rejects_writes_and_pins_root(self):
        export_fixture(self.db)
        with evidence.read_database(self.db) as conn:
            with self.assertRaises(sqlite3.OperationalError):conn.execute('DELETE FROM exports')
        with self.assertRaisesRegex(ValueError,'pinned database'):
            evidence.capture(self.db,self.selection,history_root=self.root/'elsewhere')
