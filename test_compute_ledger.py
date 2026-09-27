"""Behavioral receipts, bounded storage, failure isolation and real-worker parity."""
from test_environment import install, network_attempts
install()

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import compute_ledger as cl
from build_snapshots import create_snapshot
from candidate_library import initialize, connect, load_candidates, roster_keys, run_search
from optimizers import ShowdownLineup, MultiSportClassicOptimizer
from test_showdown_performance import _showdown_players
from test_nfl_logic import _fixture_players


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.players = _showdown_players()
        self.recipe = dict(sport='NFL', contest_kind='showdown', compute_mode='Deep',
            requested_lineups=2, salary_cap=50000, salary_strategy='Flexible', build_style='Strategic')
        self.row = ShowdownLineup(self.players[0], self.players[1:5] + [self.players[18]])
        self.second = ShowdownLineup(self.players[1], [self.players[0]] + self.players[2:5] + [self.players[18]])
        self.ledger = cl.Ledger(players=self.players, recipe=self.recipe, rules={}, root=self.root)
        self.token = cl._current.set(self.ledger)
        self.addCleanup(cl._current.reset, self.token)

    def receipt(self):
        return cl.read_receipt(self.root / ('compute-' + self.ledger.data['run_id'] + '.json'))

    def test_finish_once_and_no_candidate_rows_persist(self):
        self.ledger.batch([self.row], source='optimizer', requested=1)
        self.ledger.finish(dict(lineups=[self.row], candidate_count=1))
        before = self.receipt()
        self.ledger.finish(dict(lineups=[], cancelled=True))
        self.assertEqual(self.receipt(), before)
        self.assertEqual(before['status'], 'completed')
        self.assertEqual(before['final_candidate_count'], 1)
        self.assertNotIn('Captain', json.dumps(before))
        self.assertNotIn(self.players[0]['Name'], json.dumps(before))
        self.assertFalse(before['resumable'])

    def test_attempts_legal_returned_new_duplicates_are_distinct(self):
        self.ledger.batch([self.row, self.second, self.row], source='optimizer', requested=4,
                          attempted=15, legal=7, cpu=0.01)
        batch = self.ledger.data['batches'][0]
        self.assertEqual([batch[k] for k in ('attempted', 'legal_trials', 'returned', 'new', 'duplicate', 'rejected_trials')],
                         [15, 7, 3, 2, 1, 8])

    def test_showdown_athlete_sets_and_captains_are_distinct(self):
        self.ledger.batch([self.row, self.second], source='optimizer')
        batch = self.ledger.data['batches'][0]
        self.assertEqual(batch['athlete_sets_new'], 1)
        self.assertEqual(batch['captain_assignments_new'], 2)

    def test_unknown_attempt_count_is_not_returned_count(self):
        self.ledger.batch([self.row], source='optimizer')
        batch = self.ledger.data['batches'][0]
        self.assertIsNone(batch['attempted'])
        self.assertIsNone(batch['rejected_trials'])

    def test_solver_fallback_does_not_claim_greedy_only_counts(self):
        players = _fixture_players()
        ledger = cl.Ledger(players=players, recipe=dict(self.recipe, contest_kind='classic'), rules={}, root=self.root)
        token = cl._current.set(ledger)
        try:
            def greedy(**kwargs):
                cl.trial_counts(100, 10)
                return []
            optimizer = MultiSportClassicOptimizer(players)
            with patch('optimizers.HAS_PULP', True), patch.object(optimizer, '_build_lineups_greedy', side_effect=greedy), \
                 patch.object(optimizer, '_build_lineups_pulp', return_value=[players[:9]]):
                optimizer.build_lineups(1)
        finally:
            cl._current.reset(token)
        self.assertIsNone(ledger.data['batches'][0]['attempted'])
        self.assertIsNone(ledger.data['batches'][0]['legal_trials'])

    def test_phase_totals_reconcile_across_batches(self):
        for rows in ([self.row], [self.row, self.second]):
            self.ledger.batch(rows, source='optimizer', requested=2, cpu=.1, wall=.2)
        phase = self.ledger.data['phases']['generation']
        self.assertEqual((phase['calls'], phase['input_count'], phase['output_count']), (2, 4, 3))
        self.assertAlmostEqual(phase['cpu_seconds'], .2)
        self.assertEqual(self.ledger.data['reuse']['deduplicated'], 2)

    def test_cancel_checks_are_not_added_and_partial_batch_not_completed(self):
        checks = []
        @cl.generator(method=False)
        def generate(players, count, cancel_callback):
            if cancel_callback():
                return []
            if cancel_callback():
                return [self.row]
            return [self.row, self.second]
        def stop():
            checks.append(1)
            return len(checks) == 2
        self.assertEqual(generate(self.players, 2, cancel_callback=stop), [self.row])
        self.assertEqual(len(checks), 2)
        self.assertFalse(self.ledger.data['batches'][0]['completed'])
        self.ledger.finish(dict(cancelled=True, lineups=[]))
        receipt = self.receipt()
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertEqual(receipt['checkpoints'][-1]['completed_batch_count'], 0)
        self.assertEqual(receipt['completed_source_state'], {})

    def test_nested_generator_counted_once_and_recovery_source_recorded(self):
        @cl.generator(method=False)
        def inner(players, count):
            return [self.row]
        @cl.source('exposure_recovery')
        @cl.generator(method=False)
        def outer(players, count):
            return inner(players, count)
        outer(self.players, 1)
        self.assertEqual(self.ledger.data['batch_count'], 1)
        self.assertEqual(self.ledger.data['batches'][0]['source'], 'exposure_recovery')

    def test_opponent_field_is_not_candidate_generation(self):
        @cl.generator('field_shaped', method=False, candidate_only=True)
        def field(players, count, **kwargs):
            return [self.row], {}
        field(self.players, 1)
        self.assertEqual(self.ledger.data['batch_count'], 0)
        field(self.players, 1, candidate_mode=True)
        self.assertEqual(self.ledger.data['batch_count'], 1)

    def test_no_input_or_recipe_mutation(self):
        original = copy.deepcopy((self.players, self.recipe))
        self.ledger.batch([self.row], source='optimizer')
        self.ledger.finish(dict(lineups=[self.row]))
        self.assertEqual((self.players, self.recipe), original)

    def test_candidate_compatible_but_simulation_stale(self):
        changed = copy.deepcopy(self.players)
        changed[0]['FlexProjection'] += 1
        a = cl.compatibility_ids(self.players, self.recipe, {}, 'code')
        b = cl.compatibility_ids(changed, self.recipe, {}, 'code')
        self.assertEqual(a[0], b[0]); self.assertNotEqual(a[1], b[1])
        changed[0]['MaxPct'] = 0
        self.assertNotEqual(a[0], cl.compatibility_ids(changed, self.recipe, {}, 'code')[0])

    def test_constraints_and_salary_change_candidate_identity(self):
        baseline = self.ledger.data['candidate_compatibility_id']
        for field, value in [('FlexSalary', 200), ('CptSalary', 300), ('LockCpt', True),
                             ('FadeFlex', True), ('MinPct', 10), ('MaxCptPct', 0)]:
            changed = copy.deepcopy(self.players); changed[0][field] = value
            self.assertNotEqual(baseline, cl.compatibility_ids(changed, self.recipe, {}, cl.app_code_id())[0])
        self.assertNotEqual(baseline, cl.compatibility_ids(self.players, self.recipe, {'min_unique': 3}, cl.app_code_id())[0])

    def test_bounded_batches_keep_totals_and_no_persistent_rows(self):
        for _ in range(cl.MAX_BATCHES + 12):
            self.ledger.batch([self.row], source='optimizer')
        self.ledger.finish(dict(lineups=[self.row]))
        value = self.receipt()
        self.assertEqual(len(value['batches']), cl.MAX_BATCHES)
        self.assertEqual(value['sources']['optimizer']['returned'], cl.MAX_BATCHES + 12)
        self.assertEqual(value['sources']['optimizer']['new'], 1)
        self.assertEqual(value['omitted_batch_details'], 12)
        self.assertLess(len(json.dumps(value)), 250000)

    def test_identity_cap_becomes_unknown_instead_of_false_unique_counts(self):
        with patch.object(cl, 'MAX_IDENTITIES', 1):
            self.ledger.batch([self.row, self.second], source='optimizer')
        self.assertIsNone(self.ledger.data['batches'][0]['new'])
        self.assertIsNone(self.ledger.data['reuse']['deduplicated'])

    def test_checkpoints_follow_natural_boundaries_and_are_bounded(self):
        ticks = [0]
        ledger = cl.Ledger(players=self.players, recipe=self.recipe, rules={}, root=self.root, clock=lambda: ticks[0])
        for value in (29, 30, 59, 60, 119, 120, 299, 300):
            ticks[0] = value; ledger.boundary('generation')
        self.assertEqual([p['elapsed_seconds'] for p in ledger.data['checkpoints']], [30, 60, 120, 300])
        for value in range(600, 30000, 300):
            ticks[0] = value; ledger.boundary('generation')
        self.assertEqual(len(ledger.data['checkpoints']), cl.MAX_CHECKPOINTS)
        self.assertIsNone(ledger.data['checkpoints'][-1]['portfolio_hash'])

    def test_hash_order_and_overlap_are_deterministic(self):
        self.assertEqual(cl.portfolio_hash([self.row], 'showdown'), cl.portfolio_hash(copy.deepcopy([self.row]), 'showdown'))
        self.assertNotEqual(cl.portfolio_hash([self.row, self.second], 'showdown'), cl.portfolio_hash([self.second, self.row], 'showdown'))
        self.ledger.boundary('selection', [self.row], force=True)
        self.ledger.boundary('selection', [self.row, self.second], force=True)
        self.assertEqual(self.ledger.data['checkpoints'][-1]['overlap_previous'], .5)

    def test_atomic_failure_keeps_previous_complete_receipt(self):
        self.ledger.persist(); before = self.receipt()
        self.ledger.data['status'] = 'completed'
        with patch('compute_ledger.os.replace', side_effect=OSError('unavailable')):
            self.assertRaises(OSError, self.ledger.persist)
        self.assertEqual(self.receipt(), before)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_partial_or_unsupported_receipt_rejected(self):
        for changes in ({'atomic_complete': False}, {'schema_version': 99}, {'resumable': True}):
            path = self.root / 'partial.json'
            cl.atomic_write(path, dict(self.ledger.data, **changes))
            self.assertRaises(ValueError, cl.read_receipt, path)

    def test_memory_sampling_is_low_frequency_and_failure_is_unknown(self):
        ticks = [0]
        with patch.object(cl, 'memory_bytes', side_effect=OSError('unsupported')) as sample:
            ledger = cl.Ledger(players=self.players, recipe=self.recipe, rules={}, root=self.root, clock=lambda: ticks[0])
            for _ in range(10): ledger.boundary('generation')
            self.assertEqual(sample.call_count, 1)
            ticks[0] = 5; ledger.boundary('generation')
            self.assertEqual(sample.call_count, 2)
            ledger.finish(dict(lineups=[]))
        self.assertIsNone(ledger.data['peak_observed_memory_bytes'])

    def test_cpu_capability_failure_remains_unknown(self):
        with patch.object(cl.time, 'thread_time', side_effect=OSError('unsupported')):
            ledger = cl.Ledger(players=self.players, recipe=self.recipe, rules={}, root=self.root)
            ledger.finish(dict(lineups=[]))
        self.assertIsNone(ledger.data['cpu_seconds'])

    def test_retention_preserves_all_nonledger_evidence(self):
        protected = ['results.csv', 'salary.csv', 'snapshot.json', 'archive.zip', 'export.csv',
                     'learning.sqlite', 'compute-not-owned.json', 'compute-' + 'e' * 32 + '.json']
        for name in protected: (self.root / name).write_text('historical evidence', encoding='utf-8')
        for index in range(4):
            run = f'{index:032x}'
            cl.atomic_write(self.root / f'compute-{run}.json', dict(self.ledger.data,
                run_id=run, started_at=str(index), status='running' if index == 0 else 'completed'))
        cl.retain(self.root, 2)
        self.assertFalse((self.root / ('compute-' + '0' * 32 + '.json')).exists())
        for name in protected:
            self.assertEqual((self.root / name).read_text(encoding='utf-8'), 'historical evidence')
        self.assertEqual(len(list(self.root.glob('compute-*.json'))), 4)

    def test_shareable_summary_is_deterministic_and_ignores_private_fields(self):
        summary = self.ledger.summary()
        summary.update(path=r'C:\Users\private\contest', username='private', contest_label='secret')
        report = '\n'.join(cl.summary_lines(summary))
        self.assertEqual(report, '\n'.join(cl.summary_lines(copy.deepcopy(summary))))
        self.assertNotIn('private', report); self.assertNotIn('secret', report)
        self.assertIn('peak observed', report)

    def library(self):
        path = self.root / 'library.dfslib'
        snapshot = create_snapshot(self.players, self.recipe, {})
        initialize(path, snapshot)
        with connect(path) as con:
            for index, row in enumerate((self.row, self.second)):
                encoded = json.dumps(roster_keys(row, 'showdown'))
                con.execute('INSERT INTO candidates VALUES (?,?,?,?,?)', (encoded, encoded, 0, 'Balanced', index))
        return path

    def test_actual_library_accept_reject_counts(self):
        path = self.library()
        changed = copy.deepcopy(self.players); changed[0]['FadeCpt'] = True
        rows, report = load_candidates(path, changed, kind='showdown', salary_cap=50000)
        self.assertEqual((len(rows), report['accepted'], report['rejected']), (1, 1, 1))
        self.assertEqual(self.ledger.data['reuse']['loaded'], 2)
        self.assertEqual(self.ledger.data['reuse']['accepted'], 1)
        self.assertEqual(self.ledger.data['reuse']['rejected'], 1)

    def test_library_stale_rejection_authority_unchanged(self):
        path = self.library()
        with patch('candidate_library.code_id', return_value='different-code'):
            with self.assertRaisesRegex(ValueError, 'different app code'):
                load_candidates(path, self.players, kind='showdown', salary_cap=50000)
        self.assertEqual(self.ledger.data['reuse']['library_status'], 'rejected')
        self.assertIsNone(self.ledger.data['reuse']['rejected'])

    def test_only_completed_scenario_reuse_is_reported(self):
        cl.cache_observation('digest', dict(reused_scenarios=0))
        cl.cache_observation('digest', dict(reused_scenarios=12))
        self.assertEqual(self.ledger.data['reuse']['reused_scenarios'], 12)
        self.assertEqual(self.ledger.data['simulation_cache_ids'], ['digest'])

    def test_no_network(self):
        self.ledger.finish(dict(lineups=[self.row]))
        self.assertEqual(network_attempts, [])

    def test_preparation_resume_records_only_existing_saved_work(self):
        snapshot = create_snapshot(self.players, self.recipe, {})
        path = self.root / 'preparation.dfslib'
        with patch.object(cl, 'folder', return_value=self.root):
            self.assertEqual(run_search(path, snapshot, seconds=5, candidate_limit=2, batch_size=2), 2)
            self.assertEqual(run_search(path, snapshot, seconds=5, candidate_limit=2, batch_size=2), 2)
        values = [cl.read_receipt(p) for p in self.root.glob('compute-*.json')]
        resumed = next(v for v in values if v['reuse']['resumed_batches'])
        self.assertEqual(resumed['purpose'], 'preparation')
        self.assertEqual(resumed['reuse']['accepted'], 2)
        self.assertEqual(resumed['reuse']['generated'], 0)
        self.assertEqual(resumed['reuse']['deduplicated'], 2)
        cl.resumed([roster_keys(self.row, 'showdown')], 1)
        self.ledger.batch([self.row, self.second], source='optimizer')
        self.assertEqual(self.ledger.data['batches'][0]['new'], 1)
        self.assertEqual(self.ledger.data['batches'][0]['duplicate'], 1)

    def test_source_archetypes_survive_aggregation(self):
        from nfl_simulation import SimLineup
        ledger = cl.Ledger(players=_fixture_players(), recipe=dict(self.recipe, contest_kind='classic'), rules={}, root=self.root)
        row = SimLineup(_fixture_players()[:9], candidate_source='scenario_built', candidate_archetype='Ceiling')
        ledger.batch([row], source='scenario_built')
        self.assertEqual(ledger.data['sources']['scenario_built']['archetypes'], {'Ceiling': 1})
        self.assertEqual(row.candidate_source, 'scenario_built')

    def test_retained_rows_participate_in_compatibility_identity(self):
        a = cl.compatibility_ids(self.players, self.recipe, {}, 'code')
        b = cl.compatibility_ids(self.players, dict(self.recipe, retained_signatures=cl.signatures([self.row], 'showdown')), {}, 'code')
        self.assertNotEqual(a, b)

    def test_normal_error_after_cancel_is_still_recorded_cancelled(self):
        worker = type('Worker', (), {})()
        import threading
        worker._cancel_event = threading.Event(); worker._cancel_event.set()
        worker.players = self.players; worker.portfolio_rules = {}; worker.compute_telemetry = True
        worker.repair_source = ''; worker.field_calibration = {}; worker.contest_profile = {}
        @cl.instrument_worker
        def run(worker):
            return None  # Existing worker delivered its own error; no receipt rewrite.
        with patch.object(cl, 'recipe_for', return_value=self.recipe), patch.object(cl, 'folder', return_value=self.root):
            self.assertIsNone(run(worker))
        value = cl.read_receipt(next(self.root.glob('compute-*.json')))
        self.assertEqual(value['status'], 'cancelled')

    def test_private_recipe_values_and_batch_labels_are_not_serialized(self):
        ledger = cl.Ledger(players=self.players, recipe=dict(self.recipe, build_style='private-user',
            salary_strategy=r'C:\Users\private-user', deep_compute={'private': 'private-user'}),
            rules={}, root=self.root)
        ledger.batch([self.row], source='private-user', style='private-user', seed='private-user')
        self.assertNotIn('private-user', json.dumps(ledger.data))


