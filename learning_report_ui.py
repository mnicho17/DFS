"""Build the complete local report without blocking the Qt event loop."""
import threading
from PyQt5 import QtCore


class LearningReportWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, builder, username, db_path):
        super().__init__()
        self.builder, self.username, self.db_path = builder, username, db_path
        self.cancelled = threading.Event()

    def request_cancel(self):
        self.cancelled.set()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            self.progress.emit(0, 0, 'Building report and verifying saved evidence…')
            payload = {} if self.cancelled.is_set() else self.builder(
                username=self.username, db_path=self.db_path)
            self.finished.emit(dict(cancelled=self.cancelled.is_set(), report=payload))
        except Exception as exc:
            self.error.emit(str(exc))
