"""Background qualification and explicit SIM validation build review."""
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
from PyQt5 import QtCore, QtWidgets

from analysis_imports import ImportCancelled, _check
from distribution_build_selection import catalog, save_choice, saved_choice


class SIMBuildWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, db_path, import_id=None, choice=None):
        super().__init__()
        self.db_path, self.import_id, self.choice = db_path, import_id, choice
        self.cancelled = threading.Event()

    def request_cancel(self):
        self.cancelled.set()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            self.progress.emit(0, 0, 'Checking saved pregame SIM evidence…')
            if self.choice is not None:
                result = save_choice(self.db_path, self.import_id, self.choice, self.cancelled.is_set)
            else:
                with closing(sqlite3.connect(Path(self.db_path).resolve().as_uri()+'?mode=ro', uri=True)) as conn:
                    if self.import_id is None:
                        result = dict(imports=conn.execute('''SELECT DISTINCT h.import_id,h.file_name
                            FROM historical_imports h JOIN analysis_sources s ON s.import_id=h.import_id
                            JOIN analysis_salary_pairs p ON p.result_hash=s.hash ORDER BY h.created_at DESC,h.file_name''').fetchall())
                    else:
                        result = catalog(conn, self.import_id, Path(self.db_path).resolve().parent, self.cancelled.is_set)
                        result.pop('scores')
                        result['saved_choice'] = saved_choice(conn, self.import_id)
                _check(self.cancelled.is_set)
            self.finished.emit(result)
        except ImportCancelled:
            self.finished.emit(dict(cancelled=True, committed=False))
        except Exception as exc:
            self.error.emit(str(exc))


class SIMBuildDialog(QtWidgets.QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Select pregame SIM validation build')
        self.resize(1050, 540)
        self.choice = None
        self.data = data
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel(data['name'] + '\nChoose the exact recorded SIM build to validate. '
            'This preserves the automatic forecast snapshot and does not establish which build was submitted.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setObjectName('simValidationBuildTable')
        self.table.setHorizontalHeaderLabels(['Completed before kickoff', 'Input ID', 'Scenarios', 'Actual-score coverage', 'Model'])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, candidate in enumerate(data['candidates']):
            self.table.insertRow(row)
            values = [candidate['finished_at'], candidate['input_id'][:12], str(candidate['scenarios']),
                      f"{candidate['covered']}/{candidate['players']} players", candidate['model'][:12]]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        reasons = '\n'.join(r['input_id'][:12]+': '+r['reason'] for r in data.get('rejected', []))
        self.details.setPlainText('No qualifying saved captures.' if not data['candidates'] else 'Select a row to review its full identity.')
        if reasons:
            self.details.appendPlainText('Excluded captures:\n'+reasons)
        if data.get('saved_choice'):
            self.details.appendPlainText('Saved validation choice: '+data['saved_choice']['input_id'])
        layout.addWidget(self.details)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.use = buttons.addButton('Use selected SIM build', QtWidgets.QDialogButtonBox.AcceptRole)
        self.use.setObjectName('useSIMValidationBuildButton')
        self.use.setEnabled(False)
        buttons.rejected.connect(self.reject)
        self.use.clicked.connect(self._accept)
        self.table.currentCellChanged.connect(self._selected)
        self.table.clearSelection()
        layout.addWidget(buttons)

    def _selected(self, row, *unused):
        valid = 0 <= row < len(self.data['candidates'])
        self.use.setEnabled(valid)
        if valid:
            c = self.data['candidates'][row]
            self.details.setPlainText(f"Input: {c['input_id']}\nCapture: {c['capture_id']}\nModel: {c['model']}\n"
                f"Snapshot recorded: {c['recorded_at']}\nSIM completed: {c['finished_at']}\n"
                'Exact salary/player IDs, game identity and pre-kickoff timing qualified. Revalidated before saving.')

    def _accept(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.data['candidates']):
            self.choice = dict(self.data['candidates'][row])
            self.accept()
