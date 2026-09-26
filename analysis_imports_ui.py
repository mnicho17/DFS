"""Background combined import and explicit salary-match review."""
import threading
import traceback
from PyQt5 import QtCore, QtWidgets

from analysis_imports import import_folders, pairing_state, save_pair, ImportCancelled


class CombinedImportWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, results_folder='', salary_folder='', username='', pair=None, db_path=None):
        super().__init__()
        self.results_folder, self.salary_folder, self.username = results_folder, salary_folder, username
        self.pair = pair
        from learning_db import history_db_path
        self.db_path = str(db_path or history_db_path())
        self.cancelled = threading.Event()

    def request_cancel(self):
        self.cancelled.set()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            if self.pair:
                saved = save_pair(*self.pair[:2], db_path=self.db_path, confirm_date=self.pair[2], cancelled=self.cancelled.is_set)
                result = dict(pairs_added=int(saved), pair_only=True, errors=[])
            else:
                result = import_folders(self.results_folder, self.salary_folder, username=self.username,
                                        db_path=self.db_path, cancelled=self.cancelled.is_set, progress=self.progress.emit)
                ids = result.get('analysis_import_ids', [])
                if ids and not self.cancelled.is_set():
                    from performance_review import analyze_saved_results
                    try:
                        review = analyze_saved_results(username=self.username, import_ids=set(ids), db_path=self.db_path,
                            cancelled=self.cancelled.is_set, progress=lambda text: self.progress.emit(0, 0, text))
                        result['analysis_message'] = review['message']
                    except Exception as exc:
                        result['errors'].append('Files saved; Analyze Saved Results can retry analysis: ' + str(exc))
            result['cancelled'] = bool(result.get('cancelled') or self.cancelled.is_set())
            if not result['cancelled'] and any(result.get(key) for key in ('results_imported','salaries_imported','pairs_added')):
                from learning_db import generate_learning_report
                try:
                    self.progress.emit(0, 0, 'Refreshing the saved-results report…')
                    result['report'] = generate_learning_report(username=self.username, db_path=self.db_path)
                except Exception as exc:
                    result['errors'].append('Files saved; Refresh Report can retry: ' + str(exc))
            result['cancelled'] = bool(result.get('cancelled') or self.cancelled.is_set())
            if result['cancelled']:
                result.pop('report', None)
            # All duplicate scans leave report/history untouched.
            self.finished.emit(result)
        except ImportCancelled:
            self.finished.emit(dict(cancelled=True, errors=[]))
        except Exception:
            self.error.emit(traceback.format_exc())


