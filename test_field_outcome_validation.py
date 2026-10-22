from test_environment import install
install()
import copy
import csv
from decimal import Decimal
from pathlib import Path
import unittest
import analysis_imports as ai
import test_pregame_field_comparison as fixtures
from field_outcome_validation import capture_outcomes,ownership_accuracy,evaluate_candidates
from field_history_validation import evaluate_history,render_evaluation
from pregame_sim_comparison import freeze_bank,compare_sim
from showdown_field import sample_field
from test_portfolio_risk_evidence import logical_db,source_bytes


class FieldOutcomeTests(unittest.TestCase):
    def history(self):return fixtures.PregameFieldTests.history(self)
    def qualify(self,f,r,m,s):return fixtures.PregameFieldTests.qualify(self,f,r,m,s)
    def rewrite(self,source,edit):
        source=copy.deepcopy(source);path=Path(source['snapshot'])
        with path.open(encoding='utf-8-sig',newline='') as h:rows=list(csv.reader(h))
        edit(rows)
        with path.open('w',encoding='utf-8-sig',newline='') as h:csv.writer(h).writerows(rows)
        source['hash']=ai._hash(path)
        return source

    def test_exact_scores_observed_subset_ownership_and_readonly(self):
        f,r,m,s,_,_=self.history();pool,_=self.qualify(f,r,m,s)
        before=logical_db(f.db);files=source_bytes(f.root)
        evidence=capture_outcomes(r,m,[(r,m)])
        self.assertEqual(evidence['accepted'],100);self.assertEqual(evidence['valid_rosters'],100)
        self.assertTrue(evidence['complete'])
        field=sample_field(pool,50,seed=17)
        accuracy=ownership_accuracy(evidence,pool,{'current':field})
        self.assertTrue(accuracy['full_field_verified']);self.assertIn('complete supplied field',accuracy['basis'])
        self.assertEqual(accuracy['denominator'],100)
        self.assertEqual(sum(row['observed'] for row in accuracy['rows'] if row['slot']=='Captain'),100)
        self.assertEqual(sum(row['observed'] for row in accuracy['rows'] if row['slot']=='FLEX'),500)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(files,source_bytes(f.root))

    def test_missing_selected_scores_not_filled_by_other_export(self):
        f,r,m,s,_,_=self.history();pool,_=self.qualify(f,r,m,s)
        original=copy.deepcopy(r)
        backup=Path(r['snapshot']).with_name('other-results.csv');backup.write_bytes(Path(r['snapshot']).read_bytes())
        original.update(snapshot=str(backup))
        def remove(rows):
            h=rows[0]
            for row in rows[1:]:
                if row[h.index('Player')]:row[h.index('FPTS')]=''
        source=self.rewrite(r,remove);evidence=capture_outcomes(source,m,[(original,m)])
        bank=freeze_bank(pool,20,lambda:False)
        actual=evaluate_candidates(evidence,bank,{'current':bank})
        self.assertEqual(actual['status'],'unavailable');self.assertTrue(actual['missing_players'])
        self.assertEqual(evidence['base'],{})

    def test_duplicate_conflicts_and_order_do_not_establish_complete_field(self):
        f,r,m,s,_,_=self.history()
        def conflicting(rows):
            h=rows[0];row=list(rows[1]);row[h.index('Points')]='999';rows.append(row)
        source=self.rewrite(r,conflicting);a=capture_outcomes(source,m,[])
        def reverse(rows):rows[1:]=reversed(rows[1:])
        source=self.rewrite(source,reverse);b=capture_outcomes(source,m,[])
        for key in ('accepted','valid_rosters','conflicting_entries','complete','points','counts','duplicates'):
            self.assertEqual(a[key],b[key])
        self.assertEqual(a['accepted'],99);self.assertFalse(a['complete'])

    def test_score_and_explicit_identity_conflicts_block(self):
        for conflict in ('score','identity'):
            f,r,m,s,_,_=self.history()
            def edit(rows):
                h=rows[0]
                if conflict=='score':
                    row=list(next(row for row in rows[1:] if row[h.index('Player')]));row[h.index('FPTS')]='99';rows.append(row)
                elif conflict=='identity':rows[-1][h.index('PlayerID')]='999999'
                else:rows[1][h.index('Points')]='999'
            source=self.rewrite(r,edit)
            with self.assertRaises(ValueError):capture_outcomes(source,m,[])

    def test_cross_export_conflict_blocks_and_cancel_withholds(self):
        f,r,m,s,_,_=self.history();other=copy.deepcopy(r)
        path=Path(r['snapshot']).with_name('other.csv');path.write_bytes(Path(r['snapshot']).read_bytes());other['snapshot']=str(path)
        def edit(rows):
            h=rows[0];rows[-1][h.index('FPTS')]='99'
        other=self.rewrite(other,edit)
        with self.assertRaisesRegex(ValueError,'Cross-contest'):capture_outcomes(r,m,[(other,m)])
        with self.assertRaises(ai.ImportCancelled):capture_outcomes(r,m,[],cancelled=lambda:True)

    def test_exact_ties_duplicates_and_missing_payouts_are_explicit(self):
        f,r,m,s,_,_=self.history();pool,_=self.qualify(f,r,m,s)
        evidence=capture_outcomes(r,m,[]);by_id={p['FlexID']:p for p in pool}
        from optimizers import ShowdownLineup
        u=evidence['universe'];lineup=ShowdownLineup(by_id[u[0]['key']],[by_id[p['key']] for p in u[1:6]])
        actual=evaluate_candidates(evidence,[lineup],{'current':[lineup]})
        self.assertEqual(actual['status'],'complete');row=actual['models']['current']['rows'][0]
        self.assertEqual(row['supplied_equal_scores'],100);self.assertEqual(row['supplied_rank_if_added'],1)
        self.assertEqual(row['observed_duplicates_lower_bound'],100)
        self.assertEqual(row['supplied_entries_beaten_pct'],0)
        self.assertIn('unavailable',actual['payout_status'])

    def test_outcomes_do_not_change_sim_ranking_and_three_model_evaluation(self):
        f,r,m,s,_,_=self.history();pool,_=self.qualify(f,r,m,s)
        evidence=capture_outcomes(r,m,[]);bank=freeze_bank(pool,20,lambda:False);field=sample_field(pool,50,seed=17)
        args=(bank,pool,field,field,17,30,lambda:False)
        a=compare_sim(*args,calibrated=field);b=compare_sim(*args,calibrated=field,outcomes=evidence)
        self.assertEqual(a['models'],b['models']);self.assertEqual(a['candidate_digest'],b['candidate_digest'])
        self.assertTrue(b['outcome_moments_identical']);self.assertEqual(set(b['actual_outcomes']['models']),{'current','historical','calibrated'})
        self.assertIn('complete supplied field',b['ownership_accuracy']['basis'])

    def test_partial_rosters_use_subset_denominator_and_do_not_certify_field(self):
        f,r,m,s,_,_=self.history();pool,_=self.qualify(f,r,m,s)
        def edit(rows):
            h=rows[0];rows[1][h.index('Lineup')]='unreadable'
            for row in rows[1:]:row[h.index('FieldSize')]=''
        source=self.rewrite(r,edit);evidence=capture_outcomes(source,m,[])
        accuracy=ownership_accuracy(evidence,pool,{})
        self.assertEqual(accuracy['denominator'],99);self.assertEqual(accuracy['accepted_entries'],100)
        self.assertEqual(accuracy['unknown_rosters'],1);self.assertFalse(accuracy['full_field_verified'])
        self.assertIn('subset',accuracy['basis'])

    def test_end_to_end_failure_is_disclosed_and_no_pregame_fallback(self):
        f,r,m,s,_,_=self.history();before=logical_db(f.db)
        report=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,),draw_mode='recorded',compare_sim=True,ownership_calibration=True,evaluate_outcomes=True,scenarios=30,candidate_count=20)
        self.assertEqual(before,logical_db(f.db));self.assertIsNone(report['games'][0]['outcome_status'])
        self.assertIn('Actual outcomes:',render_evaluation(report));self.assertIn('Payouts/ROI unavailable',render_evaluation(report))
        with self.assertRaisesRegex(ValueError,'qualified recorded'):
            evaluate_history('unused','2026-09-24',evaluate_outcomes=True)

    def test_entry_total_discrepancies_block_ranks_without_rounding_independent_scores(self):
        f,r,m,s,_,_=self.history();pool,_=self.qualify(f,r,m,s)
        def edit(rows):
            h=rows[0];rows[1][h.index('Points')]=str(Decimal(rows[1][h.index('Points')])-Decimal('0.00001'))
        source=self.rewrite(r,edit);evidence=capture_outcomes(source,m,[])
        self.assertEqual(evidence['total_discrepancies'],1)
        self.assertEqual(evidence['total_discrepancy_examples'][0]['difference'],'-0.00001')
        bank=freeze_bank(pool,20,lambda:False)
        actual=evaluate_candidates(evidence,bank,{'current':bank})
        self.assertEqual(actual['status'],'complete')
        self.assertIsNone(actual['models']['current']['mean_supplied_entries_beaten_pct'])
        self.assertTrue(all(row['supplied_rank_if_added'] is None for row in actual['models']['current']['rows']))
        self.assertIn('no rounding',actual['standings_status'])
        self.assertEqual(ownership_accuracy(evidence,pool,{})['denominator'],100)
