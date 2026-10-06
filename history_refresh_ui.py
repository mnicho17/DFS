"""One background folder refresh shared by startup and Results & Learning."""
from PyQt5 import QtCore
from analysis_imports_ui import CombinedImportWorker


def refresh_summary(result):
    history = result.get('username_history', {})
    contests = history.get('contests', [])
    added = [row for row in contests if not row.get('unchanged')]
    errors = list(result.get('errors', []))
    errors.extend(row['name'] + ': ' + row['error'] for row in history.get('errors', []))
    text = ('Refresh cancelled. Completed files/contests remain saved.' if result.get('cancelled') else
            'History refresh failed; retry when sources are available.' if result.get('failed') else 'History refresh complete.')
    text += (f"\n{result.get('results_imported', 0)} new results; {result.get('salaries_imported', 0)} new salaries; "
             f"{result.get('duplicates_skipped', 0)} identical files skipped."
             f"\n{len(added)} newly indexed contests ({sum(row.get('entries', 0) for row in added):,} entries); "
             f"{len(contests)-len(added)} unchanged contests verified."
             f"\n{result.get('unpaired', 0)} results need salary matching; {len(errors)} errors need retry/review.")
    if result.get('cancelled') and not history:
        text += '\nUsername indexing was interrupted or not started; refresh again to inspect completed work.'
    if errors:
        text += '\n\n' + '\n'.join(errors)
    if result.get('changed_sources'):
        text += '\n\nChanged files saved as separate evidence; review revisions. Existing mappings stay fixed:\n' + '\n'.join(result['changed_sources'])
    return text


class HistoryRefreshController(QtCore.QObject):
    changed = QtCore.pyqtSignal()
    completed = QtCore.pyqtSignal()

    def __init__(self, settings, db_path, parent=None):
        super().__init__(parent)
        self.settings, self.db_path = settings, str(db_path)
        self.thread = self.worker = None
        self.stop = None
        self.text = str(settings.value('learning/last_history_refresh', '') or '')
        self._pending = None
        self._reports = 0
        self._deferred_start = False

    def report_started(self):
        self._reports += 1

    def report_finished(self, *, resume=True):
        self._reports -= 1
        if not self._reports and self._deferred_start:
            self._deferred_start = False
            if resume:
                QtCore.QTimer.singleShot(0, self.start)

    @property
    def busy(self):
        return self.thread is not None

    def enabled(self):
        return str(self.settings.value('learning/auto_history_refresh', 'true')).lower() in ('true', '1')

    def startup(self):
        if self.enabled() and (self.settings.value('learning/results_folder', '') or self.settings.value('learning/salary_folder', '')):
            self.start()

    def start(self):
        if self._reports:
            self._deferred_start = True
            return False
        if self.busy:
            return False
        results = str(self.settings.value('learning/results_folder', '') or '')
        salaries = str(self.settings.value('learning/salary_folder', '') or '')
        if not (results or salaries):
            self.text = 'Choose Results and/or Salary folders in Results & Learning first.'
            self.changed.emit()
            return False
        self._pending = None
        self.text = 'Refreshing saved Results and Salary folders…'
        worker = self.worker = CombinedImportWorker(results, salaries,
            str(self.settings.value('learning/dk_username', '') or ''), db_path=self.db_path, refresh_history=True)
        self.stop = worker.cancelled
        thread = self.thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.progress)
        worker.finished.connect(self.receive)
        worker.error.connect(self.failure)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        thread.finished.connect(self.retire)
        self.changed.emit()
        thread.start()
        return True

    def progress(self, done, total, text):
        if not self.stop.is_set():
            self.text = text
            self.changed.emit()

    def receive(self, result):
        self._pending = result

    def failure(self, message):
        self._pending = dict(errors=[message], failed=True)

    def cancel(self):
        if self.busy:
            self.stop.set()
            self.text = 'Cancelling refresh; waiting for safe worker retirement…'
            self.changed.emit()

    def retire(self):
        result = dict(self._pending or {})
        result['cancelled'] = bool(result.get('cancelled') or self.stop.is_set())
        self.text = refresh_summary(result)
        self.settings.setValue('learning/last_history_refresh', self.text)
        self.settings.sync()
        thread = self.thread
        self.thread = self.worker = None
        thread.deleteLater()
        self._pending = None
        self.changed.emit()
        self.completed.emit()
