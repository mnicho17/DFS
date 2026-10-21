"""One cooperative queue for automatic work; leases end only on worker retirement."""
from PyQt5 import QtCore, QtWidgets

HISTORY_PRIORITY = 10
BACKUP_PRIORITY = 20
PREPARATION_PRIORITY = 30


class BackgroundQueue(QtCore.QObject):
    changed = QtCore.pyqtSignal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner, self.hub = owner, owner.tasks
        self.pending = {}
        self.active = None
        self.closed = self.dispatching = False
        self.checking = None
        self.serial = 0
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.pump)
        self.timer.start()
        self.hub.changed.connect(self.wake)

    def wake(self):
        if not self.dispatching and not self.closed:
            QtCore.QTimer.singleShot(0, self.pump)

    def request(self, key, title, priority, start, cancel, *, ready=lambda: True,
                detail='Waiting for current work to retire.', idle_only=False, on_cancel=None):
        if self.closed or (self.active and self.active['key'] == key):
            return False
        if key in self.pending:
            return True
        self.serial += 1
        self.dispatching = True
        try:
            task = self.hub.start(title, lambda: self.cancel(key), state='Queued', background=True)
            self.hub.update(task, detail)
            self.pending[key] = dict(key=key, title=title, priority=priority,
                start=start, cancel=cancel, ready=ready, task=task, order=self.serial,
                owned=set(), yielding=False, run_now=False, idle_only=idle_only, on_cancel=on_cancel or cancel)
        finally:
            self.dispatching = False
        self.changed.emit()
        self.wake()
        return True

    def is_ready(self, job):
        self.checking = job
        try:
            return job['ready']()
        finally:
            self.checking = None

    def idle_override(self, key=None):
        job = self.checking or self.active
        return bool(job and job.get('run_now') and (key is None or job['key'] == key))

    def run_now(self, task):
        job = next((j for j in self.pending.values() if j['task'] == task), None)
        if self.closed or job is None:
            return False
        job['run_now'] = True
        self.hub.update(task, 'Run now requested; waiting only for safety checks and higher-priority work to retire.')
        self.wake()
        return True

    def foreground(self):
        owned = self.active['owned'] if self.active else set()
        return any(k not in owned and r['ended'] is None and r['state'] != 'Queued'
                   for k, r in self.hub.rows.items()) or any(
            self.running(getattr(self.owner, name, None)) for name in ('_build_thread', '_own_thread'))

    @staticmethod
    def running(thread):
        try: return thread is not None and thread.isRunning()
        except RuntimeError: return False

    def pump(self):
        if self.closed or self.dispatching:
            return
        self.dispatching = True
        try:
            ready = sorted((j for j in self.pending.values() if self.is_ready(j)),
                           key=lambda j: (j['priority'], j['order']))
            blocked = self.foreground()
            if self.active:
                job = self.active
                if job['cancel'] and not job['yielding'] and (blocked or
                        (ready and ready[0]['priority'] < job['priority']) or
                        (job['idle_only'] and not self.is_ready(job))):
                    job['yielding'] = True
                    job['cancel']()
                    self.changed.emit()
                return
            if blocked or not ready:
                return
            job = ready[0]
            self.pending.pop(job['key'])
            self.hub.finish(job['task'], 'Dispatched', 'Starting queued work; waiting for worker retirement before the next job.')
            self.active = job
            before = set(self.hub.rows)
            try:
                started = job['start']()
            except Exception as exc:
                self.hub.rows[job['task']].update(state='Failed', detail=str(exc))
                self.active = None
                self.hub.changed.emit()
                return
            job['owned'] = set(self.hub.rows) - before
            if started is False:
                self.active = None
            self.changed.emit()
        finally:
            self.dispatching = False

    def release(self, key):
        if self.active and self.active['key'] == key:
            self.active = None
            self.changed.emit()
            self.wake()

    def cancel(self, key):
        job = self.pending.pop(key, None)
        if job:
            self.hub.finish(job['task'], 'Cancelled', 'Queued work cancelled; no worker was started.')
        elif self.active and self.active['key'] == key:
            job = self.active
            job['yielding'] = True
        if job and job['on_cancel']:
            job['on_cancel']()
        self.changed.emit()

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        for key in list(self.pending): self.cancel(key)
        if self.active and self.active['cancel'] and not self.active['yielding']:
            self.active['yielding'] = True
            self.active['cancel']()

    def summary(self):
        running = self.active['title'] if self.active else 'No queued worker running'
        waiting = sorted(self.pending.values(), key=lambda j: (j['priority'], j['order']))
        return running + ('; next: ' + ', '.join(j['title'] for j in waiting) if waiting else '')


def idle_ready(owner, key=None):
    """Explicit dispatch bypasses inactivity only; controller safety gates still apply."""
    queue = getattr(owner, 'background_queue', None)
    if queue and queue.idle_override(key):
        return True
    if QtWidgets.QApplication.activeModalWidget() is not None:
        return False
    if owner.isMinimized():
        return True
    backup = owner.idle_backup
    return backup.clock() - backup.last_input >= 60
