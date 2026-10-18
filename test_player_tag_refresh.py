from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from main_window import MainWindow, _pkey


class PlayerTagRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w = MainWindow()
        self.addCleanup(w.close)
        w.players = [dict(Name=name, ID=str(i), Team='SEA', Position='WR',
                         FlexSalary=5000, CptSalary=7500, FlexProjection=10,
                         CptProjection=15, InjuryStatus='Starter')
                     for i, name in enumerate(('Zulu', 'Alpha', 'Mike'))]
        w._refresh_players_table()
        w.tbl_players.setSortingEnabled(True)
        w.tbl_players.sortItems(0, QtCore.Qt.AscendingOrder)
        return w

    def test_sorted_fade_updates_only_target_and_preserves_cells_selection(self):
        w = self.window()
        table = w.tbl_players
        table.selectRow(0)
        selected = w._get_selected_player_rows()
        self.assertEqual(selected, [1])
        cells = [[table.item(r,c) for c in range(table.columnCount())]
                 for r in range(table.rowCount())]
        with patch.object(w, '_refresh_players_table', side_effect=AssertionError('full redraw')):
            w.apply_tags('fade')
        self.assertTrue(w.players[1]['FadeFlex'])
        self.assertTrue(w.players[1]['FadeCpt'])
        self.assertFalse(w.players[0].get('FadeFlex', False))
        self.assertEqual(w._get_selected_player_rows(), selected)
        for row, items in enumerate(cells):
            for col, item in enumerate(items):
                self.assertIs(table.item(row,col), item)
        self.assertEqual(table.item(0,14).text(), 'F CF')
        self.assertEqual(w.lbl_player_inspector_title.text(), 'Alpha')

    def test_multiselect_lock_and_clear_keep_other_player_tags(self):
        w = self.window()
        table = w.tbl_players
        table.selectRow(0)
        table.selectionModel().select(table.model().index(2,0),
            QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        w.apply_tags('cpt_lock')
        self.assertTrue(w.players[0]['LockCpt'])
        self.assertTrue(w.players[1]['LockCpt'])
        self.assertFalse(w.players[2].get('LockCpt', False))
        w.players[2]['FadeFlex'] = True
        w.clear_tags()
        self.assertFalse(w.players[0]['LockCpt'])
        self.assertFalse(w.players[1]['LockCpt'])
        self.assertTrue(w.players[2]['FadeFlex'])
        table.clearSelection()
        w.clear_tags()
        self.assertFalse(any(p.get('FadeFlex') for p in w.players))

    def test_tag_sort_keeps_stable_player_identity_and_validation_refresh(self):
        w = self.window()
        table = w.tbl_players
        table.selectRow(0)
        key = _pkey(w.players[w._get_selected_player_rows()[0]])
        table.sortItems(14, QtCore.Qt.AscendingOrder)
        with patch.object(w, '_update_readiness_badge') as readiness, \
                patch.object(w, '_update_lineup_space_dashboard') as space:
            w.apply_tags('lock')
        readiness.assert_called_once()
        space.assert_called_once()
        self.assertEqual([_pkey(w.players[i]) for i in w._get_selected_player_rows()], [key])
        self.assertTrue(table.isSortingEnabled())
        self.assertTrue(table.updatesEnabled())
        self.assertFalse(table.signalsBlocked())
