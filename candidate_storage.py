"""User allocation for reusable candidate libraries; never removes saved work."""
from PyQt5 import QtCore, QtWidgets

KEY = 'storage/candidate_budget_gib'
DEFAULT_GIB = 64


def budget_bytes(default=DEFAULT_GIB * 1024**3):
    value = QtCore.QSettings('DFS Optimizer', 'DFS Optimizer').value(KEY)
    try:
        gib = int(value)
        if 1 <= gib <= 4096:
            return gib * 1024**3
    except (TypeError, ValueError, OverflowError):
        pass
    return default


def open_storage(parent):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle('Candidate storage')
    layout = QtWidgets.QVBoxLayout(dialog)
    note = QtWidgets.QLabel('Shared space allowance for Smart preparation candidate libraries. '
        'Existing files are retained. Preparation also keeps 5 GiB free on the drive. '
        'Increasing this allowance lets storage-paused slates resume on the next preparation check.')
    note.setWordWrap(True)
    layout.addWidget(note)
    size = QtWidgets.QSpinBox(dialog)
    size.setRange(1, 4096)
    size.setSuffix(' GiB')
    size.setValue(budget_bytes() // 1024**3)
    layout.addWidget(size)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec_() == QtWidgets.QDialog.Accepted:
        settings = QtCore.QSettings('DFS Optimizer', 'DFS Optimizer')
        settings.setValue(KEY, size.value())
        settings.sync()
