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
