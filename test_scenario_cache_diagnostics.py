from test_environment import install
install()
import copy
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from build_snapshots import fingerprint
from scenario_cache import ScenarioReplay
from scenario_cache_diagnostics import components,compare,record,LABELS,LIMIT


class ScenarioCacheDiagnosticTests(unittest.TestCase):
    def identity(self):
        return dict(schema=2,scores_codec='zlib-double-v1',players=[dict(Name='Private athlete',FlexID='1',
            Team='A',GameInfo='A@B 10/21/2026 08:15PM ET',FlexProjection=10,InjuryStatus='Active',Own=25,FlexSalary=5000)],
            fields=[[['1']]],kind='showdown',seed=7,count=1,model='build',byteorder='little',python='runtime',context={})

    def replay(self,folder,identity):
        return ScenarioReplay(identity['players'],identity['fields'],kind=identity['kind'],seed=identity['seed'],
            count=identity['count'],enabled=True,folder=folder,context=identity['context'])

    def save(self,folder,identity):
        cache=self.replay(folder,identity)
        cache.frame(0,lambda:({'1':10.0},[5.0]),Counter())
        return cache.finish(1)

    def test_component_fingerprints_identify_each_changed_input_without_raw_values(self):
        original=self.identity();before=components(original)
        cases=[('player forecasts',lambda x:x['players'][0].update(FlexProjection=11)),
               ('player availability and roles',lambda x:x['players'][0].update(InjuryStatus='Out')),
               ('player ownership',lambda x:x['players'][0].update(Own=30)),
               ('player ownership',lambda x:x['players'][0].update(ProjectedOwnership=30)),
               ('other player inputs',lambda x:x['players'][0].update(ShowdownSalary=6000)),
               ('other player inputs',lambda x:x['players'][0].update(FlexSalary=6000)),
               ('opponent field',lambda x:x.update(fields=[[['other']]])),
               ('random seed',lambda x:x.update(seed=8)),('scenario count',lambda x:x.update(count=2)),
               ('app/model version',lambda x:x.update(model='changed')),('runtime/codec',lambda x:x.update(python='new')),
               ('context/history settings',lambda x:x.update(context={'history_profile':{'version':2}}))]
        for label,change in cases:
            changed=copy.deepcopy(original);change(changed);after=components(changed)
            self.assertEqual([k for k in LABELS if before[k]!=after[k]],[label])
        self.assertNotIn('Private athlete',json.dumps(before))
        self.assertTrue(all(len(value)==64 for value in before.values()))

    def test_exact_key_is_unchanged_and_warm_replay_still_uses_verified_frames(self):
        identity=self.identity()
        import sys
        identity.update(byteorder=sys.byteorder,python=sys.version)
        with tempfile.TemporaryDirectory() as raw,patch('candidate_library.code_id',return_value='build'):
            folder=Path(raw);saved=self.save(folder,identity)
            self.assertEqual(saved['cache_id'],fingerprint(identity))
            cache=self.replay(folder,identity)
            outcomes,_=cache.frame(0,lambda: self.fail('must use verified frames'),Counter())
            self.assertEqual(outcomes,{'1':10.0})
            self.assertEqual(cache.finish(1)['reused_scenarios'],1)

    def test_changed_projection_reports_comparison_and_recomputes(self):
        identity=self.identity()
        with tempfile.TemporaryDirectory() as raw,patch('candidate_library.code_id',return_value='build'):
            folder=Path(raw);self.save(folder,identity)
            identity['players'][0]['FlexProjection']=11
            cache=self.replay(folder,identity)
            self.assertEqual(cache.comparison['changed_components'],['player forecasts'])
            calls=[];cache.frame(0,lambda:(calls.append(1) or {'1':11.0},[6.0]),Counter())
            report=cache.finish(1)
            self.assertEqual(calls,[1]);self.assertEqual(report['reused_scenarios'],0)
            self.assertEqual(report['comparison']['changed_components'],['player forecasts'])

    def test_receipts_cannot_authorize_missing_corrupt_or_partial_artifacts(self):
        identity=self.identity()
        with tempfile.TemporaryDirectory() as raw,patch('candidate_library.code_id',return_value='build'):
            folder=Path(raw);saved=self.save(folder,identity)
            target=folder/(saved['cache_id']+'.sqlite');target.unlink()
            cache=self.replay(folder,identity)
            self.assertEqual(cache.status,'recording');self.assertEqual(cache.hits,0)
            cache.finish(0)
            self.assertFalse(target.exists())
            self.save(folder,identity)
            with sqlite3.connect(target) as con:con.execute("UPDATE frames SET payload=?",(b'bad',))
            con.close()
            cache=self.replay(folder,identity)
            self.assertEqual(cache.status,'recording');self.assertIn('integrity',cache.reason)
            cache.finish(0)

    def test_malformed_receipts_do_not_prevent_exact_reuse(self):
        identity=self.identity()
        with tempfile.TemporaryDirectory() as raw,patch('candidate_library.code_id',return_value='build'):
            folder=Path(raw);self.save(folder,identity)
            (folder/'diagnostics.db').write_bytes(b'not a database')
            cache=self.replay(folder,identity)
            self.assertEqual(cache.status,'reused');cache.finish(1)
            identity['seed']=8
            cache=self.replay(folder,identity)
            self.assertEqual(cache.status,'recording');cache.finish(0)

    def test_receipts_are_bounded_and_other_slate_receipts_are_not_compared(self):
        parts=components(self.identity())
        with tempfile.TemporaryDirectory() as raw:
            folder=Path(raw)
            for i in range(LIMIT+3):record(folder,'slate',f'{i:064x}',parts)
            with sqlite3.connect(folder/'diagnostics.db') as con:
                self.assertEqual(con.execute('SELECT count(*) FROM receipts').fetchone()[0],LIMIT)
            con.close()
            self.assertEqual(compare(folder,'other-slate',parts)['status'],'No comparable receipt recorded')

    def test_cancelled_comparison_and_oversized_store_do_not_read_cache_frames(self):
        parts=components(self.identity())
        with tempfile.TemporaryDirectory() as raw:
            folder=Path(raw);record(folder,'slate','a'*64,parts)
            self.assertEqual(compare(folder,'slate',parts,lambda:True)['status'],'No comparable receipt recorded')
            with patch('scenario_cache_diagnostics.MAX_BYTES',1):
                self.assertEqual(compare(folder,'slate',parts)['status'],'Diagnostic comparison unavailable')
                record(folder,'slate','b'*64,parts)
