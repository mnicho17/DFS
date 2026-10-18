from test_environment import install
install()
import copy
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtGui, QtWidgets
from main_window import _pkey
import test_player_tag_refresh as tag_tests


class PlayerTableBatchTests(unittest.TestCase):
    setUpClass=classmethod(tag_tests.PlayerTagRefreshTests.setUpClass.__func__)
    window=tag_tests.PlayerTagRefreshTests.window

    def test_sorted_multiselection_refreshes_inspector_once_without_mutating_players(self):
        w=self.window();table=w.tbl_players
        table.selectRow(0)
        table.selectionModel().select(table.model().index(2,0),
            QtCore.QItemSelectionModel.Select|QtCore.QItemSelectionModel.Rows)
        selected={_pkey(w.players[i]) for i in w._get_selected_player_rows()}
        before=copy.deepcopy(w.players)
        notifications=[];table.itemSelectionChanged.connect(lambda:notifications.append(True))
        with patch.object(w,'_update_player_inspector',wraps=w._update_player_inspector) as inspector:
            w._refresh_players_table()
        self.assertEqual(notifications,[]);inspector.assert_called_once()
        self.assertEqual({_pkey(w.players[i]) for i in w._get_selected_player_rows()},selected)
        self.assertEqual(w.players,before)
        self.assertEqual([table.item(r,0).text() for r in range(table.rowCount())],['Alpha','Mike','Zulu'])
        self.assertEqual(w.lbl_player_inspector_title.text(),'2 players selected')
        self.assertTrue(table.isSortingEnabled());self.assertTrue(table.updatesEnabled())
        self.assertFalse(table.signalsBlocked());self.assertFalse(table.selectionModel().signalsBlocked())

    def test_failed_render_restores_qt_state_and_propagates_error(self):
        w=self.window();table=w.tbl_players
        def fail():
            self.assertFalse(table.updatesEnabled())
            self.assertTrue(table.signalsBlocked());self.assertTrue(table.selectionModel().signalsBlocked())
            table.setSortingEnabled(False)
            raise ValueError('injected render failure')
        with patch.object(w,'_populate_players_table',side_effect=fail):
            with self.assertRaisesRegex(ValueError,'injected'):
                w._refresh_players_table()
        self.assertTrue(table.updatesEnabled());self.assertTrue(table.isSortingEnabled())
        self.assertFalse(table.signalsBlocked());self.assertFalse(table.selectionModel().signalsBlocked())

    def test_nested_batch_keeps_preexisting_signal_and_redraw_state(self):
        w=self.window();table=w.tbl_players
        table.setUpdatesEnabled(False);table.blockSignals(True);table.selectionModel().blockSignals(True)
        table.setSortingEnabled(False)
        w._refresh_players_table()
        self.assertFalse(table.updatesEnabled());self.assertFalse(table.isSortingEnabled())
        self.assertTrue(table.signalsBlocked());self.assertTrue(table.selectionModel().signalsBlocked())
        table.blockSignals(False);table.selectionModel().blockSignals(False);table.setUpdatesEnabled(True)

    def test_row_style_precedence_colors_and_clearing_are_preserved(self):
        w=self.window();table=w.tbl_players;table.setSortingEnabled(False)
        normal=(232,234,237);pink=(255,205,205)
        cases=[({},None,normal,False),({'FadeCpt':True},(85,48,48),pink,False),
               ({'LockFlex':True},(35,85,55),normal,True),
               ({'LockCpt':True,'LockFlex':True,'FadeCpt':True},(38,70,110),normal,True),
               ({'FadeFlex':True,'LockCpt':True},(85,38,38),pink,True)]
        for flags,bg,fg,bold in cases:
            with self.subTest(flags=flags):
                w._set_player_row_style(0,flags,fresh=True)
                tags=table.item(0,14)
                if bg:self.assertEqual(tags.background().color().getRgb()[:3],bg)
                else:self.assertEqual(tags.background().style(),QtCore.Qt.NoBrush)
                self.assertEqual(tags.foreground().color().getRgb()[:3],fg)
                self.assertEqual(tags.font().bold(),bold)
                ordinary=table.item(0,4)
                self.assertEqual(ordinary.foreground().color().getRgb()[:3],(160,160,160) if flags.get('FadeFlex') else normal)
                self.assertFalse(ordinary.font().bold());self.assertFalse(ordinary.font().strikeOut())
        w._set_player_row_style(0,{})
        for col in range(table.columnCount()):
            item=table.item(0,col)
            self.assertEqual(item.background().style(),QtCore.Qt.NoBrush)
            self.assertEqual(item.foreground().color().getRgb()[:3],normal)
            self.assertFalse(item.font().bold())

    def test_inplace_tag_updates_preserve_individual_font_family_size_and_italic(self):
        w=self.window();table=w.tbl_players
        custom=QtGui.QFont('Consolas',13);custom.setItalic(True);custom.setStrikeOut(True);custom.setBold(True)
        table.item(0,4).setFont(custom)
        w._set_player_row_style(0,{'FadeFlex':True})
        font=table.item(0,4).font()
        self.assertEqual(font.family(),'Consolas');self.assertEqual(font.pointSize(),13)
        self.assertTrue(font.italic());self.assertFalse(font.strikeOut());self.assertFalse(font.bold())

    def test_empty_slate_clears_view_and_inspector(self):
        w=self.window();w.tbl_players.selectRow(0);w.players=[]
        w._refresh_players_table()
        self.assertEqual(w.tbl_players.rowCount(),0)
        self.assertEqual(w.lbl_player_inspector_title.text(),'Select a player')
        self.assertTrue(w.tbl_players.updatesEnabled());self.assertTrue(w.tbl_players.isSortingEnabled())