class SalaryMatchesDialog(QtWidgets.QDialog):
    """Select an explicit compatible revision; verification runs in the parent worker."""
    def __init__(self, parent=None, db_path=None):
        super().__init__(parent)
        self.setWindowTitle('Review Salary Matches')
        self.resize(780, 560)
        self.selection = None
        self.state = pairing_state(db_path)
        self.rows = self.state['results']
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel('Choose the exact contest and salary snapshot. Existing pairings stay fixed. '
            'Matching covers readable rosters; it does not establish a complete eligible pool or payouts.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.summary = QtWidgets.QLabel(
            f"{self.state['saved_pairings']} saved pairings; {self.state['unpaired_results']} unpaired results; "
            f"{self.state['invalid_saved_pairings']} invalid saved pairings. "
            f"Database: {self.state['database_identity']}" +
            ('\n' + '\n'.join(self.state['warnings']) if self.state['warnings'] else ''))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.results = QtWidgets.QComboBox()
        for row in self.rows:
            self.results.addItem(row['name'] + ' [' + row['hash'][:12] + '] — ' + row['status'], row)
        layout.addWidget(self.results)
        self.salaries = QtWidgets.QComboBox()
        layout.addWidget(self.salaries)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.show_diagnostics = QtWidgets.QCheckBox('Show rejected salary candidates (diagnostics)')
        layout.addWidget(self.show_diagnostics)
        self.diagnostics = QtWidgets.QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(100)
        self.diagnostics.hide()
        self.show_diagnostics.toggled.connect(self.diagnostics.setVisible)
        layout.addWidget(self.diagnostics)
        self.confirm_date = QtWidgets.QCheckBox('I confirm this salary slate belongs to the selected contest (result date missing).')
        layout.addWidget(self.confirm_date)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Close)
        self.buttons.accepted.connect(self._select)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.results.currentIndexChanged.connect(self._update)
        self.salaries.currentIndexChanged.connect(self._candidate_changed)
        self._update()

    def _update(self):
        self.salaries.clear()
        self.show_diagnostics.setChecked(False)
        self.diagnostics.clear()
        self.confirm_date.setChecked(False)
        row = self.results.currentData()
        if not row:
            self.details.setPlainText('No results cataloged yet. Import Results & Salaries first.')
            self.buttons.button(QtWidgets.QDialogButtonBox.Save).setEnabled(False)
            return
        notes = [f"Status: {row['status']}"]
        if row['entries'] is not None:
            pct = f"{row['readable_pct']:.1f}%" if row['readable_pct'] is not None else 'n/a'
            notes.append(f"Readable result rosters: {row['readable']:,} / {row['entries']:,} ({pct})")
        notes.append(f"Unreadable result rosters: {row['unreadable']:,} (excluded from matching).")
        saved = row['saved_pair']
        rejected = []
        for candidate in row['candidates']:
            label = candidate['name'] + ' [' + candidate['hash'][:12] + ']'
            if not candidate['compatible']:
                rejected.append(label + ': ' + candidate['reason'])
            if candidate['compatible'] and not saved:
                self.salaries.addItem(label, candidate)
                notes.append(label + ': ' + candidate['reason'])
        if saved:
            notes[1:1] = [f"Salary file: {saved['name']}", f"Revision: {saved['hash']}",
                          f"Qualification: {saved['reason']}", 'Existing pairing preserved.']
            self.salaries.addItem(saved['name'] + ' [' + saved['hash'][:12] + ']', saved)
            if row['status'] == 'INVALID_SAVED_PAIR':
                notes.append('This saved revision no longer qualifies. No automatic repair or replacement was made.')
        elif not row['candidates']:
            notes.append('No salary files imported. Choose a salary folder and run the combined import.')
        elif row['status'] == 'NO_COMPATIBLE_MATCH':
            notes.extend(rejected)
        self.diagnostics.setPlainText('\n\n'.join(rejected))
        self.show_diagnostics.setVisible(bool(rejected) and row['status'] != 'NO_COMPATIBLE_MATCH')
        self.details.setPlainText('\n\n'.join(notes))
        self.salaries.setEnabled(row['status'] == 'READY_TO_PAIR')
        self.buttons.button(QtWidgets.QDialogButtonBox.Save).setEnabled(row['status'] == 'READY_TO_PAIR')
        self._candidate_changed()

    def _candidate_changed(self):
        candidate = self.salaries.currentData()
        row = self.results.currentData()
        self.confirm_date.setVisible(bool(row and row['status'] == 'READY_TO_PAIR' and candidate and not candidate['automatic']))
        self.confirm_date.setChecked(False)

    def _select(self):
        row, candidate = self.results.currentData(), self.salaries.currentData()
        if not row or not candidate or row['status'] != 'READY_TO_PAIR':
            return
        needs_date = not candidate['automatic']
        if needs_date and not self.confirm_date.isChecked():
            QtWidgets.QMessageBox.information(self, 'Confirm the contest date', 'Confirm that this exact salary slate belongs to the selected contest.')
            return
        self.selection = (row['hash'], candidate['hash'], self.confirm_date.isChecked())
        self.accept()


def import_summary(result):
    lines = ['Import cancelled; completed files remain saved.' if result.get('cancelled') else 'Import complete.',
             f"New result files: {result.get('results_imported', 0):,}",
             f"New salary files: {result.get('salaries_imported', 0):,}",
             f"Already imported files skipped: {result.get('duplicates_skipped', 0):,}",
             f"Other CSV files ignored: {result.get('ignored', 0):,}",
             f"Your result entries added: {result.get('personal_results_added', 0):,}",
             f"New contest salary matches: {result.get('pairs_added', 0):,}"]
    if result.get('unpaired'):
        lines.append(f"{result['unpaired']:,} results need review. Use Review Salary Matches.")
    if result.get('pairs_added'):
        lines.append('Analyze Saved Results applies saved salary matches to construction reports.')
    if result.get('analysis_message'):
        lines.append(result['analysis_message'])
    if result.get('errors'):
        lines.append('Files needing retry / review:\n' + '\n'.join(result['errors']))
    return '\n'.join(lines)
