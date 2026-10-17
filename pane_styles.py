"""Higher-contrast pane boundaries shared by the desktop workspace."""
from PyQt5 import QtWidgets

PANE_QSS = """
QGroupBox {
    border: 2px solid #637B9A;
    border-radius: 6px;
    margin-top: 18px;
    padding: 10px;
}
QGroupBox::title { color: #FFFFFF; font-weight: 600; left: 10px; padding: 0 6px; }
#simpleBuildPanel { background-color: #1B2738; border-color: #8199BB; }
#simpleBuildPanel QLabel { background-color: transparent; color: #F4F7FC; }
#workspaceControlTabs::pane { border: 2px solid #637B9A; }
QSplitter::handle { background-color: #637B9A; border: 1px solid #9BB0CB; }
QSplitter::handle:hover { background-color: #9BBEF1; }
QDockWidget::title { background-color: #263A54; color: white; padding: 7px; border-bottom: 2px solid #8199BB; }
#preparationOverview, #preparationDetails, #taskOverview, #taskDetails { background-color: #192334; }
#preparationTable, #preparationText, #taskTable, #taskText { border: 1px solid #8199BB; }
"""


def labelled_pane(title, widget, name):
    group = QtWidgets.QGroupBox(title)
    group.setObjectName(name)
    layout = QtWidgets.QVBoxLayout(group)
    layout.addWidget(widget)
    return group
