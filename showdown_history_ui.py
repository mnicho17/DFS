"""Explicit preparation of a frozen field profile outside the GUI thread."""
import threading
from PyQt5 import QtCore, QtWidgets
from showdown_history_field import prepare_profile, slate_cutoff


class ProfileWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    done = QtCore.pyqtSignal()

    def __init__(self, db_path, cutoff, preset):
        super().__init__()
        self.db_path, self.cutoff, self.preset = db_path, cutoff, preset
        self.stop = threading.Event()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            profile = prepare_profile(self.db_path, self.cutoff, self.preset, self.stop.is_set, self.progress.emit)
            if not self.stop.is_set(): self.result.emit(profile)
        except Exception as exc:
            if not self.stop.is_set(): self.error.emit(str(exc))
        finally:
            self.done.emit()


class HistoryFieldDialog(QtWidgets.QDialog):
    def __init__(self, players, preset, parent=None):
        super().__init__(parent)
        from learning_db import history_db_path
        self.db_path, self.cutoff = history_db_path(), slate_cutoff(players)
        self.profile = self._pending = None
        self._worker = self._thread = None
        self._closing = False
        self.setWindowTitle('Prepare historical Showdown field')
        self.resize(650, 300)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(f'Use verified results strictly before {self.cutoff}. Each game gets equal weight; contest entry limits stay separate. Historical construction receives 35% weight. This is experimental field modeling, without proven return improvement. Player projections and your lineup rules are unchanged.')
        note.setWordWrap(True); layout.addWidget(note)
        self.preset = QtWidgets.QComboBox()
        self.preset.addItems(['Single Entry', '3-Max', '20-Max', '150-Max'])
        self.preset.setCurrentText(preset); layout.addWidget(self.preset)
        self.preset.currentTextChanged.connect(self.invalidate)
        self.status = QtWidgets.QLabel('Requires indexed mapped results: three prior games, 1,000 known entries, and 95% construction coverage per contest.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.prepare = QtWidgets.QPushButton('Prepare from saved history')
        self.prepare.clicked.connect(self.start); layout.addWidget(self.prepare)
        self.apply = QtWidgets.QPushButton('Use this profile for Deep builds')
        self.apply.setEnabled(False); self.apply.clicked.connect(self.accept); layout.addWidget(self.apply)
        close = QtWidgets.QPushButton('Cancel / close'); close.clicked.connect(self.reject); layout.addWidget(close)

    def invalidate(self):
        self.profile = None; self.apply.setEnabled(False)

    def start(self):
        if self._thread is not None: return
        self.invalidate(); self._pending = None; self.prepare.setEnabled(False); self.preset.setEnabled(False)
        worker = self._worker = ProfileWorker(self.db_path, self.cutoff, self.preset.currentText())
        thread = self._thread = QtCore.QThread(self); worker.moveToThread(thread)
        thread.started.connect(worker.run); worker.progress.connect(self.status.setText)
        worker.result.connect(self.receive); worker.error.connect(self.status.setText)
        worker.done.connect(thread.quit); worker.done.connect(worker.deleteLater)
        thread.finished.connect(self.retire); thread.start()

    def receive(self, profile):
        if not self._worker.stop.is_set(): self._pending = profile

    def retire(self):
        cancelled = self._worker.stop.is_set(); thread = self._thread
        self._worker = self._thread = None; thread.deleteLater()
        self.prepare.setEnabled(True); self.preset.setEnabled(True)
        if not cancelled and self._pending is not None:
            self.profile = self._pending
            p = self.profile['payload']
            self.status.setText(f"Prepared {len(p['training'])} prior games, {sum(r['known'] for r in p['training']):,} known entries; {len(p['excluded'])} contests excluded. Coverage describes observed entries. Sources are checked again before a build.")
            self.apply.setEnabled(True)
        if self._closing: super().reject()

    def reject(self):
        if self._worker is not None:
            self._closing = True; self._worker.stop.set(); self._pending = None
        else: super().reject()

    def closeEvent(self, event):
        if self._worker is not None: self.reject(); event.ignore()
        else: super().closeEvent(event)
