"""Offline calibration evidence: no extra outcomes, games or model fitting."""
from test_environment import install, network_attempts
install()
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from distribution_calibration import summarize, calibration_summary, report_lines
from scoring_distributions import DistributionCapture


def row(**changes):
    return dict(dict(name='synthetic player', game='AAA|BBB|2099-09-21T20:00:00+00:00',
        kind='showdown', model='a'*64, position='RB', role='Other depth roles / rotation',
        captured_at='2099-09-21T19:00:00+00:00', capture_id='b'*64, scenarios=100,
        actual=5., mean=4., p10=1., p25=2., p50=4., p75=6., p90=9.,
        expected_below_p10=.1, expected_above_p90=.1), **changes)


class CalibrationTests(unittest.TestCase):
    def all_group(self, result):
        return next(g for g in result['groups'] if g['axis']=='all')

    def test_quantile_coverage_mae_bias_and_tails(self):
        result = summarize([row(actual=0), row(name='second', actual=10)])
        group = self.all_group(result)['player_weighted']
        self.assertEqual((group['below_p10_pct'], group['inside_p10_p90_pct'], group['above_p90_pct']), (50,0,50))
        self.assertEqual(group['mean_score_mae'],5)
        self.assertEqual(group['actual_minus_mean_bias'],1)
        self.assertEqual(group['quantile_coverage']['p50']['at_or_below_pct'],50)

    def test_repeated_contests_entries_and_captain_do_not_add_samples(self):
        evidence = row()
        result = summarize([evidence]*150)
        self.assertEqual(result['unique_player_games'],1)
        self.assertEqual(result['independent_games'],1)
        self.assertEqual(result['excluded_repeated_or_conflicting'],149)
        self.assertEqual(self.all_group(result)['player_weighted']['player_games'],1)

    def test_conflicting_actual_scores_exclude_all_formats_and_models(self):
        result = summarize([row(actual=5), row(actual=6, model='b'*64, kind='classic')])
        self.assertEqual(result['conflicting_player_games'],1)
        self.assertEqual(result['groups'],[])

    def test_models_and_formats_keep_separate_observations(self):
        result = summarize([row(),row(model='c'*64),row(kind='classic')])
        self.assertEqual(len([g for g in result['groups'] if g['axis']=='all']),3)
        self.assertEqual(result['unique_player_games'],1)
        self.assertEqual(result['independent_games'],1)

    def test_positions_and_recorded_roles_have_separate_groups(self):
        result = summarize([row(), row(name='qb',position='QB',role='Backup QBs')])
        labels = {g['label'] for g in result['groups'] if g['axis']=='position_role'}
        self.assertEqual(labels, {'RB / Other depth roles / rotation', 'QB / Backup QBs'})

    def test_equal_game_weight_does_not_overweight_large_game(self):
        rows = [row(name=str(i),actual=14) for i in range(10)]
        rows += [row(game='CCC|DDD|2099-09-21T20:00:00+00:00',actual=4)]
        group = self.all_group(summarize(rows))
        self.assertAlmostEqual(group['player_weighted']['mean_score_mae'],100/11)
        self.assertEqual(group['equal_game_weighted']['mean_score_mae'],5)

    def test_zero_negative_and_boundary_outcomes_are_known(self):
        result = summarize([row(actual=0,p10=-1), row(name='two',actual=-1,p10=-1), row(name='three',actual=9)])
        self.assertEqual(result['unique_player_games'],3)
        self.assertEqual(self.all_group(result)['player_weighted']['inside_p10_p90_pct'],100)

    def test_unknown_postgame_missing_model_and_partial_are_excluded(self):
        result = summarize([row(actual=None),row(actual=float('nan')),row(model='unknown'),
            row(captured_at='2099-09-21T21:00:00+00:00'),row(scenarios=0),row(p25=99)])
        self.assertEqual(result['excluded_invalid'],6)
        self.assertEqual(result['status'],'unavailable')

    def test_legacy_quartiles_stay_unknown(self):
        old = row(); old.pop('p25'); old.pop('p75')
        group = self.all_group(summarize([old]))['player_weighted']['quantile_coverage']
        self.assertIsNone(group['p25']['at_or_below_pct'])
        self.assertEqual(group['p25']['player_games'],0)
        self.assertIsNotNone(group['p50']['at_or_below_pct'])

    def test_latest_capture_per_model_is_selected_deterministically(self):
        old = row(mean=2); new = row(mean=5,captured_at='2099-09-21T19:30:00+00:00')
        result = summarize([old,new])
        self.assertEqual(result,summarize([new,old]))
        self.assertEqual(self.all_group(result)['player_weighted']['actual_minus_mean_bias'],0)

    def test_read_only_sql_no_input_mutation_and_private_names_absent(self):
        data = row(name='private username',position=r'C:\private',role='private contest label')
        original = copy.deepcopy(data)
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE distribution_validations(import_id,payload)')
        conn.execute('INSERT INTO distribution_validations VALUES (?,?)', ('one',json.dumps({'rows':[data]})))
        before = conn.total_changes
        result = calibration_summary(conn)
        self.assertEqual(before,conn.total_changes)
        self.assertEqual(data,original)
        text = json.dumps(result)+'\n'.join(report_lines(result))
        self.assertNotIn('private',text)
        self.assertEqual(network_attempts,[])

    def test_no_evidence_means_unavailable_not_recreated(self):
        with sqlite3.connect(':memory:') as conn:
            self.assertEqual(calibration_summary(conn)['status'],'unavailable')

    def test_new_quartiles_come_from_existing_exact_outcomes(self):
        player=dict(Name='Example',FlexID='p',Position='RB')
        capture=DistributionCapture([player],'classic',100,7)
        for i in range(100):capture.record({'p':float(i)})
        value=capture.finish(100)['players'][0]
        self.assertEqual([value[k] for k in ('p10','p25','p50','p75','p90')],[9,24,49,74,89])


class ReconciliationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.db=self.root/'history.sqlite'
        from learning_db import init_historical_import_tables
        with closing(sqlite3.connect(self.db)) as conn:
            init_historical_import_tables(conn)
            conn.commit()

    def test_reconciliation_has_separate_purpose_and_phases(self):
        import compute_ledger as cl
        from historical_identity import reconcile
        with patch.object(cl,'folder',return_value=self.root/'ledger'):
            values=reconcile(self.db)
        self.assertEqual(values,())
        receipt=cl.read_receipt(next((self.root/'ledger').glob('compute-*.json')))
        self.assertEqual(receipt['purpose'],'historical_reconciliation')
        self.assertEqual(receipt['status'],'completed')
        self.assertEqual(receipt['historical_identity']['total_historical_contests'],0)
        self.assertEqual(receipt['phases']['historical_identity_derivation']['output_count'],0)
        self.assertIsNone(receipt['candidate_compatibility_id'])
        self.assertNotIn(str(self.db),json.dumps(receipt))

    def test_cancelled_reconciliation_keeps_exception_and_atomicity(self):
        import compute_ledger as cl
        from historical_identity import reconcile
        from analysis_imports import ImportCancelled
        before=self.db.read_bytes()
        with patch.object(cl,'folder',return_value=self.root/'ledger'):
            with self.assertRaises(ImportCancelled):reconcile(self.db,cancelled=lambda:True)
        receipt=cl.read_receipt(next((self.root/'ledger').glob('compute-*.json')))
        self.assertEqual(receipt['status'],'cancelled')
        self.assertEqual(self.db.read_bytes(),before)

    def test_read_model_stays_read_only_without_telemetry_writes(self):
        import compute_ledger as cl
        from historical_identity import qualified_contests
        before=self.db.read_bytes()
        with patch.object(cl,'atomic_write') as write:
            qualified_contests(self.db)
        write.assert_not_called()
        self.assertEqual(self.db.read_bytes(),before)

    def test_disk_failure_does_not_fail_reconciliation(self):
        import compute_ledger as cl
        from historical_identity import reconcile
        with patch.object(cl,'atomic_write',side_effect=OSError('Disk full')):
            self.assertEqual(reconcile(self.db),())


if __name__=='__main__':unittest.main()
