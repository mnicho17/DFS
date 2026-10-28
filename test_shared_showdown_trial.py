from test_environment import install
install()
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json,unittest
from optimizers import ShowdownLineup
from build_snapshots import fingerprint
from test_hindsight_evidence import SourceFixture
import test_primary_showdown_scoring as primary
from test_ranking_audit_efficiency import values,report_values
import shared_showdown_trial as trial
from showdown_simulation import simulate_showdown

class SharedTrialTests(unittest.TestCase):
    def fixture(self):
        f=SourceFixture();self.addCleanup(f.close)
        players=f.snap['inputs']['players'];rows=[]
        for p in players:
            if p['Position']=='QB':p['NFLQBEligible']=True
        f.snap['input_id']=fingerprint(f.snap['inputs'])
        for i in range(4):rows.append(ShowdownLineup(players[i],[players[j] for j in range(8) if j!=i][:5]))
        return f,rows,rows+rows[:2],f.root/'shared.dfstrial.json'
    def edit(self,path,change):
        value=json.loads(path.read_text(encoding='utf-8'));change(value['payload']);value['trial_id']=fingerprint(value['payload']);path.write_text(json.dumps(value),encoding='utf-8')
    def test_capture_and_replay_keep_exact_normal_scores_hits_and_forecasts(self):
        f,rows,field,path=self.fixture();before=deepcopy(f.snap);original=deepcopy(rows)
        normal=simulate_showdown(deepcopy(rows),deepcopy(before['inputs']['players']),scenarios=60,field_lineup_count=len(field),diagnostic_field=deepcopy(field))
        capture=trial.capture_trial(f.snap,rows,field,path,scenarios=60)
        replay=trial.replay_trial(path)
        for result in (capture['result'],replay['result']):
            self.assertEqual(primary.exact(values(normal)),primary.exact(values(result)))
            # Diagnostic mode adds an opponent-field receipt; model data match.
            ordinary=report_values(normal);diagnostic=report_values(result)
            self.assertIsNone(ordinary.pop('sensitivity_field_id'))
            self.assertIsNotNone(diagnostic.pop('sensitivity_field_id'))
            self.assertEqual(primary.exact(ordinary),primary.exact(diagnostic))
        self.assertEqual(f.snap,before);self.assertEqual(rows,original)
        saved=trial.load_trial(path);self.assertEqual(saved['payload']['snapshot'],before)
        self.assertFalse(saved['payload']['historical_qualified']);self.assertEqual(len(saved['payload']['frames']),60)
    def test_scenario_outcomes_are_frozen_not_replaced_by_replay_draws(self):
        f,rows,field,path=self.fixture();captured=trial.capture_trial(f.snap,rows,field,path,scenarios=10)
        with patch('showdown_simulation._scenario_outcomes',return_value={str(p['FlexID']):999 for p in f.snap['inputs']['players']}):
            replay=trial.replay_trial(path)
        self.assertEqual(primary.exact(values(captured['result'])),primary.exact(values(replay['result'])))
    def test_flex_order_and_duplicate_opponents_are_preserved(self):
        f,rows,field,path=self.fixture();rows[0]=ShowdownLineup(rows[0]['Captain'],list(reversed(rows[0]['Flex'])))
        trial.capture_trial(f.snap,rows,field,path,scenarios=5);p=trial.load_trial(path)['payload']
        self.assertEqual([s['key'] for s in p['candidates'][0]][1:],[str(v['FlexID']) for v in rows[0]['Flex']])
        self.assertEqual(len(p['field']),6)
    def test_duplicate_candidates_and_conflicting_explicit_ids_are_rejected(self):
        f,rows,field,path=self.fixture()
        with self.assertRaisesRegex(ValueError,'Duplicate'):trial.capture_trial(f.snap,rows+[rows[0]],field,path,scenarios=1)
        bad=deepcopy(rows);bad[0]['Captain']['CptID']='1101'
        with self.assertRaisesRegex(ValueError,'contradicts'):trial.capture_trial(f.snap,bad,field,path,scenarios=1)
        self.assertFalse(path.exists())
    def test_checksum_and_rehashed_missing_frame_identity_are_rejected(self):
        f,rows,field,path=self.fixture();trial.capture_trial(f.snap,rows,field,path,scenarios=3)
        value=json.loads(path.read_text(encoding='utf-8'));value['payload']['frames'][0].pop(next(iter(value['payload']['frames'][0])))
        path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'checksum'):trial.load_trial(path)
        value['trial_id']=fingerprint(value['payload']);path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'missing identities'):trial.load_trial(path)
    def test_incomplete_frames_and_runtime_change_do_not_become_replayable(self):
        f,rows,field,path=self.fixture();trial.capture_trial(f.snap,rows,field,path,scenarios=3)
        with patch('shared_showdown_trial.model_version',return_value='different'):
            with self.assertRaisesRegex(ValueError,'implementation changed'):trial.replay_trial(path)
        self.edit(path,lambda p:p['frames'].pop())
        with self.assertRaisesRegex(ValueError,'Incomplete'):trial.load_trial(path)
    def test_cancelled_capture_and_replay_publish_no_partial_result(self):
        f,rows,field,path=self.fixture();stop=[False]
        def progress(*args):stop[0]=True
        with self.assertRaises(trial.TrialCancelled):trial.capture_trial(f.snap,rows,field,path,scenarios=100,cancelled=lambda:stop[0],progress=progress)
        self.assertFalse(path.exists())
        stop[0]=False;trial.capture_trial(f.snap,rows,field,path,scenarios=100);before=path.read_bytes()
        with self.assertRaises(trial.TrialCancelled):trial.replay_trial(path,cancelled=lambda:stop[0],progress=progress)
        self.assertEqual(path.read_bytes(),before)
    def test_player_value_byte_and_roster_bounds_prevent_publication(self):
        f,rows,field,path=self.fixture()
        for limit in ('MAX_VALUES','MAX_BYTES','MAX_CANDIDATES','MAX_FIELD','MAX_PLAYERS'):
            with self.subTest(limit=limit),patch('shared_showdown_trial.'+limit,1):
                with self.assertRaises(ValueError):trial.capture_trial(f.snap,rows,field,path,scenarios=2)
                self.assertFalse(path.exists())
    def test_postgame_inputs_and_existing_destination_are_preserved(self):
        f,rows,field,path=self.fixture();bad=deepcopy(f.snap);bad['created_at']='2026-09-22T18:00:00-04:00'
        with self.assertRaisesRegex(ValueError,'pre-game'):trial.capture_trial(bad,rows,field,path,scenarios=1)
        path.write_bytes(b'existing evidence')
        with self.assertRaises(FileExistsError):trial.capture_trial(f.snap,rows,field,path,scenarios=1)
        self.assertEqual(path.read_bytes(),b'existing evidence')
    def test_storage_failure_does_not_publish_capture(self):
        f,rows,field,path=self.fixture()
        with patch('shared_showdown_trial.atomic_json',side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):trial.capture_trial(f.snap,rows,field,path,scenarios=1)
        self.assertFalse(path.exists())

    def test_recorded_runtime_and_qb_eligibility_are_required(self):
        f,rows,field,path=self.fixture();bad=deepcopy(f.snap)
        del bad['inputs']['players'][0]['NFLQBEligible'];bad['input_id']=fingerprint(bad['inputs'])
        with self.assertRaisesRegex(ValueError,'eligibility is missing'):trial.capture_trial(bad,rows,field,path,scenarios=1)
        trial.capture_trial(f.snap,rows,field,path,scenarios=1)
        with patch('shared_showdown_trial.runtime_identity',return_value={'python':'different'}):
            with self.assertRaisesRegex(ValueError,'runtime identity changed'):trial.load_trial(path)
    def test_destination_created_during_capture_is_never_overwritten(self):
        f,rows,field,path=self.fixture()
        def race(*args):path.write_bytes(b'other evidence')
        with self.assertRaises(FileExistsError):trial.capture_trial(f.snap,rows,field,path,scenarios=50,progress=race)
        self.assertEqual(path.read_bytes(),b'other evidence')
        self.assertEqual(list(path.parent.glob('*.trial.tmp')),[])

if __name__=='__main__':unittest.main()