class WorkerLedgerTests(unittest.TestCase):
    def worker(self, **kwargs):
        from main_window import LineupBuildWorker
        options = dict(kind='classic', num_lineups=2, salary_cap=50000, salary_strategy='Flexible')
        options.update(kwargs)
        worker = LineupBuildWorker(_fixture_players(), **options)
        results, errors = [], []
        worker.finished.connect(results.append); worker.error.connect(errors.append)
        return worker, results, errors

    def test_fast_worker_enabled_disabled_same_rows(self):
        values = []
        for enabled in (False, True):
            worker, results, errors = self.worker(compute_telemetry=enabled)
            worker.run(); self.assertFalse(errors); self.assertEqual(len(results), 1)
            result = results[0]; values.append([roster_keys(row, 'classic') for row in result['lineups']])
            self.assertEqual('compute_ledger' in result['sim_report'], enabled)
        self.assertEqual(*values)

    def test_cancelled_saved_repair_receipt_preserved(self):
        worker, results, errors = self.worker(repair_source='saved')
        worker.request_cancel(); worker.run()
        self.assertFalse(errors); self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['cancelled'])
        summary = results[0]['sim_report']['compute_ledger']
        self.assertEqual(summary['purpose'], 'saved_repair')
        self.assertEqual(summary['status'], 'cancelled')

    def test_telemetry_storage_and_capability_failures_do_not_fail_build(self):
        worker, results, errors = self.worker()
        with patch.object(cl, 'memory_bytes', side_effect=OSError('unavailable')), \
             patch.object(cl, 'atomic_write', side_effect=OSError('read-only')), \
             patch.object(cl.time, 'thread_time', side_effect=OSError('unavailable')):
            worker.run()
        self.assertFalse(errors); self.assertEqual(len(results[0]['lineups']), 2)

    def test_telemetry_initialization_failure_does_not_fail_build(self):
        worker, results, errors = self.worker()
        with patch.object(cl, 'Ledger', side_effect=ValueError('unsupported metadata')):
            worker.run()
        self.assertFalse(errors); self.assertEqual(len(results[0]['lineups']), 2)

    def test_cancel_during_deep_keeps_batch_incomplete(self):
        worker, results, errors = self.worker(compute_mode='Deep', sim_enabled=True, repair_source='saved',
            deep_options=dict(candidates=40, shortlist=20, field=80, screening=250))
        worker.progress.connect(lambda *args: worker.request_cancel())
        worker.run()
        self.assertFalse(errors); self.assertTrue(results[0]['cancelled'])
        summary = results[0]['sim_report']['compute_ledger']
        receipt = cl.read_receipt(cl.folder() / ('compute-' + summary['run_id'] + '.json'))
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertLessEqual(receipt['completed_batch_count'], receipt['batch_count'])


class RealPipelineParityTests(unittest.TestCase):
    def check_case(self, kind, mode):
        from scripts.benchmark_compute_ledger import observation
        disabled = observation(kind, mode, False)
        enabled = observation(kind, mode, True)
        for field in ('candidate_signature', 'scored_signature', 'final_signature', 'candidate_count', 'final_count'):
            self.assertEqual(disabled[field], enabled[field], field)
        self.assertEqual(enabled['network_attempts'], [])

    def test_fast_classic_parity(self): self.check_case('classic', 'Fast')
    def test_deep_classic_parity(self): self.check_case('classic', 'Deep')
    def test_fast_showdown_parity(self): self.check_case('showdown', 'Fast')
    def test_deep_showdown_parity(self): self.check_case('showdown', 'Deep')


if __name__ == '__main__':
    unittest.main()
