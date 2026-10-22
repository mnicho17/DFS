"""Build error messages with an explicit clipboard action."""
from PyQt5 import QtCore, QtWidgets


def show_build_error(parent, title, message, warning=False):
    if warning:
        return show_build_stop(parent, title, message)
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QtWidgets.QMessageBox.Warning if warning else QtWidgets.QMessageBox.Critical)
    box.setTextFormat(QtCore.Qt.PlainText)
    box.setText(message)
    box.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    copy_button = box.addButton('Copy Error', QtWidgets.QMessageBox.ActionRole)
    # Copy the original string, including all line breaks and traceback details.
    copy_button.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(message))
    box.setDefaultButton(QtWidgets.QMessageBox.Ok)
    box.exec_()


def show_build_stop(parent, title, message):
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setObjectName('buildStopDialog')
    dialog.resize(850, 600)
    layout = QtWidgets.QVBoxLayout(dialog)
    label = QtWidgets.QLabel('The search stopped short. Review the captured details and choose a next action.')
    label.setWordWrap(True)
    layout.addWidget(label)
    details = QtWidgets.QPlainTextEdit(dialog)
    details.setObjectName('buildStopDetails')
    details.setReadOnly(True)
    details.setPlainText(message)
    layout.addWidget(details, 1)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close, parent=dialog)
    buttons.rejected.connect(dialog.reject)
    copy = buttons.addButton('Copy Error', QtWidgets.QDialogButtonBox.ActionRole)
    copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(message))
    choice = []
    for text, token in (('Review search...', 'search'), ('Review limits...', 'limits')):
        button = buttons.addButton(text, QtWidgets.QDialogButtonBox.ActionRole)
        def choose(_checked=False, action=token):
            choice.append(action)
            dialog.accept()
        button.clicked.connect(choose)
    buttons.button(QtWidgets.QDialogButtonBox.Close).setDefault(True)
    layout.addWidget(buttons)
    dialog.exec_()
    return choice[0] if choice else None
