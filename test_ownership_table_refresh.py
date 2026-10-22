from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
from main_window import _pkey
from nfl_pipeline import apply_ownership
import test_player_tag_refresh as tag_tests


class OwnershipTableRefreshTests(unittest.TestCase):
    setUpClass = classmethod(tag_tests.PlayerTagRefreshTests.setUpClass.__func__)
    window = tag_tests.PlayerTagRefreshTests.window

    def result(self, w):
        keys = [_pkey(p) for p in w.players]
        return dict(total=dict(zip(keys, [71.25, 8.5, 40.0])),
                    cpt=dict(zip(keys, [20.0, 3.0, 10.0])),
                    flex=dict(zip(keys, [51.25, 5.5, 30.0])),
                    meta=dict(model='test model', valid_lineups=56,
                              role_pool_size=3, eligible_keys=keys[:2]))

    def test_result_matches_existing_application_preserves_cells_and_multiselection(self):
        w = self.window(); table = w.tbl_players
        table.selectRow(0)
        table.selectionModel().select(table.model().index(2, 0),
            QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        selected = {_pkey(w.players[i]) for i in w._get_selected_player_rows()}
        cells = {str(table.item(row, 0).data(QtCore.Qt.UserRole)):
                 [table.item(row, col) for col in range(table.columnCount())]
                 for row in range(table.rowCount())}
        expected = copy.deepcopy(w.players); result = self.result(w)
        apply_ownership(expected, result)
        notifications = []; table.itemSelectionChanged.connect(lambda: notifications.append(True))
        with patch.object(w, '_refresh_players_table', side_effect=AssertionError('full redraw')), \
                patch.object(w, '_update_player_inspector', wraps=w._update_player_inspector) as inspector, \
                patch.object(w, '_update_readiness_badge') as readiness, \
                patch.object(w, '_update_lineup_space_dashboard') as space:
            w._on_own_sim_finished(result)
        self.assertEqual(w.players, expected)
        self.assertEqual(notifications, []); inspector.assert_called_once()
        readiness.assert_called_once(); space.assert_called_once()
        self.assertEqual({_pkey(w.players[i]) for i in w._get_selected_player_rows()}, selected)
        for row in range(table.rowCount()):
            key = str(table.item(row, 0).data(QtCore.Qt.UserRole))
            for col, cell in enumerate(cells[key]):
                self.assertIs(table.item(row, col), cell)
            self.assertEqual(table.item(row, 15).text(), f"{result['total'][key]:.1f}%")
            self.assertEqual(table.item(row, 15).data(QtCore.Qt.UserRole), result['total'][key])
        self.assertIn('Lineup simulation', w.lbl_ownership_source.text())
        self.assertIn('Lineup simulation', w.ownership_status_label.text())
        self.assertTrue(w.ownership_retry_button.isEnabled())
        self.assertTrue(w.ownership_manual_panel.isHidden())
        self.assertEqual(w.lbl_player_inspector_title.text(), '2 players selected')

    def test_numeric_ownership_sort_keeps_selected_identity(self):
        w = self.window(); table = w.tbl_players; table.selectRow(0)
        selected = {_pkey(w.players[i]) for i in w._get_selected_player_rows()}
        table.sortItems(15, QtCore.Qt.DescendingOrder)
        w._on_own_sim_finished(self.result(w))
        self.assertEqual([table.item(r, 15).data(QtCore.Qt.UserRole) for r in range(3)], [71.25, 40.0, 8.5])
        self.assertEqual({_pkey(w.players[i]) for i in w._get_selected_player_rows()}, selected)
        self.assertTrue(table.isSortingEnabled()); self.assertTrue(table.updatesEnabled())
        self.assertFalse(table.signalsBlocked()); self.assertFalse(table.selectionModel().signalsBlocked())

    def test_mismatched_or_incomplete_rows_fall_back_before_mutating_cells(self):
        for defect in ('key', 'duplicate', 'missing cell', 'row count', 'duplicate source'):
            with self.subTest(defect=defect):
                w = self.window(); table = w.tbl_players; table.setSortingEnabled(False)
                if defect == 'key': table.item(0, 0).setData(QtCore.Qt.UserRole, 'wrong')
                elif defect == 'duplicate': table.item(1, 0).setData(QtCore.Qt.UserRole, table.item(0, 0).data(QtCore.Qt.UserRole))
                elif defect == 'missing cell': table.takeItem(1, 15)
                elif defect == 'row count': table.setRowCount(2)
                else: w.players[1]['Name'] = w.players[0]['Name']
                before = table.item(0, 15).text()
                with patch.object(w, '_refresh_players_table') as full:
                    w.players[0]['ProjOwnPct'] = 99
                    w._refresh_player_ownership()
                full.assert_called_once(); self.assertEqual(table.item(0, 15).text(), before)

    def test_nested_disabled_qt_state_is_retained(self):
        w = self.window(); table = w.tbl_players
        table.setSortingEnabled(False); table.setUpdatesEnabled(False)
        table.blockSignals(True); table.selectionModel().blockSignals(True)
        w._on_own_sim_finished(self.result(w))
        self.assertFalse(table.isSortingEnabled()); self.assertFalse(table.updatesEnabled())
        self.assertTrue(table.signalsBlocked()); self.assertTrue(table.selectionModel().signalsBlocked())
        table.blockSignals(False); table.selectionModel().blockSignals(False)

    def test_cell_update_failure_restores_qt_state(self):
        w = self.window(); table = w.tbl_players
        with patch.object(table.item(0, 15), 'setText', side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                w._refresh_player_ownership()
        self.assertTrue(table.isSortingEnabled()); self.assertTrue(table.updatesEnabled())
        self.assertFalse(table.signalsBlocked()); self.assertFalse(table.selectionModel().signalsBlocked())

    def test_empty_result_preserves_existing_zero_ownership_semantics(self):
        w = self.window(); w._on_own_sim_finished(self.result(w)); w._on_own_sim_finished({})
        for p in w.players:
            self.assertEqual(p['ProjOwnPct'], 0); self.assertEqual(p['ProjCptOwnPct'], 0)
            self.assertEqual(p['ProjFlexOwnPct'], 0); self.assertNotIn('OwnershipModel', p)
        self.assertTrue(all(w.tbl_players.item(r, 15).text() == '0.0%' for r in range(3)))


if __name__ == '__main__':
    unittest.main()
