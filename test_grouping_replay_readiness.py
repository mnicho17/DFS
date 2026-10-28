from test_environment import install
install()
from contextlib import closing
import hashlib,json,unittest,zipfile
from unittest.mock import patch
from pathlib import Path
from test_hindsight_evidence import SourceFixture
from test_portfolio_risk_evidence import logical_db,source_bytes
from build_snapshots import fingerprint
import review_build_evidence as be
from grouping_replay_readiness import inspect_archives,render

class GroupingReplayReadinessTests(unittest.TestCase):
    def fixture(self,kind='showdown'):
        f=SourceFixture(kind);self.addCleanup(f.close);f.archive();return f
    def edit(self,f,change):
        p=f.root/'build-archives'/'archive.zip'
        with zipfile.ZipFile(p) as z:files={n:z.read(n) for n in z.namelist()}
        snap=json.loads(files['input-snapshot.json']);payload=json.loads(files['lineups.json'])
        change(snap,payload)
        snap['input_id']=fingerprint(snap['inputs']);payload['metadata']['input_id']=snap['input_id']
        files['input-snapshot.json']=json.dumps(snap).encode();files['lineups.json']=json.dumps(payload).encode()
        files['manifest.json']=json.dumps(dict(metadata=payload['metadata'],sha256={n:hashlib.sha256(v).hexdigest() for n,v in files.items() if n!='manifest.json'})).encode()
        with zipfile.ZipFile(p,'w') as z:
            for n,v in files.items():z.writestr(n,v)
    def test_pregame_inputs_are_not_a_paired_trial_and_inventory_is_readonly(self):
        f=self.fixture();db=logical_db(f.db);sources=source_bytes(f.root)
        r=inspect_archives(f.root)
        self.assertEqual(r['frozen_inputs_available'],1);self.assertEqual(r['paired_replay_ready'],0)
        self.assertFalse(r['rows'][0]['paired_replay_ready']);self.assertEqual(len(r['rows'][0]['replay_blockers']),2)
        self.assertEqual(db,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
        self.assertIn('not a historical qualification',render(r))
    def test_inputs_and_outputs_must_both_precede_kickoff(self):
        for which in ('input','output','naive','reversed','cancelled'):
            with self.subTest(which=which):
                f=self.fixture()
                def change(s,p):
                    if which=='input':s['created_at']='2026-09-21T23:00:00-04:00'
                    if which=='output':p['metadata']['created_at']='2026-09-21T23:00:00-04:00'
                    if which=='naive':p['metadata']['created_at']='2026-09-21T18:10:00'
                    if which=='reversed':p['metadata']['created_at']='2026-09-21T17:00:00-04:00'
                    if which=='cancelled':p['metadata']['build_status']='cancelled'
                self.edit(f,change);r=inspect_archives(f.root)
                self.assertEqual(r['frozen_inputs_available'],0);self.assertTrue(r['rows'][0]['input_issues'])
    def test_conflicting_explicit_output_identity_is_excluded(self):
        f=self.fixture();self.edit(f,lambda s,p:p['lineups'][0]['slots'][0]['player'].update(CptID='1101'))
        r=inspect_archives(f.root);self.assertEqual(r['rows'],[]);self.assertEqual(len(r['excluded']),1)
    def test_missing_forecast_never_becomes_available_and_zero_is_valid(self):
        for value,expected in ((None,0),(True,0),(0,1),(-1,1)):
            f=self.fixture();self.edit(f,lambda s,p:s['inputs']['players'][0].update(FlexProjection=value))
            self.assertEqual(inspect_archives(f.root)['frozen_inputs_available'],expected)
    def test_checksum_corruption_is_excluded(self):
        f=self.fixture();p=f.root/'build-archives'/'archive.zip'
        with zipfile.ZipFile(p) as z:files={n:z.read(n) for n in z.namelist()}
        files['lineups.json']+=b' '
        with zipfile.ZipFile(p,'w') as z:
            for n,v in files.items():z.writestr(n,v)
        r=inspect_archives(f.root);self.assertEqual(r['rows'],[]);self.assertIn('checksum',r['excluded'][0]['reason'])
    def test_file_and_byte_limits_abort_without_partial_readiness(self):
        f=self.fixture()
        with patch('review_build_evidence.HISTORY_MAX_FILES',0):
            with self.assertRaisesRegex(ValueError,'incomplete'):inspect_archives(f.root)
        with patch('review_build_evidence.HISTORY_MAX_TOTAL_BYTES',1):
            with self.assertRaises(OverflowError):inspect_archives(f.root)
    def test_cancel_and_inventory_changes_abort(self):
        f=self.fixture()
        with self.assertRaises(be.Cancelled):inspect_archives(f.root,lambda:True)
        def changed(text):(f.root/'build-archives'/'new.zip').write_bytes(b'new')
        with self.assertRaisesRegex(ValueError,'inventory changed'):inspect_archives(f.root,progress=changed)
    def test_changed_archive_bytes_during_recheck_abort(self):
        f=self.fixture();original=be.Reader.archive_details
        def changed(reader,path):
            value=original(reader,path);path.write_bytes(path.read_bytes()+b' ');return value
        with patch('review_build_evidence.Reader.archive_details',changed):
            with self.assertRaisesRegex(ValueError,'inventory changed|bytes changed'):inspect_archives(f.root)
    def test_format_and_preset_are_not_inferred(self):
        f=self.fixture('classic');r=inspect_archives(f.root)
        self.assertEqual(r['rows'],[]);self.assertEqual(r['skipped']['other_format'],1)
        f=self.fixture();r=inspect_archives(f.root,preset='150-Max')
        self.assertEqual(r['rows'],[]);self.assertEqual(r['skipped']['other_or_unknown_preset'],1)
    def test_unverified_trial_claim_in_metadata_never_promotes_replay(self):
        f=self.fixture();self.edit(f,lambda s,p:p['metadata'].update(paired_replay_ready=True))
        self.assertEqual(inspect_archives(f.root)['paired_replay_ready'],0)

    def test_missing_faded_player_is_reported_without_erasing_other_forecasts(self):
        f=self.fixture();self.edit(f,lambda s,p:s['inputs']['players'][-1].update(ProjectionSource='Missing forecast',FadeFlex=True,FadeCpt=True))
        r=inspect_archives(f.root);row=r['rows'][0]
        self.assertEqual(row['forecast_coverage'],dict(players=8,known_players=7,missing_players=1,missing_with_recorded_exclusions=1))
        self.assertEqual(r['pregame_completed'],1);self.assertEqual(r['frozen_inputs_available'],0)
        self.assertIn('full pool is preserved',render(r))

if __name__=='__main__':unittest.main()
