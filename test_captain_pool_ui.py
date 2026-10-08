from test_environment import install
install()
import copy
import tempfile
import unittest
from pathlib import Path
from PyQt5 import QtCore, QtWidgets
from captain_pool_ui import CaptainPoolDialog
from showdown_library import prepare, iter_candidates
from test_showdown_library import players
from portfolio_rules import player_key
from unittest.mock import patch


class CaptainPoolUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_cancel_does_not_change_inputs_and_empty_pool_cannot_save(self):
        rows = players(); before = copy.deepcopy(rows)
        dialog = CaptainPoolDialog(rows)
        dialog.set_all(False)
        self.assertFalse(dialog.save.isEnabled())
        dialog.accept(); self.assertEqual(rows, before)
        dialog.reject(); self.assertEqual(rows, before)

    def test_selected_pool_filters_library_without_changing_flex_or_limits(self):
        rows = players(); before = copy.deepcopy(rows)
        dialog = CaptainPoolDialog(rows); dialog.set_all(False)
        key = player_key(rows[0])
        dialog.rows[key][1].setCheckState(QtCore.Qt.Checked); dialog.accept()
        for old, new in zip(before, rows):
            self.assertEqual({k:v for k,v in old.items() if k != 'FadeCpt'},
                             {k:v for k,v in new.items() if k != 'FadeCpt'})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'library.sdlib'; prepare(path, rows)
            candidates = list(iter_candidates(path, rows))
        self.assertTrue(candidates)
        self.assertEqual({player_key(r['Captain']) for r in candidates}, {key})
        self.assertTrue(any(player_key(p) != key for r in candidates for p in r['Flex']))

    def test_locks_are_preserved_and_flex_locked_players_cannot_be_enabled(self):
        rows = players(); rows[0]['LockCpt'] = True; rows[1]['LockFlex'] = True
        dialog = CaptainPoolDialog(rows); dialog.set_all(False); dialog.accept()
        self.assertTrue(rows[0]['LockCpt']); self.assertFalse(rows[0]['FadeCpt'])
        self.assertFalse(dialog.rows[player_key(rows[1])][2])
        self.assertTrue(rows[1]['LockFlex'])
        self.assertTrue(dialog.save.isEnabled())

    def test_reopen_preserves_pool_and_all_eligible_restores_only_captain_fades(self):
        rows = players(); rows[0]['FadeCpt'] = True
        dialog = CaptainPoolDialog(rows)
        self.assertEqual(dialog.rows[player_key(rows[0])][1].checkState(), QtCore.Qt.Unchecked)
        dialog.set_all(True); dialog.accept()
        self.assertFalse(any(p['FadeCpt'] for p in rows))

    def test_full_stream_skips_excluded_captain_partitions_before_opening(self):
        from showdown_full_library import prepare as prepare_full, iter_candidates as stream
        rows = players()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'library.sdfull'; prepare_full(path, rows)
            selected = player_key(rows[0])
            for player in rows:
                player['FadeCpt'] = player_key(player) != selected
            with patch('showdown_library.iter_candidates', return_value=iter(())) as read:
                list(stream(path, rows))
            self.assertEqual(read.call_count, 1)
            self.assertEqual(read.call_args.kwargs['captain_key'], selected)
