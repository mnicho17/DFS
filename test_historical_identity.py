"""RL-05A synthetic sources only; no production history or external services."""
import copy
import csv
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import analysis_imports as ai
from build_snapshots import create_snapshot, save_snapshot
import historical_identity as hi
from learning_db import init_historical_import_tables
from test_analysis_imports import salary_file


class HistoricalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rl05a-')
        self.root = Path(self.tmp.name)
        self.db = self.root/'history.sqlite'
        self.results = self.root/'results'; self.results.mkdir()
        self.salaries = self.root/'salaries'; self.salaries.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def query(self, sql, args=()):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql,args).fetchall()

    def change(self, sql, args=()):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(sql,args)

    def fixture(self, kind='showdown', dated=True, scores=None, entries=30):
        salary = salary_file(self.salaries/'salary.csv',kind)
        manifest = ai._salary_manifest(salary,lambda:False)
        bases = [p for p in manifest['players'] if p['role']!='CPT']
        roles = ['CPT']+['FLEX']*5 if kind=='showdown' else ['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']
        roster = ' '.join(role+' '+p['name'] for role,p in zip(roles,bases))
        values = scores if scores is not None else [10]*len(bases)
        total = sum(v or 0 for v in values) + ((values[0] or 0)*.5 if kind=='showdown' else 0)
        result = self.results/'synthetic.csv'
        with result.open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f)
            w.writerow(['Rank','EntryId','EntryName','Points','Lineup','ContestId','SlateDate',
                        'Player','Roster Position','FPTS'])
            for i in range(entries):
                side = [bases[i]['name'], 'FLEX' if kind=='showdown' else bases[i]['position'], values[i]] if i<len(bases) else ['','','']
                w.writerow([i+1,1000+i,'Synthetic_User',total,roster,'123','2026-09-21' if dated else '',*side])
        imported=ai.import_folders(self.results,self.salaries,db_path=str(self.db),username='Synthetic_User')
        self.assertFalse(imported['errors'])
        with closing(sqlite3.connect(self.db)) as conn:
            sources=ai._sources(conn)
        self.source=next(s for s in sources if s['kind']=='results')
        self.salary=next(s for s in sources if s['kind']=='salary')
        if not dated:
            ai.save_pair(self.source['hash'],self.salary['hash'],db_path=str(self.db),confirm_date=True)
        players=[]
        for row in bases:
            captain=next((p for p in manifest['players'] if p['name']==row['name'] and p['role']=='CPT'),None)
            players.append(dict(Name=row['name'], Position=row['position'],Team=row['team'],Opponent=row['opponent'],
                FlexID=row['id'],CptID=captain['id'] if captain else None,FlexSalary=row['salary'],
                CptSalary=captain['salary'] if captain else None,GameInfo=row['raw']['gameinfo'],
                FlexProjection=8,CptProjection=12,ProjectionSource='Synthetic frozen forecast'))
        self.snap=create_snapshot(players,dict(sport='NFL',contest_kind=kind),{})
        self.snap['created_at']='2026-09-21T18:00:00-04:00'
        self.roles=roles
        return result,salary

    def snapshot(self, snap=None):
        snap=snap or self.snap
        path=self.root/'snapshots'/(snap['input_id']+'.json')
        save_snapshot(str(path),snap)
        return path

    def archive(self, name='build.zip', snap=None, **changes):
        snap=snap or self.snap
        meta=dict(schema_version=1,record_type='generated_outputs',sport='NFL',
                  kind=snap['inputs']['recipe']['contest_kind'],input_id=snap['input_id'],
                  output_count=1,created_at='2026-09-21T18:10:00-04:00',build_status='completed')
        meta.update(changes)
        rows=[dict(slots=[dict(slot=s,player=p) for s,p in zip(self.roles,snap['inputs']['players'])])]
        files={'input-snapshot.json':json.dumps(snap).encode(),
               'lineups.json':json.dumps(dict(metadata=meta,lineups=rows)).encode()}
        files['manifest.json']=json.dumps(dict(metadata=meta,sha256={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})).encode()
        folder=self.root/'build-archives';folder.mkdir(exist_ok=True)
        path=folder/name
        with zipfile.ZipFile(path,'w') as z:
            for k,v in files.items():z.writestr(k,v)
        return path

    def one(self, persist=False):
        rows=hi.reconcile(self.db) if persist else hi.qualified_contests(self.db)
        self.assertEqual(len(rows),1)
        return rows[0].data

    def replace_result(self, edit):
        """Create a new immutable synthetic result revision for score fixtures."""
        path=Path(self.source['snapshot'])
        with path.open(newline='',encoding='utf-8-sig') as f:
            reader=csv.reader(f);rows=list(reader)
        edit(rows)
        with path.open('w',newline='',encoding='utf-8-sig') as f:csv.writer(f).writerows(rows)
        new_hash=ai._hash(path)
        self.change('UPDATE analysis_sources SET hash=? WHERE hash=?',(new_hash,self.source['hash']))
        self.change('UPDATE analysis_salary_pairs SET result_hash=? WHERE result_hash=?',(new_hash,self.source['hash']))
        self.source['hash']=new_hash

    def test_legacy_null_observation_qualifies_without_backfill(self):
        self.fixture(dated=False,entries=120)
        self.change('UPDATE historical_results SET slate_date=NULL')
        before=self.query('SELECT * FROM historical_results')
        self.snapshot();self.archive()
        d=self.one(True)
        self.assertEqual(d['state'],'OUTCOME_QUALIFIED')
        self.assertEqual(d['identity']['slate_date'],'2026-09-21')
        self.assertEqual(d['identity']['format'],'showdown')
        self.assertEqual(d['results_evidence']['observed_dates'],[])
        self.assertEqual(before,self.query('SELECT * FROM historical_results'))

    def test_explicit_date_preserved(self):
        self.fixture();self.change("UPDATE historical_results SET slate_date='2026-09-21'")
        d=self.one()
        self.assertEqual(d['results_evidence']['observed_dates'],['2026-09-21'])
        self.assertEqual(d['state'],'SALARY_QUALIFIED')

    def test_explicit_date_conflict_blocks(self):
        self.fixture();self.change("UPDATE historical_results SET slate_date='2026-09-22'")
        d=self.one()
        self.assertEqual(d['state'],'CANDIDATE')
        self.assertIn('explicit_result_salary_date_conflict',d['conflicts'])
        self.assertEqual(d['identity']['slate_date'],'2026-09-22')

    def test_explicit_sport_conflict_blocks(self):
        self.fixture();self.change("UPDATE historical_results SET sport='NBA'")
        self.assertIn('explicit_result_salary_sport_conflict',self.one()['conflicts'])

    def test_classic_showdown_mismatch_rejected(self):
        self.fixture()
        self.change("UPDATE historical_results SET raw_json=json_set(raw_json,'$.contest type','classic')")
        self.assertIn('explicit_result_salary_format_conflict',self.one()['conflicts'])

    def test_both_formats_reach_full_chain(self):
        self.fixture('classic');self.snapshot();self.archive()
        self.assertEqual(self.one()['state'],'OUTCOME_QUALIFIED')

    def test_captain_flex_separate_ids_and_scaled_scores(self):
        self.fixture();d=self.one()
        scores=d['actual_score_evidence']['scores']
        alpha=[r for r in scores if r['name']=='Alpha']
        self.assertEqual({r['player_id'] for r in alpha},{'100','1100'})
        self.assertEqual({r['role']:r['actual'] for r in alpha},{'FLEX':10,'CPT':15})

    def test_unique_automatic_qualification_does_not_create_saved_pair(self):
        self.fixture();self.change('DELETE FROM analysis_salary_pairs')
        d=self.one(True)
        self.assertEqual(d['state'],'SALARY_QUALIFIED')
        self.assertEqual(self.query('SELECT * FROM analysis_salary_pairs'),[])
        self.assertIn('not_saved_pair',d['salary_evidence']['association_method'])

    def test_ambiguous_revisions_stay_candidate(self):
        self.fixture();self.change('DELETE FROM analysis_salary_pairs')
        salary_file(self.salaries/'revision.csv',offset=100)
        ai.import_folders(self.results,self.salaries,db_path=str(self.db))
        d=self.one()
        self.assertEqual(d['state'],'CANDIDATE')
        self.assertIn('ambiguous_salary_revisions',d['blockers'])

    def test_explicit_saved_revision_stays_stable_with_alternative(self):
        self.fixture(dated=False)
        salary_file(self.salaries/'revision.csv',offset=100)
        ai.import_folders(self.results,self.salaries,db_path=str(self.db))
        for _ in range(2):
            self.assertEqual(self.one(True)['salary_evidence']['revision_hash'],self.salary['hash'])

    def test_renamed_identical_sources_recognized(self):
        result,salary=self.fixture();before=self.one(True)
        result.rename(result.with_name('moved.csv'));salary.rename(salary.with_name('moved.csv'))
        ai.import_folders(self.results,self.salaries,db_path=str(self.db))
        self.assertEqual(self.one(True),before)

    def test_changed_content_never_retargets_saved_revision(self):
        self.fixture();self.one(True)
        Path(self.salary['snapshot']).write_text('changed',encoding='utf-8')
        salary_file(self.salaries/'revision.csv',offset=100)
        ai.import_folders(self.results,self.salaries,db_path=str(self.db))
        d=self.one(True)
        self.assertEqual(d['state'],'CANDIDATE')
        self.assertEqual(d['salary_evidence']['revision_hash'],self.salary['hash'])
        self.assertIn('invalid_saved_salary_revision',d['conflicts'])

    def test_pregame_snapshot_without_archive_qualifies(self):
        self.fixture();self.snapshot()
        self.assertEqual(self.one()['state'],'SNAPSHOT_QUALIFIED')

    def test_postgame_snapshot_rejected(self):
        self.fixture();self.snap['created_at']='2026-09-22T01:00:00Z';self.snapshot()
        d=self.one()
        self.assertEqual(d['state'],'SALARY_QUALIFIED')
        self.assertIn('postgame_or_unknown_time_snapshots_rejected',d['limitations'])

    def test_ambiguous_latest_snapshots_not_first(self):
        self.fixture();self.snapshot()
        alternate=copy.deepcopy(self.snap)
        alternate['inputs']['players'][0]['FlexProjection']=99
        alternate['input_id']=hi._digest(alternate['inputs'])
        self.snapshot(alternate)
        self.assertIn('conflicting_latest_snapshots',self.one()['conflicts'])

    def test_explicit_contest_id_preferred(self):
        self.fixture();self.snapshot()
        alternate=copy.deepcopy(self.snap);alternate['inputs']['players'][0]['FlexProjection']=99
        alternate['input_id']=hi._digest(alternate['inputs']);self.snapshot(alternate)
        (self.root/'contest-snapshots.json').write_text(json.dumps({'123':[self.snap['input_id']]}))
        self.assertEqual(self.one()['snapshot_evidence']['input_id'],self.snap['input_id'])

    def test_exact_archive_input_link_only(self):
        self.fixture();self.snapshot();self.archive(input_id='a'*64)
        d=self.one()
        self.assertEqual(d['state'],'SNAPSHOT_QUALIFIED')
        self.assertEqual(d['build_evidence'],[])
        self.assertTrue(d['source_issues']['invalid_snapshot_or_archive'])

    def test_multiple_builds_preserved_without_submission_claim(self):
        self.fixture();self.snapshot();self.archive(audit_id='one');self.archive('two.zip',audit_id='two')
        d=self.one()
        self.assertEqual(len(d['build_evidence']),2)
        self.assertEqual({a['submission'] for a in d['build_evidence']},{'not_established'})

    def test_unknown_eligible_score_prevents_outcome(self):
        self.fixture(scores=[10,10,None,10,10,10]);self.snapshot();self.archive()
        d=self.one()
        self.assertEqual(d['state'],'BUILD_QUALIFIED')
        self.assertEqual(d['actual_score_evidence']['unknown_scores'],2)

    def test_zero_actual_valid(self):
        self.fixture(scores=[0]*6);self.snapshot();self.archive()
        self.assertEqual(self.one()['state'],'OUTCOME_QUALIFIED')

    def test_negative_actual_valid(self):
        self.fixture(scores=[-2,10,10,10,10,10]);self.snapshot();self.archive()
        self.assertEqual(self.one()['state'],'OUTCOME_QUALIFIED')

    def test_missing_stored_roster_blocks_outcome(self):
        self.fixture();self.snapshot();self.archive()
        self.change("UPDATE historical_results SET raw_json='{}'")
        d=self.one()
        self.assertEqual(d['state'],'BUILD_QUALIFIED')
        self.assertIn('unreadable_stored_result_rosters',d['blockers'])

    def test_readable_stored_roster_still_requires_salary_player_role_coverage(self):
        self.fixture();self.snapshot();self.archive()
        self.change("UPDATE historical_results SET raw_json=replace(raw_json,'CPT Alpha','CPT Alpha (100)')")
        d=self.one()
        self.assertEqual(d['state'],'CANDIDATE')
        self.assertIn('stored_result_player_role_conflict',d['conflicts'])

    def test_duplicate_reconciliation_preserves_rows_and_timestamps(self):
        self.fixture();self.one(True)
        before=self.query('SELECT * FROM historical_contest_identities')
        self.one(True)
        self.assertEqual(self.query('SELECT * FROM historical_contest_identities'),before)

    def test_cancellation_during_second_insert_rolls_back_every_identity(self):
        self.fixture();self.one(True)
        before=self.query('SELECT * FROM historical_contest_identities')
        self.snapshot()
        original=hi.derive_contests
        cancel=[False]
        def proposal(*args,**kwargs):
            rows=original(*args,**kwargs)
            class CancelOnData:
                evidence=rows[0].evidence
                @property
                def data(self):
                    cancel[0]=True
                    return rows[0].data
            return (CancelOnData(),rows[0])
        with patch.object(hi,'derive_contests',side_effect=proposal):
            with self.assertRaises(ai.ImportCancelled):hi.reconcile(self.db,cancelled=lambda:cancel[0])
        self.assertEqual(self.query('SELECT * FROM historical_contest_identities'),before)

    def test_cancel_before_work_does_not_write(self):
        self.fixture();before=self.db.read_bytes()
        with self.assertRaises(ai.ImportCancelled):hi.reconcile(self.db,cancelled=lambda:True)
        self.assertEqual(before,self.db.read_bytes())

    def test_rebuild_leaves_original_sources_and_observations_unchanged(self):
        self.fixture();self.snapshot();self.archive();self.one(True)
        files={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file() and p!=self.db}
        before=self.query('SELECT * FROM historical_results')
        first=self.one(True)
        self.change('DELETE FROM historical_contest_identities')
        self.assertEqual(first,self.one(True))
        self.assertEqual(before,self.query('SELECT * FROM historical_results'))
        self.assertEqual(files,{p:p.read_bytes() for p in files})

    def test_v123_migration_additive(self):
        self.fixture();self.change('DROP TABLE historical_contest_identities')
        before=self.query('SELECT * FROM historical_results')
        with closing(sqlite3.connect(self.db)) as conn:init_historical_import_tables(conn)
        self.assertEqual(before,self.query('SELECT * FROM historical_results'))
        self.assertEqual(self.query('SELECT * FROM historical_contest_identities'),[])

    def test_legacy_name_match_cannot_qualify_identity(self):
        self.fixture();self.change('DELETE FROM analysis_sources');self.change('DELETE FROM analysis_salary_pairs')
        self.change("UPDATE historical_results SET matched_lineup_id='legacy',match_method='player_names'")
        self.assertEqual(self.one()['state'],'UNRESOLVED')

    def test_review_counts_reconcile_and_omit_private_identifiers(self):
        from review_report import capture
        self.fixture();self.snapshot();self.archive()
        report=capture(db_path=self.db,diagnostic_path=self.root/'absent.json')
        aggregate=report.data['database']['historical_identity']
        self.assertEqual(sum(aggregate['states'].values()),aggregate['total_historical_contests'])
        self.assertEqual(aggregate['states']['OUTCOME_QUALIFIED'],1)
        encoded=json.dumps(aggregate)
        for private in ('Synthetic_User',str(self.root),self.source['hash'],self.source['import_id'],'123'):
            self.assertNotIn(private,encoded)
        self.assertIn('Historical evidence identity',report.summary())

    def test_read_model_is_detached_and_read_only(self):
        self.fixture();self.snapshot()
        before=self.db.read_bytes();model=hi.qualified_contests(self.db)[0]
        data=model.data;data['snapshot_evidence']['players'][0]['FlexProjection']=9999
        self.assertEqual(model.data['snapshot_evidence']['players'][0]['FlexProjection'],8)
        self.assertEqual(before,self.db.read_bytes())

    def test_objective_only_when_recorded_recipe_precedence(self):
        self.fixture()
        self.snap['inputs']['recipe'].pop('contest_objective')
        self.snap['input_id']=hi._digest(self.snap['inputs']);self.snapshot()
        self.assertIsNone(self.one()['identity']['contest_objective'])
        shutil.rmtree(self.root/'snapshots')
        self.snap['inputs']['recipe']['contest_objective']='DOUBLE_UP'
        self.snap['inputs']['contest']['objective']='TOURNAMENT'
        self.snap['input_id']=hi._digest(self.snap['inputs']);self.snapshot()
        self.assertEqual(self.one()['identity']['contest_objective'],'DOUBLE_UP')

    def test_pool_or_captain_id_mismatch_rejected(self):
        self.fixture();self.snap['inputs']['players'][0]['CptID']='9999'
        self.snap['input_id']=hi._digest(self.snap['inputs']);self.snapshot()
        self.assertEqual(self.one()['state'],'SALARY_QUALIFIED')

    def test_scan_limit_does_not_choose_partial_snapshot_candidate(self):
        self.fixture();self.snapshot()
        with patch('review_build_evidence.MAX_FILES',0):
            self.assertIn('snapshot_evidence_scan_incomplete',self.one()['blockers'])

    def test_outcome_coverage_independent_of_build_identity(self):
        self.fixture();self.snapshot()
        d=self.one()
        self.assertTrue(d['actual_score_evidence']['complete'])
        self.assertEqual(d['state'],'SNAPSHOT_QUALIFIED')

    def test_duplicate_conflicting_scores_not_overwritten_even_after_blank_rows(self):
        self.fixture();self.snapshot();self.archive()
        self.replace_result(lambda rows: rows.append(['','','','','','','','Alpha','FLEX',99]))
        d=self.one()
        self.assertEqual(d['state'],'BUILD_QUALIFIED')
        self.assertIn('alpha',d['actual_score_evidence']['conflicting_scores'])

    def test_captain_base_score_conflict(self):
        self.fixture();self.snapshot();self.archive()
        self.replace_result(lambda rows: rows.append(['','','','','','','','Alpha','CPT',99]))
        self.assertIn('@cpt:alpha',self.one()['actual_score_evidence']['conflicting_scores'])

    def test_lineup_total_conflict_blocks_outcome(self):
        self.fixture();self.snapshot();self.archive()
        self.replace_result(lambda rows: rows[1].__setitem__(3,999))
        self.assertIn('roster_actual_total_conflict',self.one()['blockers'])

    def test_archive_embedded_snapshot_is_valid_source(self):
        self.fixture();self.archive()
        self.assertEqual(self.one()['state'],'OUTCOME_QUALIFIED')

    def test_cancelled_and_postgame_archives_do_not_qualify_build(self):
        self.fixture();self.snapshot();self.archive(build_status='cancelled')
        self.archive('late.zip',created_at='2026-09-22T12:00:00Z')
        self.assertEqual(self.one()['state'],'SNAPSHOT_QUALIFIED')

    def test_older_same_slate_build_candidates_not_lost_or_called_submitted(self):
        self.fixture();self.snapshot()
        older=copy.deepcopy(self.snap)
        older['created_at']='2026-09-21T17:00:00-04:00'
        older['inputs']['players'][0]['FlexProjection']=7
        older['input_id']=hi._digest(older['inputs'])
        self.archive(snap=older)
        d=self.one()
        self.assertEqual(d['state'],'SNAPSHOT_QUALIFIED')
        self.assertEqual(d['snapshot_evidence']['input_id'],self.snap['input_id'])
        self.assertEqual(d['build_candidates'][0]['input_id'],older['input_id'])
        self.assertEqual(d['build_candidates'][0]['submission'],'not_established')

    def test_explicit_empty_contest_registry_does_not_fall_back(self):
        self.fixture();self.snapshot()
        (self.root/'contest-snapshots.json').write_text(json.dumps({'123':[]}))
        self.assertEqual(self.one()['state'],'SALARY_QUALIFIED')

    def test_removing_snapshot_downgrades_cached_state(self):
        self.fixture();path=self.snapshot();self.one(True)
        path.unlink()
        self.assertEqual(self.one(True)['state'],'SALARY_QUALIFIED')

    def test_missing_date_without_confirmation_remains_candidate(self):
        self.fixture(dated=False);self.change('DELETE FROM analysis_salary_pairs')
        d=self.one()
        self.assertEqual(d['state'],'CANDIDATE')
        self.assertIn('salary_date_confirmation_required',d['blockers'])

    def test_cancellation_during_evidence_read_retains_previous_state(self):
        self.fixture();self.snapshot();self.one(True)
        before=self.query('SELECT * FROM historical_contest_identities')
        calls=[0]
        def cancel():
            calls[0]+=1
            return calls[0]>=15
        with self.assertRaises(ai.ImportCancelled):hi.reconcile(self.db,cancelled=cancel)
        self.assertEqual(before,self.query('SELECT * FROM historical_contest_identities'))

    def test_source_change_during_read_invalidates_proposed_qualification(self):
        self.fixture();self.snapshot()
        read=hi._outcomes
        def change_after_read(*args):
            value=read(*args)
            Path(self.salary['snapshot']).write_text('changed after validation')
            return value
        with patch.object(hi,'_outcomes',side_effect=change_after_read):d=self.one(True)
        self.assertEqual(d['state'],'CANDIDATE')
        self.assertFalse(d['salary_evidence']['qualified'])
        self.assertIn('source_revision_changed_during_reconciliation',d['conflicts'])

    def test_conflicting_scores_across_same_qualified_salary_revision(self):
        result,_=self.fixture();self.snapshot();self.archive()
        with result.open(newline='',encoding='utf-8-sig') as f:rows=list(csv.reader(f))
        for row in rows[1:]:row[5]='456';row[3]=str(float(row[3])+1.5)
        rows[1][-1]='11'
        with (self.results/'second.csv').open('w',newline='',encoding='utf-8-sig') as f:csv.writer(f).writerows(rows)
        ai.import_folders(self.results,self.salaries,db_path=str(self.db),username='Synthetic_User')
        contests=hi.reconcile(self.db)
        self.assertEqual(len(contests),2)
        for contest in contests:
            d=contest.data
            self.assertEqual(d['state'],'BUILD_QUALIFIED')
            self.assertIn('cross_contest_actual_score_conflict',d['conflicts'])
            self.assertFalse(d['actual_score_evidence']['complete'])


if __name__=='__main__':unittest.main()
