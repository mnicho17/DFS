"""Event-driven history refresh; notifications trigger the existing full validation."""
from pathlib import Path
import os
import weakref
from PyQt5 import QtCore

MAX_WATCH_PATHS = 4096
QUIET_MS = 30000


class HistoryFolderWatch(QtCore.QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = weakref.proxy(controller)
        self.watcher = QtCore.QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.schedule)
        self.watcher.directoryChanged.connect(self.schedule)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dispatch)
        self.observed = []
        self.pending = self.paused = self.closed = False
        self.message = 'Folder watching is not armed.'
        self.task = None
        self.hub = None

    def enabled(self):
        setting = str(self.controller.settings.value('learning/watch_history_changes', 'true')).lower()
        # Monitoring needs an app/dialog owner for shutdown and cancellation.
        return self.controller.parent() is not None and self.controller.enabled() and setting in ('true', '1') and not self.closed

    def roots(self):
        return tuple(str(Path(str(self.controller.settings.value(key, '') or '')).resolve())
                     for key in ('learning/results_folder', 'learning/salary_folder')
                     if str(self.controller.settings.value(key, '') or '').strip())

    def arm(self, paths=None):
        if paths is not None:
            self.observed = list(paths[:MAX_WATCH_PATHS])
        previous = self.watcher.files() + self.watcher.directories()
        if previous:
            self.watcher.removePaths(previous)
        if not self.enabled() or self.paused:
            if not self.paused:
                self.message = 'Folder watching is off.'
            return
        roots = [Path(p) for p in self.roots()]
        if not roots:
            self.message = 'Choose source folders to enable folder watching.'
            return
        owned = (Path(self.controller.db_path).parent/'analysis_sources').resolve()
        paths = []
        seen = set()
        for raw in list(map(str, roots)) + self.observed:
            # Worker-catalogued paths are already resolved. Normalize lexically
            # here rather than stat every source again on the GUI thread.
            path = Path(os.path.abspath(raw))
            if (path == owned or owned in path.parents or
                    not any(path == root or root in path.parents for root in roots)):
                continue
            key = str(path).casefold()
            if key not in seen:
                paths.append(str(path))
                seen.add(key)
            if len(paths) >= MAX_WATCH_PATHS:
                break
        failed = self.watcher.addPaths(paths) if paths else []
        count = len(paths)-len(failed)
        incomplete = bool(failed) or len(self.observed) >= MAX_WATCH_PATHS
        self.message = f'Watching {count} source files/folders for changes; updates wait 30 seconds of quiet.'
        if incomplete:
            self.message += ' Some paths could not be watched or exceed the limit; use Refresh history now for full coverage.'

    def configuration_changed(self):
        self.cancel_pending()
        self.observed = []
        self.paused = False
        self.arm()
        if self.enabled() and self.roots():
            self.schedule()
        self.controller.changed.emit()

    def schedule(self, *_):
        if not self.enabled() or self.paused or not self.roots():
            return
        self.pending = True
        self.timer.start(QUIET_MS)
        if self.task is None:
            from task_dashboard import task_hub
            self.hub = task_hub(self.controller)
            if self.hub:
                self.task = self.hub.start('History refresh request', self.pause, state='Queued', background=True)
        if self.hub and self.task is not None:
            self.hub.update(self.task, 'Source change noticed; waiting for 30 seconds of quiet before validated import.')
        self.controller.changed.emit()

    def clear_pending(self, state='Dispatched', detail='Queued update handed to the history refresh worker.'):
        self.timer.stop()
        self.pending = False
        if self.hub and self.task is not None:
            self.hub.finish(self.task, state, detail)
        self.task = None

    def cancel_pending(self):
        queue=getattr(self.controller.parent(),'background_queue',None)
        if queue and 'history' in queue.pending:queue.cancel('history')
        self.clear_pending('Cancelled', 'Queued history update cancelled.')
        self.controller.changed.emit()

    def dispatch(self):
        if not self.pending:
            return
        if not self.enabled() or self.paused:
            self.cancel_pending()
            return
        queue=getattr(self.controller.parent(),'background_queue',None)
        if queue and self.pending and self.enabled() and not self.paused:
            self.clear_pending()
            self.controller.start(automatic=True)
            return
        other_work = self.hub and any(key != self.task and row['ended'] is None for key, row in self.hub.rows.items())
        if self.controller.busy or self.controller._reports or other_work:
            self.timer.start(5000)
            if self.hub and self.task is not None:
                self.hub.update(self.task, 'Waiting for current app work to retire before refreshing.')
            return
        self.clear_pending()
        self.controller.start(automatic=True)

    def resume(self):
        self.paused = False
        self.clear_pending()
        self.arm()

    def accept_result(self, result):
        if result.get('cancelled') or result.get('failed') or result.get('errors') or result.get('username_history', {}).get('errors'):
            self.paused = True
            self.message = 'Folder watching paused after cancellation/errors. Refresh history now to retry; existing evidence is preserved.'
            self.cancel_pending()
        else:
            self.arm(result.get('watch_paths', self.observed))
        self.controller.changed.emit()

    def pause(self):
        self.paused = True
        self.message = 'Folder watching paused. Refresh history now to resume.'
        self.cancel_pending()

    def shutdown(self):
        self.closed = True
        self.cancel_pending()
        self.arm()
