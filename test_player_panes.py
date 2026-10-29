from test_environment import install
install()
import unittest
from PyQt5 import QtCore, QtWidgets, QtTest
from main_window import MainWindow


class PlayerPaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_player_inspector_can_expand_shrink_and_reset(self):
        window=MainWindow();self.addCleanup(window.close)
        window.resize(1600,950);window.show();self.app.processEvents()
        panes=window.player_panes
        scroll=window.findChild(QtWidgets.QScrollArea,'playerInspectorScroll')
        self.assertGreater(scroll.maximumWidth(),1000)
        panes.setSizes([600,600]);self.app.processEvents()
        wide=panes.sizes()[1]
        self.assertGreater(wide,400)
        panes.setSizes([1000,240]);self.app.processEvents()
        self.assertLess(panes.sizes()[1],wide)
        self.assertGreaterEqual(panes.sizes()[1],220)
        QtTest.QTest.mouseDClick(panes.handle(1),QtCore.Qt.LeftButton)
        self.app.processEvents()
        self.assertLessEqual(panes.sizes()[1],360)
        self.assertGreater(panes.sizes()[0],0)
        action=window.findChild(QtWidgets.QAction,'resetPlayerPaneWidthsAction')
        self.assertIsNotNone(action)
        panes.setSizes([500,700]);action.trigger();self.app.processEvents()
        self.assertLessEqual(panes.sizes()[1],360)
        self.assertFalse(panes.childrenCollapsible())

    def test_results_height_saved_widths_and_columns_are_adjustable(self):
        window=MainWindow();self.addCleanup(window.close)
        window.resize(3600,1100);window.show()
        window.action_show_saved_portfolio.setChecked(True)
        self.app.processEvents()
        panes=window.results_splitter
        panes.setSizes([150,600]);self.app.processEvents()
        result_height=panes.sizes()[1]
        panes.setSizes([600,180]);self.app.processEvents()
        self.assertLess(panes.sizes()[1],result_height)
        self.assertFalse(panes.childrenCollapsible())
        root=window.workspace_splitter
        root.setSizes([2200,1000]);self.app.processEvents()
        wide=root.sizes()[1]
        root.setSizes([2800,300]);self.app.processEvents()
        self.assertLess(root.sizes()[1],wide)
        for table in (window.tbl_saved_sd,window.tbl_saved_cl):
            self.assertEqual(table.horizontalHeader().sectionResizeMode(0),QtWidgets.QHeaderView.Interactive)
            table.setColumnWidth(0,260)
            self.assertEqual(table.columnWidth(0),260)
        action=window.findChild(QtWidgets.QAction,'resetResultsLayoutAction')
        action.trigger();self.app.processEvents()
        self.assertEqual(window.tbl_saved_sd.columnWidth(0),150)
        self.assertGreater(panes.sizes()[0],0)
        self.assertGreater(panes.sizes()[1],0)
