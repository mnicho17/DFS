from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

import app as desktop_app


class AppErrorHandlingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_unhandled_ui_error_is_logged_and_shown_without_reraising(self):
        try:
            raise RuntimeError("display failed")
        except RuntimeError:
            exc_type, exc_value, exc_traceback = sys.exc_info()

        with mock.patch.object(desktop_app, "_show_unhandled_exception_dialog") as show, mock.patch.object(
            desktop_app.logging.getLogger("dfs.crash"), "critical"
        ) as critical:
            desktop_app.handle_unhandled_exception(exc_type, exc_value, exc_traceback)

        critical.assert_called_once()
        show.assert_called_once()
        self.assertIn("RuntimeError: display failed", show.call_args.args[0])

    def test_install_exception_handler_uses_containing_hook(self):
        previous = sys.excepthook
        try:
            desktop_app.install_exception_handler()
            self.assertIs(sys.excepthook, desktop_app.handle_unhandled_exception)
        finally:
            sys.excepthook = previous


if __name__ == "__main__":
    unittest.main()
