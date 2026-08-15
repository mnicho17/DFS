# app.py
from __future__ import annotations

import os
import sys
import logging
import traceback
from typing import Any

from PyQt5 import QtCore, QtWidgets

DARK_QSS = """
QWidget {
    background-color: #111318;
    color: #E8EAED;
    font-size: 10pt;
}
QMainWindow, QDialog {
    background-color: #0B0D10;
}
QTableWidget, QTableView, QTreeView, QListView, QTextEdit, QPlainTextEdit {
    background-color: #151922;
    color: #E8EAED;
    gridline-color: #2A303B;
    selection-background-color: #2F6FED;
    selection-color: #FFFFFF;
    alternate-background-color: #111722;
}
QHeaderView::section {
    background-color: #202634;
    color: #F1F3F4;
    border: 1px solid #2F3645;
    padding: 4px;
}
QPushButton {
    background-color: #242A36;
    color: #F1F3F4;
    border: 1px solid #3A4354;
    border-radius: 5px;
    padding: 5px 9px;
}
QPushButton:hover { background-color: #303849; }
QPushButton:pressed { background-color: #1B2330; }
QPushButton:disabled { color: #777D87; background-color: #171B22; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QInputDialog, QAbstractSpinBox {
    background-color: #171C25;
    color: #F1F3F4;
    border: 1px solid #3A4354;
    border-radius: 4px;
    padding: 3px;
}
QComboBox QAbstractItemView {
    background-color: #171C25;
    color: #F1F3F4;
    selection-background-color: #2F6FED;
}
QTabWidget::pane { border: 1px solid #2F3645; }
QTabBar::tab {
    background: #1A1F2B;
    color: #C9D1D9;
    padding: 7px 12px;
    border: 1px solid #2F3645;
}
QTabBar::tab:selected { background: #2A3140; color: #FFFFFF; }
QGroupBox {
    border: 1px solid #2F3645;
    border-radius: 6px;
    margin-top: 8px;
    padding: 8px;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QStatusBar { background-color: #0B0D10; color: #DADCE0; }
QProgressBar {
    background-color: #171C25;
    color: #FFFFFF;
    border: 1px solid #3A4354;
    border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk { background-color: #2F6FED; }
QToolTip { background-color: #202634; color: #F1F3F4; border: 1px solid #3A4354; }
"""


# ---- ensure local folder is on sys.path ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ---- basic startup diagnostics ----
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logging.info("CWD: %s", os.getcwd())
logging.info("BASE_DIR: %s", BASE_DIR)
try:
    logging.info("Files in BASE_DIR: %s", sorted(os.listdir(BASE_DIR)))
except Exception as e:
    logging.info("Could not list BASE_DIR: %s", e)

from logger_setup import setup_logging
from main_window import MainWindow


_DEFAULT_EXCEPTHOOK = sys.excepthook


def _show_unhandled_exception_dialog(details: str) -> None:
    try:
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Critical)
        box.setWindowTitle("DFS Optimizer recovered from an error")
        box.setText("An unexpected error was contained, so the app can remain open.")
        box.setInformativeText(
            "The current action may not have completed. You can copy the technical details for troubleshooting, "
            "then save or export any valid lineups that are still visible."
        )
        box.setDetailedText(details)
        copy_button = box.addButton("Copy Details", QtWidgets.QMessageBox.ActionRole)
        box.addButton(QtWidgets.QMessageBox.Ok)
        box.exec_()
        if box.clickedButton() is copy_button:
            QtWidgets.QApplication.clipboard().setText(details)
    except Exception:
        logging.getLogger("dfs.crash").exception("Could not display the recovered-error dialog")


def handle_unhandled_exception(exc_type: Any, exc_value: BaseException, exc_traceback: Any) -> None:
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        _DEFAULT_EXCEPTHOOK(exc_type, exc_value, exc_traceback)
        return
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.getLogger("dfs.crash").critical(
        "Unhandled UI exception was contained:\n%s",
        details,
    )
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    if QtCore.QThread.currentThread() == app.thread():
        _show_unhandled_exception_dialog(details)
    else:
        QtCore.QTimer.singleShot(0, lambda: _show_unhandled_exception_dialog(details))


def install_exception_handler() -> None:
    sys.excepthook = handle_unhandled_exception


def main() -> int:
    setup_logging(name="dfs")
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)
    install_exception_handler()
    w = MainWindow()
    w.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
