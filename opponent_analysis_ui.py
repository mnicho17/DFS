"""Cancellable, read-only standings browser. Each dialog owns one active job."""
import json
import os
from pathlib import Path
import tempfile
import threading

from PyQt5 import QtCore, QtWidgets
from opponent_analysis import AnalysisCancelled, analyze_standings, render_report, share_payload, username_key


class OpponentAnalysisWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    result = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    done = QtCore.pyqtSignal()

    def __init__(self, path, contest_format):
        super().__init__()
        self.path, self.contest_format = path, contest_format
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            result = analyze_standings(self.path, self.contest_format, self.stop.is_set, self.progress.emit)
            if not self.stop.is_set():
                self.result.emit(result)
        except AnalysisCancelled:
            pass
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.done.emit()


class NumberItem(QtWidgets.QTableWidgetItem):
    def __init__(self, value, digits=0):
        super().__init__('unknown' if value is None else f'{value:,.{digits}f}')
        self.setData(QtCore.Qt.UserRole, value)

    def __lt__(self, other):
        left, right = self.data(QtCore.Qt.UserRole), other.data(QtCore.Qt.UserRole)
        return (left is not None, left or 0) < (right is not None, right or 0)


class OpponentAnalysisDialog(QtWidgets.QDialog):
    def __init__(self, username='', folder='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Opponent Portfolios')
        self.resize(1100, 780)
        self.folder, self.username = folder, username
        self._thread = self._worker = None
        self._pending = self.result = None
        self._error = ''
        self._closing = False
        self._source = None
        self._result_source = None
        self._sort_column, self._sort_order = 0, QtCore.Qt.AscendingOrder
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel('Open one contest standings CSV to compare entry counts, lineup variety and exposure. '
                                'Analysis reads the file locally. Share exports contain usernames; detailed lineups are optional.')
        label.setWordWrap(True)
        layout.addWidget(label)
        row = QtWidgets.QHBoxLayout()
        self.format = QtWidgets.QComboBox()
        self.format.addItem('NFL Showdown', 'showdown')
        self.format.addItem('NFL Classic', 'classic')
        row.addWidget(self.format)
        self.open_button = QtWidgets.QPushButton('Open standings CSV…')
        self.open_button.clicked.connect(self.open_file)
        row.addWidget(self.open_button)
        self.cancel_button = QtWidgets.QPushButton('Cancel analysis')
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        row.addStretch()
        layout.addLayout(row)
        self.status = QtWidgets.QLabel('Choose the contest format, then open its standings file.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        filters = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit(username)
        self.search.setPlaceholderText('Filter by username (clear to view all entrants)')
        self.search.textChanged.connect(self.populate)
        filters.addWidget(self.search)
        self.band = QtWidgets.QComboBox()
        self.band.addItems(['All entry counts', '1', '2–5', '6–20', '21–150', '151+'])
        self.band.currentIndexChanged.connect(self.populate)
        filters.addWidget(self.band)
        layout.addLayout(filters)
        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(['Username', 'Entries', 'Readable', 'Unique', 'Shared players', 'Captain pool', 'Mean points', 'Best rank', 'Field copies'])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self.show_selection)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_rows)
        layout.addWidget(self.table, 2)
        self.table_status = QtWidgets.QLabel('')
        layout.addWidget(self.table_status)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 3)
        share = QtWidgets.QHBoxLayout()
        self.details = QtWidgets.QCheckBox('Include detailed lineups')
        self.details.toggled.connect(self.show_selection)
        share.addWidget(self.details)
        self.all_users = QtWidgets.QCheckBox('All usernames in JSON (summary only)')
        self.all_users.setToolTip('All-user JSON contains table metrics. Select one entrant for player/pair exposure and optional lineups.')
        self.all_users.toggled.connect(self._share_scope_changed)
        share.addWidget(self.all_users)
        self.copy_button = QtWidgets.QPushButton('Copy selected report')
        self.copy_button.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.report.toPlainText()))
        share.addWidget(self.copy_button)
        self.save_button = QtWidgets.QPushButton('Save analysis JSON…')
        self.save_button.clicked.connect(self.save_json)
        share.addWidget(self.save_button)
        close = QtWidgets.QPushButton('Close')
        close.clicked.connect(self.close)
        share.addWidget(close)
        layout.addLayout(share)
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)

    def open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'One contest standings CSV', self.folder, 'CSV files (*.csv)')
        if path:
            self.start(path)

    def start(self, path):
        if self._thread is not None:
            return
        self._pending, self._error, self._closing = None, '', False
        self._source = Path(path).resolve()
        self.open_button.setEnabled(False)
        self.format.setEnabled(False)
        self.save_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText('Reading standings… Previous completed results remain visible until this analysis succeeds.')
        worker = self._worker = OpponentAnalysisWorker(path, self.format.currentData())
        thread = self._thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._progress)
        worker.result.connect(self._receive)
        worker.error.connect(self._receive_error)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(self._retire)
        thread.start()

    @QtCore.pyqtSlot(str)
    def _progress(self, text):
        if self._worker is not None and not self._worker.stop.is_set():
            self.status.setText(text)

    @QtCore.pyqtSlot(object)
    def _receive(self, result):
        if self._worker is not None and not self._worker.stop.is_set():
            self._pending = result

    @QtCore.pyqtSlot(str)
    def _receive_error(self, message):
        self._error = message

    @QtCore.pyqtSlot()
    def _retire(self):
        thread, worker = self._thread, self._worker
        cancelled = worker.stop.is_set()
        self._thread = self._worker = None
        thread.deleteLater()
        self.open_button.setEnabled(True)
        self.format.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if not cancelled and self._pending is not None:
            self.result = self._pending
            self._result_source = self._source
            self.populate()
            self.status.setText(f"{self.result['source_name']}: {self.result['audit']['accepted_entries']:,} entries; "
                                f"{self.result['entrants']:,} entrants. {self.result['coverage']}")
        else:
            self.status.setText('Analysis cancelled; previous completed result preserved.' if cancelled else 'Analysis failed: ' + self._error)
        self._pending = None
        self.save_button.setEnabled(self.result is not None)
        if self._closing:
            super().reject()

    def cancel(self):
        if self._worker is not None:
            self._worker.cancel()
            self._pending = None
            self.cancel_button.setEnabled(False)
            self.status.setText('Cancelling; waiting for the reader to finish…')

    def closeEvent(self, event):
        if self._thread is not None:
            self._closing = True
            self.cancel()
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if self._thread is not None:
            self._closing = True
            self.cancel()
        else:
            super().reject()

    def selected_username(self):
        row = self.table.currentRow()
        return self.table.item(row, 0).data(QtCore.Qt.UserRole) if row >= 0 and self.table.item(row, 0) else None

    def populate(self):
        if self.result is None:
            return
        query = username_key(self.search.text())
        rows = [p for p in self.result['portfolios'] if query in p['username_key'] and
                (self.band.currentIndex() == 0 or p['entry_band'] == self.band.currentText())]
        # Sort the entire matching field before limiting rendered rows. Sorting
        # only the visible 1,000 would hide leaders later in username order.
        key = ('username_key', 'entries', 'readable_rosters', 'unique_lineups', 'mean_shared_players',
               'captain_pool', 'mean_points', 'best_rank', 'mean_field_copies')[self._sort_column]
        rows = sorted([p for p in rows if p[key] is not None], key=lambda p: p[key],
                      reverse=self._sort_order == QtCore.Qt.DescendingOrder) + [p for p in rows if p[key] is None]
        blocker = QtCore.QSignalBlocker(self.table)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(min(len(rows), 1000))
        for index, p in enumerate(rows[:1000]):
            item = QtWidgets.QTableWidgetItem(p['username'])
            item.setData(QtCore.Qt.UserRole, p['username_key'])
            self.table.setItem(index, 0, item)
            for col, (key, digits) in enumerate((('entries', 0), ('readable_rosters', 0), ('unique_lineups', 0),
                     ('mean_shared_players', 2), ('captain_pool', 0), ('mean_points', 2), ('best_rank', 0), ('mean_field_copies', 2)), 1):
                self.table.setItem(index, col, NumberItem(p[key], digits))
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)
        self.table_status.setText(f'{len(rows):,} matching entrants; showing the first {min(len(rows), 1000):,}. Filter to narrow; all-user JSON includes everyone.')
        if rows:
            self.table.selectRow(0)
        del blocker
        self.show_selection()

    def sort_rows(self, column):
        if column == self._sort_column:
            self._sort_order = QtCore.Qt.DescendingOrder if self._sort_order == QtCore.Qt.AscendingOrder else QtCore.Qt.AscendingOrder
        else:
            self._sort_order = QtCore.Qt.AscendingOrder if column in (0, 7) else QtCore.Qt.DescendingOrder
        self._sort_column = column
        self.populate()

    def show_selection(self):
        username = self.selected_username()
        self.copy_button.setEnabled(self.result is not None and username is not None)
        self.report.setPlainText(render_report(self.result, username, self.details.isChecked())
                                 if self.result is not None and username is not None else '')

    def _share_scope_changed(self, all_users):
        if all_users:
            self.details.setChecked(False)
        self.details.setEnabled(not all_users)

    def save_json(self):
        if self.result is None or self._thread is not None:
            return
        username = None if self.all_users.isChecked() else self.selected_username()
        if username is None and not self.all_users.isChecked():
            QtWidgets.QMessageBox.information(self, 'Select an entrant', 'Select a username or enable export of all usernames.')
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save opponent analysis', 'opponent-analysis.json', 'JSON files (*.json)')
        if not path:
            return
        destination = Path(path).resolve()
        if destination in (self._source, self._result_source) or destination.suffix.lower() != '.json':
            QtWidgets.QMessageBox.warning(self, 'Choose another file', 'Keep the original standings unchanged.')
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(share_payload(self.result, username or '', self.details.isChecked()), handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write('\n')
            os.replace(temporary, destination)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, 'Could not save analysis', str(exc))
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
