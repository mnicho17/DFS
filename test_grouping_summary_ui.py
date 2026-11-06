from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
from PyQt5 import QtCore,QtWidgets
from grouping_summary_ui import inspect_groups,refresh,review
from showdown_common_groups import apply
from test_showdown_library import players
from main_window import MainWindow


class GroupingSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def data(self):
        pool=players();pool[4]['Position']='DST'
        groups=apply(pool,[],{'captain_dst'})
        bad={'Captain':pool[0],'Flex':[pool[i] for i in (1,2,4,5,6)]}
        good={'Captain':pool[5],'Flex':[pool[i] for i in (0,1,2,4,6)]}
        return pool,groups,bad,good

    def window(self):
        w=MainWindow();self.addCleanup(w.close)
        pool,groups,bad,good=self.data()
        w.players=pool;w.portfolio_groups=groups;w.saved_showdown=[bad,good]
        w.tabs_lineups.setCurrentIndex(0)
        return w

    def test_summary_collapses_generated_pairs_and_counts_each_saved_lineup_once(self):
        pool,groups,bad,good=self.data();before=copy.deepcopy((groups,bad,good))
        titles,affected=inspect_groups(groups,[bad,good],'showdown')
        self.assertEqual(titles,['Block Captain + opposing DST'])
        self.assertEqual([index for index,_ in affected],[1])
        self.assertEqual((groups,bad,good),before)
        duplicate=groups+[dict(groups[0],label='Another matching rule')]
        self.assertEqual(len(inspect_groups(duplicate,[bad],'showdown')[1]),1)

    def test_main_summary_saved_refresh_clear_and_mode_switch(self):
        w=self.window();w._refresh_saved_tables()
        self.assertIn('1 of 2',w.lbl_active_groups.text())
        self.assertFalse(w.btn_review_groups.isHidden())
        w.saved_showdown=w.saved_showdown[1:];w._refresh_saved_tables()
        self.assertIn('0 of 1',w.lbl_active_groups.text())
        self.assertTrue(w.btn_review_groups.isHidden())
        w.tabs_lineups.setCurrentIndex(1);refresh(w)
        self.assertNotIn('Saved showdown',w.lbl_active_groups.text())
        w.clear_portfolio_groups()
        self.assertEqual(w.lbl_active_groups.text(),'Active grouping rules: None')

    def test_review_lists_saved_index_and_group_without_mutating(self):
        w=self.window();before=copy.deepcopy((w.saved_showdown,w.portfolio_groups))
        seen=[]
        def inspect(dialog):
            editor=dialog.findChild(QtWidgets.QPlainTextEdit,'affectedGroupingReport')
            self.assertTrue(editor.isReadOnly())
            seen.append(editor.toPlainText())
            return QtWidgets.QDialog.Rejected
        with patch.object(QtWidgets.QDialog,'exec_',inspect):review(w)
        self.assertIn('Saved lineup 1',seen[0])
        self.assertNotIn('Saved lineup 2',seen[0])
        self.assertIn('Block Captain + opposing DST',seen[0])
        self.assertEqual((w.saved_showdown,w.portfolio_groups),before)

    def test_custom_groups_and_plain_text_are_preserved(self):
        custom={'type':'at_least_one','player_keys':['missing'],'label':'<b>Custom choice</b>'}
        _,_,bad,_=self.data()
        titles,affected=inspect_groups([custom],[bad],'showdown')
        self.assertEqual(titles,['<b>Custom choice</b>'])
        self.assertEqual(affected,[(1,['<b>Custom choice</b>'])])
        w=self.window();w.portfolio_groups=[custom];refresh(w)
        self.assertEqual(w.lbl_active_groups.textFormat(),QtCore.Qt.PlainText)
