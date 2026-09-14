import copy
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, Mock
from build_archives import save_build_archive, archive_folder
from build_snapshots import create_snapshot, save_snapshot
from nfl_simulation import SimLineup
from optimizers import ShowdownLineup
from test_showdown_performance import _showdown_players


class BuildArchiveTests(unittest.TestCase):
    def test_all_450_outputs_snapshot_rank_and_integrity_without_export(self):
        players=_showdown_players();six=players[:3]+players[18:21]
        rows=[]
        for i in range(450):
            lu=ShowdownLineup(six[0],six[1:]);lu.sim_metrics={'sim_scenarios':100,'sim_top_one_pct':i/100,'sim_mean':90}
            rows.append(lu)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':tmp}),patch('build_archives.implementation_id',return_value='code'):
            snap=create_snapshot(players,{'sport':'NFL','contest_kind':'showdown'},{})
            folder=archive_folder().parent/'snapshots';folder.mkdir()
            save_snapshot(str(folder/(snap['input_id']+'.json')),snap)
            context={'kind':'showdown','sport':'NFL','input_id':snap['input_id'],'settings':{},'portfolio_rules':{}}
            payload={'kind':'showdown','lineups':rows}
            saved=save_build_archive(payload,context,{})
            with zipfile.ZipFile(archive_folder()/saved['filename']) as z:
                data=json.loads(z.read('lineups.json'));manifest=json.loads(z.read('manifest.json'))
                self.assertEqual(len(data['lineups']),450)
                self.assertEqual(data['lineups'][0]['sim_metrics']['sim_top_one_pct'],4.49)
                self.assertEqual(data['metadata']['submission_status'],'not established')
                self.assertEqual(data['metadata']['snapshot_status'],'included')
                for name,sha in manifest['sha256'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),sha)
            self.assertEqual(rows[0].sim_metrics['sim_top_one_pct'],0)
            other=save_build_archive(payload,context,{})
            self.assertNotEqual(saved['filename'],other['filename'])
            self.assertFalse(list(Path(tmp).rglob('*.sqlite')))

    def test_classic_cancelled_and_missing_snapshot_are_explicit(self):
        from test_classic_performance import _fixture_players
        from optimizers import MultiSportClassicOptimizer
        rows=MultiSportClassicOptimizer(_fixture_players()).build_lineups(1)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':tmp}),patch('build_archives.implementation_id',return_value='code'):
            result=save_build_archive({'lineups':rows,'cancelled':True},{'kind':'classic','sport':'NFL'}, {})
            with zipfile.ZipFile(archive_folder()/result['filename']) as z:
                data=json.loads(z.read('lineups.json'))
                self.assertEqual(data['metadata']['build_status'],'cancelled')
                self.assertEqual(data['metadata']['snapshot_status'],'unavailable')
                self.assertEqual(len(data['lineups'][0]['slots']),9)

    def test_failure_removes_partial_archive(self):
        ps=_showdown_players();lu=ShowdownLineup(ps[0],ps[1:3]+ps[18:21])
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':tmp}),patch('build_archives.implementation_id',return_value='code'),patch('build_archives.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):save_build_archive({'kind':'showdown','lineups':[lu]}, {}, {})
            self.assertFalse(list(archive_folder().iterdir()))

    def test_ui_archive_failure_does_not_prevent_diagnostic_save(self):
        from main_window import MainWindow
        window=Mock();window._active_build_context={'kind':'classic','sport':'NFL','settings':{}}
        with patch('build_archives.save_build_archive',side_effect=OSError('disk full')),patch('main_window.save_build_diagnostic',side_effect=lambda d:d) as save:
            MainWindow._record_build_diagnostic(window, {'lineups':[]}, displayed_count=0)
        save.assert_called_once()
        self.assertEqual(window.last_build_diagnostic['generated_archive']['status'],'failed')
