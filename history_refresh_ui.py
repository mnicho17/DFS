"""One background folder refresh shared by startup and Results & Learning."""
from PyQt5 import QtCore
from analysis_imports_ui import CombinedImportWorker


def refresh_summary(result):
    if result.get('unchanged_checkpoint'):
        return ('History unchanged; completed imports and indexes reused.\n'
                'Checked file metadata and database revision; no source hashing or indexing needed.\n'
                f"{result.get('unpaired', 0)} results still need salary matching.")
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
        self._automatic = self._yielded = False
        self._deferred_start = False
        from history_folder_watch import HistoryFolderWatch
        self.watch = HistoryFolderWatch(self)

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

    @property
    def queued(self):
        queue=getattr(self.parent(),'background_queue',None)
        return self.watch.pending or bool(queue and 'history' in queue.pending)

    def status_line(self):
        if self.busy:
            return self.text.split('\n')[0]
        if self.queued:
            return 'History refresh queued; waiting for quiet sources and current app work.'
        if self.watch.paused:
            return self.watch.message
        return self.text.split('\n')[0]

    def enabled(self):
        return str(self.settings.value('learning/auto_history_refresh', 'true')).lower() in ('true', '1')

    def startup(self):
        if self.enabled() and (self.settings.value('learning/results_folder', '') or self.settings.value('learning/salary_folder', '')):
            self.watch.arm()
            self.start(automatic=True)

    def set_enabled(self, value):
        self.settings.setValue('learning/auto_history_refresh', bool(value))
        if value:
            self.watch.arm()
        else:
            self.watch.cancel_pending()
            self.watch.arm()
        self.changed.emit()

    def automatic_ready(self):
        if self.watch.closed or self.watch.paused or not self.enabled():
            return False
        owner = self.parent()
        backup = getattr(owner, 'idle_backup', None)
        if backup is not None:
            from background_queue import idle_ready
            if not idle_ready(owner,'history'):
                return False
        queue = getattr(owner, 'background_queue', None)
        return not self._reports or bool(queue and queue.active)

    def yield_work(self):
        if self.busy and self._automatic and not self._yielded:
            self._yielded = True
            self.stop.set()
            self.text = 'Yielding history refresh for app activity; waiting for safe worker retirement…'
            self.changed.emit()

    def start(self, *, automatic=False):
        if self.watch.closed:
            return False
        queue=getattr(self.parent(),'background_queue',None)
        if queue and (not queue.active or queue.active['key']!='history'):
            if not automatic and 'history' in queue.pending:
                old = queue.pending.pop('history')
                queue.hub.finish(old['task'], 'Superseded', 'Explicit full refresh replaces the queued automatic change check.')
            from background_queue import HISTORY_PRIORITY
            queue.request('history','Results and salary history refresh',HISTORY_PRIORITY,
                lambda:self.start(automatic=automatic),self.yield_work if automatic else None,on_cancel=self.cancel,
                ready=(self.automatic_ready if automatic else lambda:not self.watch.closed and (not self._reports or bool(queue.active))),
                idle_only=automatic,
                detail=('Waiting for idle app time and safe worker retirement.' if automatic else 'History refresh precedes backup and idle preparation.'))
            queue.pump()
            return False
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
        if not automatic:
            self.watch.resume()
        self._automatic, self._yielded = automatic, False
        self._pending = None
        self.text = 'Refreshing saved Results and Salary folders…'
        worker = self.worker = CombinedImportWorker(results, salaries,
            str(self.settings.value('learning/dk_username', '') or ''), db_path=self.db_path, refresh_history=True, automatic=automatic)
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
        from task_dashboard import watch_task
        watch_task(self, 'Results and salary history refresh', worker, thread, self.cancel,
                   lambda *args: refresh_summary(args[0]) if len(args) == 1 else str(args[-1]))
        thread.start()
        return True

    def progress(self, done, total, text):
        if not self.stop.is_set():
            self.text = text
            self.changed.emit()

    def receive(self, result):
        if self._yielded:result['background_yield']=True
        if result.get('unchanged_checkpoint') and not result.get('cancelled'):
            result.update(no_work=True,next_check='Watching Results/Salary folders for changes; no polling timer.')
        self._pending = result

    def failure(self, message):
        self._pending = dict(errors=[message], failed=True)

    def cancel(self):
        self._yielded = False
        self.watch.pause()
        if self.busy:
            self.stop.set()
            self.text = 'Cancelling refresh; waiting for safe worker retirement…'
            self.changed.emit()

    def retire(self):
        result = dict(self._pending or {})
        result['cancelled'] = bool(result.get('cancelled') or self.stop.is_set())
        retry = (self._yielded and not self.watch.closed and not self.watch.paused
                 and not result.get('failed') and not result.get('errors')
                 and not result.get('username_history', {}).get('errors'))
        self.text = ('History refresh yielded safely; completed files remain saved. Waiting for manual workers to retire before retrying.'
                     if retry else refresh_summary(result))
        self.settings.setValue('learning/last_history_refresh', self.text)
        self.settings.sync()
        thread = self.thread
        self.thread = self.worker = None
        thread.deleteLater()
        self._pending = None
        if not retry:
            self.watch.accept_result(result)
        self.changed.emit()
        self.completed.emit()
        queue=getattr(self.parent(),'background_queue',None)
        if queue:queue.release('history')
        if retry:
            self.start(automatic=True)
