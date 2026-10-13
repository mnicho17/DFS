from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch

from PyQt5 import QtWidgets
import analysis_imports as ai
from build_snapshots import fingerprint
from historical_field import distributions
from opponent_history import sync_saved
from showdown_field import sample_field as baseline_sample
from showdown_history_field import (MODEL, HISTORY_WEIGHT, prepare_profile, validate_profile,
    revalidate_profile, sample_field, slate_cutoff)
from showdown_screening import settings
from showdown_simulation import generate_showdown_field, showdown_signature, simulate_showdown
from field_diagnostics import summarize_field, format_field
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db, source_bytes
from test_showdown_performance import _showdown_players


def profile(priors):
    p = dict(model=MODEL, version=2, cutoff='2026-09-24', preset='150-Max', history_weight=HISTORY_WEIGHT,
        training=[dict(date=f'2026-09-{d}',game='A@B',known=400) for d in (21,22,23)],
        priors=priors, coverage_basis='observed entries', validation_status='experimental')
    return dict(payload=p, profile_id=fingerprint(p))


class HistoryProfileTests(unittest.TestCase):
    def history(self, duplicate=False):
        def edit(name):
            def change(rows):
                results(name)(rows)
                entries = [list(r) for r in rows[1:101]]
                for n in range(100, 400):
                    r = list(entries[n % 100]); r[rows[0].index('EntryId')] = str(1000+n)
                    r[rows[0].index('Rank')] = str(n+1); entries.append(r)
                rows[1:101] = entries
            return change
        first = SourceFixture(edit_results=edit('first')); self.addCleanup(first.close)
        for name, day in [('second',22),('third',23),('future',24)] + ([('samegame',23)] if duplicate else []):
            other=SourceFixture(edit_results=edit(name)); self.addCleanup(other.close)
            other.raw_salary.write_text(other.raw_salary.read_text(encoding='utf-8-sig').replace('09/21/2026',f'09/{day}/2026'),encoding='utf-8-sig')
            other.raw_result.write_text(other.raw_result.read_text(encoding='utf-8-sig').replace('2026-09-21',f'2026-09-{day}'),encoding='utf-8-sig')
            imported=ai.import_folders(other.results,other.salaries,db_path=first.db)
            self.assertFalse(imported['errors'])
        self.assertFalse(sync_saved(first.db)['errors'])
        first.change("UPDATE opponent_contests SET name=name || ' 150-Max'")
        return first

    def test_prior_games_equal_weight_duplicate_game_and_readonly(self):
        f=self.history(True); before=logical_db(f.db); sources=source_bytes(f.root)
        a=prepare_profile(f.db,'2026-09-24','150-Max')
        self.assertEqual(len(a['payload']['training']),3)
        self.assertEqual(sum(r['known'] for r in a['payload']['training']),1200)
        self.assertTrue(all(r['date']<'2026-09-24' for r in a['payload']['training']))
        self.assertEqual(a,prepare_profile(f.db,'2026-09-24','150-Max'))
        self.assertEqual(before,logical_db(f.db)); self.assertEqual(sources,source_bytes(f.root))
        f.change('UPDATE opponent_user_contest_stats SET best_rank=999,top_one_pct_entries=0')
        self.assertEqual(a,prepare_profile(f.db,'2026-09-24','150-Max'))

    def test_preset_insufficient_coverage_conflicts_and_cancel_fail_closed(self):
        f=self.history()
        with self.assertRaisesRegex(ValueError,'three prior games'):prepare_profile(f.db,'2026-09-24','20-Max')
        with self.assertRaisesRegex(ValueError,'three prior games'):prepare_profile(f.db,'2026-09-23','150-Max')
        f.change("UPDATE opponent_contests SET audit_json=json_set(audit_json,'$.conflicting_entry_ids_excluded',1) WHERE end_date='2026-09-22'")
        with self.assertRaisesRegex(ValueError,'three prior games'):prepare_profile(f.db,'2026-09-24','150-Max')
        with self.assertRaises(ai.ImportCancelled):prepare_profile(f.db,'2026-09-24','150-Max',cancelled=lambda:True)

    def test_changed_mapping_or_source_cannot_reuse_profile(self):
        f=self.history(); p=prepare_profile(f.db,'2026-09-24','150-Max')
        players=[dict(GameInfo='A@B 09/24/2026 08:15PM ET')]
        revalidate_profile(p,f.db,players,'150-Max')
        f.change('DELETE FROM analysis_salary_pairs WHERE result_hash=?',(p['payload']['training'][0]['result_hash'],))
        with self.assertRaises(ValueError):revalidate_profile(p,f.db,players,'150-Max')

    def test_future_game_changes_do_not_change_training(self):
        f=self.history(); p=prepare_profile(f.db,'2026-09-24','150-Max')
        f.change("UPDATE opponent_user_contest_stats SET stats_json='{}' WHERE contest_key IN (SELECT contest_key FROM opponent_contests WHERE end_date='2026-09-24')")
        self.assertEqual(p,prepare_profile(f.db,'2026-09-24','150-Max'))


class HistoryFieldIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def pool(self):
        pool=_showdown_players()
        for p in pool:
            p.update(GameInfo='ARI@CAR 09/24/2026 08:15PM ET', OwnershipUnits='percent_of_entries',
                ProjCptOwnPct=100/len(pool), ProjFlexOwnPct=500/len(pool))
        return pool

    def test_explicit_sampler_determinism_legality_and_no_input_mutation(self):
        pool=self.pool(); before=copy.deepcopy(pool)
        p=profile(distributions(baseline_sample(pool,100,seed=18))); frozen=copy.deepcopy(p)
        a=generate_showdown_field(pool,100,seed=18,model='historical-blend-v1',history_profile=p)
        b=generate_showdown_field(pool,100,seed=18,model='historical-blend-v1',history_profile=p)
        self.assertEqual(len(a),100)
        self.assertEqual([showdown_signature(lu) for lu in a],[showdown_signature(lu) for lu in b])
        self.assertEqual(pool,before);self.assertEqual(p,frozen)
        from showdown_simulation import validate_showdown_lineup
        for row in a:validate_showdown_lineup(row,pool,50000)
        self.assertIn('Historical field use:', '\n'.join(format_field(summarize_field(a,pool,showdown=True))))

    def test_missing_ownership_fallback_and_unenabled_report_are_explicit(self):
        pool=_showdown_players(); baseline=baseline_sample(pool,80,seed=4)
        p=profile(distributions(baseline)); actual=sample_field(pool,80,p,seed=4)
        self.assertEqual([showdown_signature(lu) for lu in baseline],[showdown_signature(lu) for lu in actual])
        self.assertEqual(actual.diagnostic['history_use']['status'],'baseline fallback')
        self.assertIn('verified',actual.diagnostic['history_use']['reason'])
        self.assertIn('not enabled','\n'.join(format_field(summarize_field(baseline,pool,showdown=True))))
        different_cap=sample_field(pool,80,p,salary_cap=60000)
        self.assertEqual(different_cap.diagnostic['history_use']['status'],'baseline fallback')
        self.assertIn('$50,000',different_cap.diagnostic['history_use']['reason'])

    def test_guarded_application_and_incomplete_trial_fallback(self):
        pool=self.pool(); baseline=baseline_sample(pool,80,seed=4); p=profile(distributions(baseline))
        with patch('historical_ownership.sample_calibrated_history_field',return_value=copy.deepcopy(baseline)):
            applied=sample_field(pool,80,p,seed=4)
        self.assertEqual(applied.diagnostic['history_use']['status'],'applied')
        with patch('historical_ownership.sample_calibrated_history_field',return_value=[]):
            fallback=sample_field(pool,80,p,seed=4)
        self.assertEqual(fallback.diagnostic['history_use']['status'],'baseline fallback')
        self.assertEqual(len(fallback),80)
        self.assertEqual(sample_field(pool,80,p,cancelled=lambda:True),[])

    def test_profile_tampering_current_game_unknown_kickoff_and_preset_rejected(self):
        pool=self.pool();p=profile(distributions(baseline_sample(pool,50)))
        changed=copy.deepcopy(p);changed['payload']['cutoff']='2026-09-25'
        with self.assertRaises(ValueError):validate_profile(changed)
        changed['profile_id']=fingerprint(changed['payload'])
        with self.assertRaisesRegex(ValueError,'after this slate'):validate_profile(changed,pool)
        changed=copy.deepcopy(p);changed['payload']['training'][0]['date']='2026-09-24';changed['profile_id']=fingerprint(changed['payload'])
        with self.assertRaisesRegex(ValueError,'current or later'):validate_profile(changed)
        with self.assertRaisesRegex(ValueError,'entry-limit'):validate_profile(p,pool,'20-Max')
        with self.assertRaises(ValueError):slate_cutoff(_showdown_players())

    def test_screening_identity_and_snapshot_preserve_exact_profile(self):
        from build_snapshots import create_snapshot,validate_snapshot
        pool=self.pool();p=profile(distributions(baseline_sample(pool,50)))
        a=settings({},1000);b=settings({},1000,p)
        self.assertNotEqual(fingerprint(a),fingerprint(b))
        snap=create_snapshot(pool,dict(sport='NFL',contest_kind='showdown',showdown_history_field=True),{},dict(showdown_field_profile=p))
        self.assertEqual(validate_snapshot(snap)['inputs']['calibration']['showdown_field_profile'],p)

    def test_field_changes_do_not_change_shared_candidate_mean_outcomes(self):
        from optimizers import ShowdownOptimizer
        pool=self.pool();p=profile(distributions(baseline_sample(pool,50)))
        rows=ShowdownOptimizer(pool).build_lineups(2)
        a=simulate_showdown(copy.deepcopy(rows),pool,scenarios=20,field_lineup_count=50,seed=8)
        b=simulate_showdown(copy.deepcopy(rows),pool,scenarios=20,field_lineup_count=50,seed=8,field_model='historical-blend-v1',history_profile=p)
        self.assertEqual([r.sim_metrics['sim_mean'] for r in a['lineups']],[r.sim_metrics['sim_mean'] for r in b['lineups']])

    def test_ui_snapshot_replay_preserves_profile_and_can_disable_it(self):
        from main_window import MainWindow
        window=MainWindow();self.addCleanup(window.close)
        window.combo_sport.setCurrentText('NFL');window.tabs_lineups.setCurrentIndex(0)
        window.chk_auto_entry_target.setChecked(False)
        window.combo_field_preset.setCurrentText('150-Max')
        window.players=self.pool()
        self.assertNotIn('showdown_history_field',window._current_build_recipe())
        self.assertIs(window.btn_showdown_history.parentWidget(),window.combo_compute_profile.parentWidget())
        self.assertGreaterEqual(window.btn_showdown_history.parentWidget().layout().indexOf(window.btn_showdown_history),0)
        window.showdown_field_profile=profile(distributions(baseline_sample(window.players,50)))
        window.chk_showdown_history.setChecked(True)
        snap=window._capture_snapshot(contest={})
        window._restore_snapshot(snap)
        self.assertTrue(window.chk_showdown_history.isChecked())
        self.assertEqual(window._capture_snapshot()['inputs'],snap['inputs'])
        window.chk_showdown_history.setChecked(False)
        self.assertNotIn('showdown_field_profile',window._snapshot_calibration())

    def test_worker_rejects_history_on_fast_and_requires_evidence_checks(self):
        from main_window import LineupBuildWorker
        pool=self.pool();p=profile(distributions(baseline_sample(pool,50)))
        worker=LineupBuildWorker(pool,kind='showdown',sport='NFL',sim_enabled=True,num_lineups=2,salary_cap=50000,
            field_calibration=dict(showdown_field_profile=p),compute_mode='Fast')
        errors=[];worker.error.connect(errors.append);worker.run()
        self.assertTrue(errors);self.assertIn('Deep',str(errors[0]))
        worker=LineupBuildWorker(pool,kind='showdown',sport='NFL',sim_enabled=True,num_lineups=2,salary_cap=50000,
            field_calibration=dict(showdown_field_profile=p),compute_mode='Deep')
        errors=[];worker.error.connect(errors.append)
        with patch('showdown_history_field.revalidate_profile',side_effect=ValueError('Source receipt changed')):
            worker.run()
        self.assertTrue(errors);self.assertIn('Source receipt changed',str(errors[0]))
        cancelled=LineupBuildWorker(pool,kind='showdown',sport='NFL',sim_enabled=True,num_lineups=2,salary_cap=50000,
            field_calibration=dict(showdown_field_profile=p),compute_mode='Deep')
        finished=[];errors=[];cancelled.finished.connect(finished.append);cancelled.error.connect(errors.append)
        def stop(*args,**kwargs):
            cancelled.request_cancel()
            raise ai.ImportCancelled('Cancelled')
        with patch('showdown_history_field.revalidate_profile',side_effect=stop):cancelled.run()
        self.assertFalse(errors);self.assertTrue(finished[0]['cancelled'])
        self.assertFalse(finished[0]['lineups'])

    def test_recorded_holdout_compares_actual_production_blend(self):
        from test_pregame_field_comparison import PregameFieldTests
        from field_history_validation import evaluate_history
        fixture=PregameFieldTests();self.addCleanup(fixture.doCleanups)
        f,_,_,_,snap,_=fixture.history()
        pool=snap['inputs']['players'];p=profile(distributions(baseline_sample(pool,50)))
        with patch('showdown_history_field.prepare_profile',return_value=p), \
             patch('showdown_history_field.revalidate_profile') as verify, \
             patch('learning_db._infer_field_preset',return_value='150-Max'):
            r=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,),draw_mode='recorded',compare_sim=True,
                scenarios=30,candidate_count=20,production_blend=True)
        self.assertTrue(verify.called)
        self.assertEqual(len(r['games']),1)
        sim=r['games'][0]['models']['current'][0]['sim_comparison']
        self.assertIn('production_blend',sim['models'])
        self.assertTrue(sim['outcome_moments_identical'])
        self.assertEqual(r['games'][0]['production_profile'],p)
        with self.assertRaisesRegex(ValueError,'recorded'):
            evaluate_history('unused','2026-09-24',production_blend=True)


if __name__=='__main__':unittest.main()
