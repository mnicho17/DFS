from test_environment import install
install()
import copy
from contextlib import closing
import json
import sqlite3
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from PyQt5 import QtCore,QtWidgets
import analysis_imports as ai
import test_field_history_validation as fixtures
from test_hindsight_evidence import SourceFixture
from test_hindsight_solver import athletes
from test_opponent_history import results as standings
from opponent_history import sync_saved
from field_history_validation import _pool,evaluate_history,render_evaluation
from pregame_field_evidence import PregameEvidence
from historical_identity import _digest
from review_build_evidence import digest
from build_snapshots import create_snapshot,fingerprint,save_snapshot
from showdown_simulation import simulate_showdown,generate_showdown_field
from pregame_sim_comparison import freeze_bank,compare_sim
from test_portfolio_risk_evidence import logical_db,source_bytes


class PregameFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def history(self):
        athletes_pool=athletes()
        for player in athletes_pool:
            player['roles']['FLEX']['salary']=7000;player['roles']['CPT']['salary']=10500
        f=SourceFixture(pool=copy.deepcopy(athletes_pool),edit_results=standings('first'));self.addCleanup(f.close)
        for name,day in (('second','22'),('later','24')):
            other=SourceFixture(pool=copy.deepcopy(athletes_pool),edit_results=standings(name));self.addCleanup(other.close)
            other.raw_salary.write_text(other.raw_salary.read_text(encoding='utf-8-sig').replace('09/21/2026',f'09/{day}/2026'),encoding='utf-8-sig')
            other.raw_result.write_text(other.raw_result.read_text(encoding='utf-8-sig').replace('2026-09-21',f'2026-09-{day}'),encoding='utf-8-sig')
            self.assertFalse(ai.import_folders(other.results,other.salaries,db_path=f.db)['errors'])
        self.assertFalse(sync_saved(f.db)['errors'])
        with closing(sqlite3.connect(f.db)) as conn:
            sources=ai._sources(conn)
            result=next(s for s in sources if s['kind']=='results' and s['manifest']['dates']==['2026-09-24'])
            sha=conn.execute('SELECT salary_hash FROM analysis_salary_pairs WHERE result_hash=?',(result['hash'],)).fetchone()[0]
        source=next(s for s in sources if s['hash']==sha)
        manifest=ai._salary_manifest(source['snapshot'],lambda:False)
        pool=_pool(manifest)
        for p in pool:
            p.update(GameInfo='NE@SEA 09/24/2026 08:15PM ET',FlexProjection=8.0,CptProjection=12.0,
                ProjectionSource='Synthetic frozen forecast',OwnershipUnits='percent_of_entries',
                ProjCptOwnPct=100/len(pool),ProjFlexOwnPct=500/len(pool),NFLQBEligible=True,NFLActive=True)
        snap=create_snapshot(pool,dict(sport='NFL',contest_kind='showdown',salary_cap=50000),{})
        snap['created_at']='2026-09-24T18:00:00-04:00'
        path=f.root/'snapshots'/'qualified.json';path.parent.mkdir();save_snapshot(str(path),snap)
        return f,result,manifest,sha,snap,path

    def write(self,path,snap):
        snap['input_id']=fingerprint(snap['inputs']);save_snapshot(str(path),snap)

    def qualify(self,f,result,manifest,sha):
        evidence=PregameEvidence(f.db)
        with closing(sqlite3.connect(f.db)) as conn:
            pool,receipt=evidence.qualify(result,manifest,sha,conn)
        evidence.revalidate()
        return pool,receipt

    def test_qualified_pregame_inputs_and_shared_sim_preserve_sources(self):
        f,r,m,s,snap,p=self.history();before=logical_db(f.db);files=source_bytes(f.root)
        pool,receipt=self.qualify(f,r,m,s)
        self.assertEqual(receipt['input_id'],snap['input_id'])
        report=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,),draw_mode='recorded',compare_sim=True,scenarios=30,candidate_count=20)
        self.assertEqual(len(report['games']),1)
        sim=report['games'][0]['models']['current'][0]['sim_comparison']
        self.assertEqual(sim['status'],'complete');self.assertTrue(sim['outcome_moments_identical'])
        self.assertEqual(sim['scenarios'],30)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(files,source_bytes(f.root))
        self.assertIn('scoring moments identical',render_evaluation(report))

    def test_postkickoff_naive_and_conflicting_original_kickoff_are_rejected(self):
        f,r,m,s,snap,p=self.history()
        for timestamp in ('2026-09-24T20:15:00-04:00','2026-09-24T20:16:00-04:00','2026-09-24T18:00:00'):
            snap['created_at']=timestamp;self.write(p,snap)
            with self.assertRaises(ValueError):self.qualify(f,r,m,s)
        snap['created_at']='2026-09-24T18:00:00-04:00'
        for player in snap['inputs']['players']:player['GameInfo']='NE@SEA 09/24/2026 09:15PM ET'
        self.write(p,snap)
        with self.assertRaisesRegex(ValueError,'kickoff'):self.qualify(f,r,m,s)

    def test_identity_salary_and_full_pool_mismatch_are_rejected(self):
        f,r,m,s,snap,p=self.history()
        for key in ('CptID','FlexID','CptSalary','Team'):
            changed=copy.deepcopy(snap);changed['inputs']['players'][0][key]='wrong' if key!='CptSalary' else 1
            self.write(p,changed)
            with self.assertRaises(ValueError):self.qualify(f,r,m,s)
        changed=copy.deepcopy(snap);changed['inputs']['players'].pop();self.write(p,changed)
        with self.assertRaises(ValueError):self.qualify(f,r,m,s)

    def test_missing_eligibility_ownership_projection_and_bad_totals_are_not_inferred(self):
        f,r,m,s,snap,p=self.history()
        for key in ('NFLQBEligible','OwnershipUnits','ProjCptOwnPct','FlexProjection','CptProjection'):
            changed=copy.deepcopy(snap)
            for player in changed['inputs']['players']:player.pop(key,None)
            self.write(p,changed)
            with self.assertRaises(ValueError):self.qualify(f,r,m,s)
        changed=copy.deepcopy(snap);changed['inputs']['players'][0]['ProjFlexOwnPct']=0;self.write(p,changed)
        with self.assertRaisesRegex(ValueError,'totals'):self.qualify(f,r,m,s)

    def test_same_time_conflicts_and_timestamp_revisions_require_exact_choice(self):
        f,r,m,s,snap,p=self.history()
        other=copy.deepcopy(snap);other['inputs']['players'][0]['FlexProjection']=10
        q=p.with_name('conflicting.json');self.write(q,other)
        with self.assertRaisesRegex(ValueError,'Conflicting latest'):self.qualify(f,r,m,s)
        q.unlink();other=copy.deepcopy(snap);other['created_at']='2026-09-24T17:00:00-04:00';self.write(q,other)
        with self.assertRaisesRegex(ValueError,'timestamp revisions'):self.qualify(f,r,m,s)

    def test_identical_snapshot_copies_are_legitimate(self):
        f,r,m,s,snap,p=self.history();p.with_name('identical.json').write_bytes(p.read_bytes())
        pool,receipt=self.qualify(f,r,m,s)
        self.assertEqual(receipt['input_id'],snap['input_id'])

    def test_saved_exact_resolution_is_preserved_even_with_newer_snapshot(self):
        f,r,m,s,snap,p=self.history()
        newer=copy.deepcopy(snap);newer['inputs']['players'][0]['FlexProjection']=10;newer['created_at']='2026-09-24T19:00:00-04:00';self.write(p.with_name('newer.json'),newer)
        ident=_digest(['historical-contest',r['import_id'],'import'])
        f.change('INSERT INTO historical_evidence_resolutions(identity_id,snapshot_digest,input_id,salary_hash,method,evidence_version) VALUES(?,?,?,?,?,?)',
            (ident,digest(snap),snap['input_id'],s,'user_confirmed',1))
        pool,receipt=self.qualify(f,r,m,s)
        self.assertEqual(receipt['input_id'],snap['input_id'])
        self.assertEqual(receipt['association'],'saved exact snapshot resolution')
        f.change('UPDATE historical_evidence_resolutions SET salary_hash=?',('wrong',))
        with self.assertRaisesRegex(ValueError,'saved choice is preserved'):self.qualify(f,r,m,s)

    def test_contest_registry_selection_is_preserved(self):
        f,r,m,s,snap,p=self.history()
        newer=copy.deepcopy(snap);newer['inputs']['players'][0]['FlexProjection']=10;newer['created_at']='2026-09-24T19:00:00-04:00';self.write(p.with_name('newer.json'),newer)
        (f.root/'contest-snapshots.json').write_text(json.dumps({str(r['manifest']['contest_ids'][0]):[snap['input_id']]}),encoding='utf-8')
        pool,receipt=self.qualify(f,r,m,s)
        self.assertEqual(receipt['input_id'],snap['input_id'])
        self.assertEqual(receipt['association'],'contest-ID association')

    def test_changed_snapshot_or_new_inventory_entry_prevents_publication(self):
        f,r,m,s,snap,p=self.history();audit=PregameEvidence(f.db)
        changed=copy.deepcopy(snap);changed['inputs']['players'][0]['FlexProjection']=10;self.write(p,changed)
        with self.assertRaisesRegex(ValueError,'changed'):audit.revalidate()
        audit=PregameEvidence(f.db);p.with_name('added.json').write_bytes(p.read_bytes())
        with self.assertRaisesRegex(ValueError,'inventory'):audit.revalidate()

    def test_unreadable_inventory_and_cancellation_do_not_select_partial_candidates(self):
        f,r,m,s,snap,p=self.history();p.with_name('bad.json').write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'invalid files'):PregameEvidence(f.db)
        with self.assertRaises(ai.ImportCancelled):PregameEvidence(f.db,cancelled=lambda:True)

    def test_explicit_field_path_matches_default_sim_and_rejects_forged_fields(self):
        f,r,m,s,snap,p=self.history();pool,_=self.qualify(f,r,m,s)
        bank=freeze_bank(pool,20,lambda:False)
        field=generate_showdown_field(pool,50,seed=18)
        kwargs=dict(scenarios=30,field_lineup_count=50,seed=17)
        a=simulate_showdown(bank,pool,**kwargs)
        b=simulate_showdown(bank,pool,diagnostic_field=field,**kwargs)
        self.assertEqual([lu.sim_metrics for lu in a['lineups']],[lu.sim_metrics for lu in b['lineups']])
        forged=copy.deepcopy(field);forged[0]['Captain']['CptSalary']=1
        with self.assertRaisesRegex(ValueError,'identity or salary'):simulate_showdown(bank,pool,diagnostic_field=forged,**kwargs)
        with self.assertRaisesRegex(ValueError,'every requested'):simulate_showdown(bank,pool,diagnostic_field=field[:10],**kwargs)
        with self.assertRaisesRegex(ValueError,'production scenario cache'):simulate_showdown(bank,pool,diagnostic_field=field,scenario_cache=True,**kwargs)

    def test_recorded_inputs_unavailable_games_are_explicit_and_not_scored(self):
        f,r,m,s,snap,p=self.history();snap['created_at']='2026-09-24T21:00:00-04:00';self.write(p,snap)
        report=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,),draw_mode='recorded')
        self.assertEqual(report['games'],[]);self.assertEqual(report['scores'],{})
        self.assertIn('Pregame-qualified later games: 0/1',render_evaluation(report))
        self.assertTrue(any('Pregame input qualification' in e['reason'] for e in report['excluded']))

    def test_worker_recorded_comparison_leaves_database_and_profile_unchanged(self):
        from opponent_history_ui import HistoryWorker
        f,r,m,s,snap,p=self.history();before=logical_db(f.db);results=[];errors=[]
        worker=HistoryWorker(f.db,'recorded','2026-09-24',config={'snapshot_root':str(f.root)})
        worker.result.connect(results.append);worker.error.connect(errors.append)
        worker.run()
        self.assertFalse(errors);self.assertEqual(len(results),1)
        self.assertEqual(results[0]['draw_mode'],'recorded')
        self.assertEqual(before,logical_db(f.db))

    def test_calibrated_third_model_uses_shared_sim_and_preserves_evidence(self):
        f,r,m,s,snap,p=self.history();before=logical_db(f.db);files=source_bytes(f.root)
        report=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,),draw_mode='recorded',
            compare_sim=True,ownership_calibration=True,scenarios=30,candidate_count=20)
        self.assertEqual(set(report['scores']),{'current','historical','calibrated'})
        sim=report['games'][0]['models']['current'][0]['sim_comparison']
        self.assertEqual(set(sim['models']),{'current','historical','calibrated'})
        self.assertTrue(sim['outcome_moments_identical'])
        self.assertIsNotNone(sim['calibrated_overlap_pct'])
        self.assertIn('Calibrated seed 17: ownership MAE',render_evaluation(report))
        self.assertEqual(before,logical_db(f.db));self.assertEqual(files,source_bytes(f.root))
        f.change('UPDATE opponent_user_contest_stats SET best_rank=999,top_one_pct_entries=0')
        self.assertEqual(report,evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,),draw_mode='recorded',
            compare_sim=True,ownership_calibration=True,scenarios=30,candidate_count=20))

    def test_calibration_requires_recorded_inputs_and_is_explicit_in_dialog(self):
        with self.assertRaisesRegex(ValueError,'qualified recorded'):
            evaluate_history('unused','2026-09-24',ownership_calibration=True)
        from opponent_history_ui import OpponentHistoryDialog
        f,r,m,s,snap,p=self.history();d=OpponentHistoryDialog(f.db);self.addCleanup(d.deleteLater)
        self.assertTrue(d.calibrate_ownership.isChecked())
        d.calibrate_ownership.setChecked(False)
        self.assertIsNone(d.profile);self.assertFalse(d.save_button.isEnabled())
