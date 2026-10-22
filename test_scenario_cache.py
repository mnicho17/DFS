import copy
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nfl_simulation import simulate_nfl_contest
from showdown_simulation import simulate_showdown
from optimizers import ShowdownOptimizer, MultiSportClassicOptimizer
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


def result_values(result):
    return [(row.sim_metrics,row.sim_top_hits,row.sim_top_five_hits,row.sim_win_hits,row.sim_scenario_values)
            for row in result['lineups']]


class ScenarioCacheTests(unittest.TestCase):
    def fixtures(self):
        for kind,fixture,opt,sim,module in (
            ('classic',_fixture_players,MultiSportClassicOptimizer,simulate_nfl_contest,'nfl_simulation'),
            ('showdown',_showdown_players,ShowdownOptimizer,simulate_showdown,'showdown_simulation')):
            players=fixture();rows=opt(players).build_lineups(4)
            yield kind,players,rows,sim,module

    def test_exact_replay_matches_live_scoring_in_both_formats(self):
        for kind,players,rows,sim,module in self.fixtures():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
                original=copy.deepcopy(players)
                opts=dict(scenarios=30,field_lineup_count=30,seed=13)
                live=sim(rows,players,**opts)
                cold=sim(rows,players,scenario_cache=True,**opts)
                self.assertEqual(cold['report']['scenario_cache']['status'],'saved')
                with patch(module+'._scenario_outcomes',side_effect=AssertionError('must reuse outcomes')):
                    warm=sim(rows,players,scenario_cache=True,**opts)
                self.assertEqual(result_values(live),result_values(cold))
                self.assertEqual(result_values(live),result_values(warm))
                self.assertEqual(warm['report']['game_script_mix'],live['report']['game_script_mix'])
                self.assertEqual(warm['report']['scenario_cache']['reused_scenarios'],30)
                self.assertEqual(players,original)
                # A different seed is independent, and refreshes of player inputs cannot hit.
                fresh=sim(rows,players,scenario_cache=True,**dict(opts,seed=14))
                self.assertEqual(fresh['report']['scenario_cache']['reused_scenarios'],0)
                changed=copy.deepcopy(players);changed[0]['Own']=91.123
                changed_rows=copy.deepcopy(rows)
                fresh=sim(changed_rows,changed,scenario_cache=True,**opts)
                self.assertEqual(fresh['report']['scenario_cache']['reused_scenarios'],0)

    def test_corrupt_and_partial_cache_recomputes_without_changing_results(self):
        players=_showdown_players();rows=ShowdownOptimizer(players).build_lineups(3)
        opts=dict(scenarios=12,field_lineup_count=20,seed=77)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            first=simulate_showdown(rows,players,scenario_cache=True,**opts)
            path=next((Path(folder)/'scenario-cache').glob('*.sqlite'))
            with sqlite3.connect(path) as con:con.execute("UPDATE frames SET payload=? WHERE id=2",(b'corrupt',))
            con.close()
            second=simulate_showdown(rows,players,scenario_cache=True,**opts)
            self.assertEqual(result_values(first),result_values(second))
            self.assertEqual(second['report']['scenario_cache']['reused_scenarios'],0)
            # Only one scenario completes before cancellation: no partial cache is published.
            import showdown_simulation
            real=showdown_simulation._scenario_outcomes;calls=[]
            def draw(*args,**kwargs):calls.append(1);return real(*args,**kwargs)
            with patch('showdown_simulation._scenario_outcomes',side_effect=draw):
                partial=simulate_showdown(rows,players,scenario_cache=True,**dict(opts,seed=78),cancel_callback=lambda:bool(calls))
            self.assertEqual(partial['report']['scenarios'],1)
            self.assertEqual(len(list((Path(folder)/'scenario-cache').glob('*.sqlite'))),1)
            self.assertFalse(list((Path(folder)/'scenario-cache').glob('*.partial')))

    def test_model_change_storage_failure_and_transforms(self):
        players=_showdown_players();rows=ShowdownOptimizer(players).build_lineups(3)
        opts=dict(scenarios=8,field_lineup_count=20,seed=23)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            baseline=simulate_showdown(rows,players,**opts)
            for version in ('one','two'):
                with patch('candidate_library.code_id',return_value=version):
                    run=simulate_showdown(rows,players,scenario_cache=True,**opts)
                    self.assertEqual(run['report']['scenario_cache']['status'],'saved')
            with patch('scenario_cache.STORE_LIMIT',0):
                run=simulate_showdown(rows,players,scenario_cache=True,**opts)
                self.assertEqual(result_values(baseline),result_values(run))
                self.assertEqual(run['report']['scenario_cache']['status'],'not saved')
            with patch('scenario_cache.cache_folder',side_effect=OSError('disk unavailable')):
                run=simulate_showdown(rows,players,scenario_cache=True,**opts)
                self.assertEqual(result_values(baseline),result_values(run))
            run=simulate_showdown(rows,players,scenario_cache=True,outcome_transform=lambda v:v,**opts)
            self.assertEqual(run['report']['scenario_cache']['status'],'disabled')

    def test_compressed_scores_are_bit_exact_and_fit_below_uncompressed_storage_limit(self):
        from scenario_cache import ScenarioReplay
        from nfl_simulation import player_key
        from array import array
        players=_showdown_players();keys=[player_key(p) for p in players]
        ranked=sorted([float(i)/7 for i in range(2000)]*5)
        outcomes={key:float(i) for i,key in enumerate(keys)}
        scripts={'neutral':1}
        with tempfile.TemporaryDirectory() as folder, patch('scenario_cache.FILE_LIMIT',300000):
            replay=ScenarioReplay(players,[[0]*10000],kind='showdown',seed=12,count=10,enabled=True,folder=folder)
            for index in range(10):replay.frame(index,lambda:(outcomes,ranked),scripts)
            report=replay.finish(10)
            self.assertEqual(report['status'],'saved')
            self.assertLess(report['stored_bytes'],300000)
            self.assertGreater(10*10000*8,300000)
            warm=ScenarioReplay(players,[[0]*10000],kind='showdown',seed=12,count=10,enabled=True,folder=folder)
            for index in range(10):
                restored, scores=warm.frame(index,lambda:self.fail('must replay'),scripts)
                self.assertEqual(restored,outcomes)
                self.assertEqual(scores.tobytes(),array('d',ranked).tobytes())
            hit=warm.finish(10)
            self.assertEqual(hit['reused_scenarios'],10)
            self.assertGreaterEqual(hit['lookup_seconds'],0)
            self.assertGreaterEqual(hit['io_seconds'],0)

    def test_bad_compressed_scores_and_legacy_codec_are_recomputed(self):
        import zlib
        players=_showdown_players();rows=ShowdownOptimizer(players).build_lineups(2)
        opts=dict(scenarios=5,field_lineup_count=20,seed=45,scenario_cache=True)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            expected=simulate_showdown(rows,players,**opts)
            path=next((Path(folder)/'scenario-cache').glob('*.sqlite'))
            for blob in (b'not compressed',zlib.compress(b'x'*100000),zlib.compress(b'12345678')+b'trailing'):
                con=sqlite3.connect(path)
                try:
                    with con:con.execute('UPDATE frames SET scores=? WHERE id=0',(blob,))
                finally:con.close()
                actual=simulate_showdown(rows,players,**opts)
                self.assertEqual(result_values(actual),result_values(expected))
                self.assertEqual(actual['report']['scenario_cache']['reused_scenarios'],0)
                self.assertIn('verification',actual['report']['scenario_cache']['reason'])
            con=sqlite3.connect(path)
            try:
                with con:con.execute("UPDATE meta SET value='legacy-raw-double' WHERE key='scores_codec'")
            finally:con.close()
            actual=simulate_showdown(rows,players,**opts)
            self.assertEqual(result_values(actual),result_values(expected))
            self.assertEqual(actual['report']['scenario_cache']['reused_scenarios'],0)

    def test_file_limit_still_falls_back_and_removes_incomplete_artifact(self):
        players=_showdown_players();rows=ShowdownOptimizer(players).build_lineups(2)
        opts=dict(scenarios=5,field_lineup_count=20,seed=53)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            expected=simulate_showdown(rows,players,**opts)
            with patch('scenario_cache.FILE_LIMIT',100):
                actual=simulate_showdown(rows,players,scenario_cache=True,**opts)
            self.assertEqual(result_values(actual),result_values(expected))
            self.assertEqual(actual['report']['scenario_cache']['status'],'not saved')
            self.assertIn('storage limit',actual['report']['scenario_cache']['reason'])
            self.assertFalse(list((Path(folder)/'scenario-cache').glob('*.partial')))
            self.assertFalse(list((Path(folder)/'scenario-cache').glob('*.sqlite')))

    def test_compressed_frame_validation_rejects_nonfinite_unsorted_and_wrong_coverage(self):
        from scenario_cache import ScenarioReplay
        from nfl_simulation import player_key
        from array import array
        import zlib,json
        players=_showdown_players();outcomes={player_key(p):1 for p in players}
        with tempfile.TemporaryDirectory() as folder:
            replay=ScenarioReplay(players,[[1,2]],kind='showdown',seed=2,count=1,enabled=True,folder=folder)
            payload=zlib.compress(json.dumps([outcomes,{}]).encode())
            for scores in ([2.,1.],[float('nan'),2.],[1.,float('inf')],[1.]):
                with self.assertRaises(ValueError):replay._decode(payload,zlib.compress(array('d',scores).tobytes()))
            with self.assertRaises(ValueError):
                replay._decode(zlib.compress(json.dumps([{},{}]).encode()),zlib.compress(array('d',[1.,2.]).tobytes()))
            replay.close()

    def test_all_exact_context_field_count_and_runtime_gates_remain_strict(self):
        from scenario_cache import ScenarioReplay
        from nfl_simulation import player_key
        players=_showdown_players();outcomes={player_key(p):1 for p in players}
        options=dict(kind='showdown',seed=9,count=1,enabled=True,context={'checked_at':'first'})
        with tempfile.TemporaryDirectory() as folder:
            cache=ScenarioReplay(players,[[1,2]],folder=folder,**options)
            cache.frame(0,lambda:(outcomes,[1.,2.]),{});cache.finish(1)
            variants=[dict(context={'checked_at':'later'}),dict(count=2),dict(seed=10),dict(kind='classic')]
            for change in variants:
                cache=ScenarioReplay(players,[[1,2]],folder=folder,**dict(options,**change))
                self.assertEqual(cache.status,'recording');cache.close()
            cache=ScenarioReplay(players,[[1,3]],folder=folder,**options)
            self.assertEqual(cache.status,'recording');cache.close()
            with patch('scenario_cache.sys.byteorder','different'):
                cache=ScenarioReplay(players,[[1,2]],folder=folder,**options)
                self.assertEqual(cache.status,'recording');cache.close()

    def test_preparation_passes_frozen_settings_and_respects_pause(self):
        from build_snapshots import create_snapshot
        from scenario_preparation import prepare_scenarios
        from PyQt5.QtCore import QObject,pyqtSignal
        snapshot=create_snapshot(_showdown_players(),dict(sport='NFL',contest_kind='showdown',requested_lineups=20),{})
        captured=[]
        class Worker(QObject):
            finished=pyqtSignal(object);error=pyqtSignal(str);progress=pyqtSignal(int,int,str)
            def __init__(self,players,**kwargs):super().__init__();captured.append(kwargs)
            def run(self):self.finished.emit({})
        with patch('main_window.LineupBuildWorker',Worker):
            text=prepare_scenarios('example.dfslib',snapshot,seconds=120)
            self.assertIn('finished',text)
            self.assertTrue(captured[0]['scenario_cache'])
            self.assertEqual(captured[0]['num_lineups'],20)
            self.assertEqual(captured[0]['candidate_library'],'example.dfslib')
            prepare_scenarios('example.dfslib',snapshot,seconds=120,cancelled=lambda:True)
            self.assertEqual(len(captured),1)


if __name__=='__main__':unittest.main()
