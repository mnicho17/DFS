# widgets.py
from __future__ import annotations

from PyQt5 import QtGui, QtWidgets


class CopyRowTableWidget(QtWidgets.QTableWidget):
    """QTableWidget that copies entire selected rows as TSV on Ctrl+C."""
    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.matches(QtGui.QKeySequence.Copy):
            sel_model = self.selectionModel()
            if sel_model is not None:
                indexes = sel_model.selectedIndexes()
                if indexes:
                    rows = sorted({idx.row() for idx in indexes})
                    lines = []
                    for r in rows:
                        vals = []
                        for c in range(self.columnCount()):
                            item = self.item(r, c)
                            vals.append("" if item is None else item.text())
                        lines.append("\t".join(vals))
                    QtWidgets.QApplication.clipboard().setText("\n".join(lines))
                    return
        super().keyPressEvent(event)
