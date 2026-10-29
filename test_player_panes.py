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
