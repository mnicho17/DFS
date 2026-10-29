"""Resizable player workspace with an easily restored starting layout."""
from PyQt5 import QtCore, QtWidgets


class PlayerPaneHandle(QtWidgets.QSplitterHandle):
    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.splitter().reset_widths()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class PlayerPaneSplitter(QtWidgets.QSplitter):
    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.setObjectName('playerWorkspaceSplitter')
        self.setHandleWidth(12)
        self.setChildrenCollapsible(False)

    def createHandle(self):
        handle = PlayerPaneHandle(self.orientation(), self)
        handle.setToolTip('Drag to resize the player table and Selected player panel. Double-click to reset widths.')
        return handle

    def reset_widths(self):
        available = max(1, self.width() - self.handleWidth())
        inspector = min(360, max(220, available // 3))
        self.setSizes([max(1, available - inspector), inspector])
