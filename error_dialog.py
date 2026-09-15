"""Build error messages with an explicit clipboard action."""
from PyQt5 import QtCore, QtWidgets


def show_build_error(parent, title, message, warning=False):
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
