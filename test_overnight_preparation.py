import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from build_snapshots import create_snapshot
from candidate_library import run_search, metadata, roster_keys
from optimizers import ShowdownOptimizer, MultiSportClassicOptimizer
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


class OvernightPreparationTests(unittest.TestCase):
    def test_target_resume_and_exclusions_in_both_formats(self):
        for kind, players, optimizer in (
            ('classic', _fixture_players(), MultiSportClassicOptimizer),
            ('showdown', _showdown_players(), ShowdownOptimizer),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'library.dfslib'
                snapshot=create_snapshot(players, dict(sport='NFL',contest_kind=kind,salary_strategy='Flexible'), {})
                original=copy.deepcopy(snapshot)
                rows=optimizer(players).build_lineups(4)
                self.assertGreaterEqual(len(rows), 3)
                calls=[]
                argument='excluded_signatures' if kind=='showdown' else 'exact_excluded_signatures'
                def batch(**kwargs):
                    calls.append(set(kwargs[argument]))
                    # An overproducing provider still cannot exceed the total target.
                    return rows
                with patch.object(optimizer,'build_lineups',side_effect=batch):
                    self.assertEqual(run_search(path,snapshot,candidate_limit=2),2)
                    self.assertEqual(run_search(path,snapshot,candidate_limit=2),2)
                    self.assertEqual(len(calls),1)
                    self.assertEqual(run_search(path,snapshot,candidate_limit=3),3)
                self.assertEqual(len(calls[1]),2)
                keys=roster_keys(rows[0],kind)
                signature=(keys[0],tuple(keys[1:])) if kind=='showdown' else tuple(keys)
                self.assertIn(signature,calls[1])
                self.assertEqual(metadata(path)['batches'],2)
                self.assertEqual(snapshot,original)

    def test_repeated_candidates_stop_without_erasing_checkpoints(self):
        players=_showdown_players();rows=ShowdownOptimizer(players).build_lineups(2)
        snapshot=create_snapshot(players,dict(sport='NFL',contest_kind='showdown'),{})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'library.dfslib';messages=[]
            with patch.object(ShowdownOptimizer,'build_lineups',return_value=rows) as build:
                self.assertEqual(run_search(path,snapshot,candidate_limit=100,progress=messages.append),len(rows))
                self.assertEqual(build.call_count,6)
            self.assertEqual(metadata(path)['count'],len(rows))
            self.assertTrue(any('does not prove' in message for message in messages))

    def test_invalid_limits_do_not_create_library(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'library.dfslib'
            for options in ({'candidate_limit':0},{'candidate_limit':100001},{'candidate_limit':True},
                            {'seconds':float('nan')},{'seconds':float('inf')},{'seconds':43201},
                            {'batch_size':0}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    run_search(path,{},**options)
                self.assertFalse(path.exists())

    def test_dialog_and_worker_pass_selected_target(self):
        from PyQt5 import QtWidgets
        from long_search_ui import LongSearchDialog, SearchWorker
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        dialog=LongSearchDialog(None)
        self.assertEqual(dialog.target.currentData(),20000)
        self.assertIn(dialog.target,dialog.controls)
        with patch('long_search_ui.run_search',return_value=12000) as search:
            worker=SearchWorker('example.dfslib',{},3600,12000)
            worker.run()
            self.assertEqual(search.call_args.kwargs['candidate_limit'],12000)
        dialog.close()


if __name__=='__main__':unittest.main()
