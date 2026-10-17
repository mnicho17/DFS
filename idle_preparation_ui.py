"""Opt-in, session-only preparation using frozen inputs and existing checkpoints."""
import copy
import time
from PyQt5 import QtCore, QtWidgets
from background_queue import PREPARATION_PRIORITY

class IdlePreparationController(QtCore.QObject):
    changed = QtCore.pyqtSignal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.worker = self.thread = None
        self.request_data = self.pending = None
        self.closed = self.paused = self.yielded = False
        self.remaining = 0
        self.state = 'Not requested'
        self.message = 'Queue a captured Showdown preparation job from Prepare Showdown Roster Library. Jobs are session-only.'

    def publish(self, state, message):
        self.state, self.message = state, message
        self.changed.emit()

    def ready(self):
        backup = self.owner.idle_backup
        modal = QtWidgets.QApplication.activeModalWidget()
        return (not self.closed and not self.paused and modal is None
                and backup.clock()-backup.last_input >= 60
                and (not self.owner.history_refresh._reports or bool(self.owner.background_queue.active)))

    def enqueue(self, path, snapshot, seconds, limit, scope, storage_bytes, screen, full_screen):
        if self.closed or self.request_data is not None:
            return False
        self.request_data = copy.deepcopy(dict(path=str(path), snapshot=snapshot, limit=limit,
            scope=scope, storage_bytes=storage_bytes, screen=screen, full_screen=full_screen))
        self.remaining = seconds
        self.paused = False
        self.queue()
        return True

    def queue(self):
        if self.closed or self.paused or self.request_data is None:
            return
        self.owner.background_queue.request('preparation','Idle Showdown preparation',PREPARATION_PRIORITY,
            self.start,self.yield_work,ready=self.ready,idle_only=True,
            detail='Captured inputs; waits for 60 seconds without app input, no modal, history refresh and backups. Cancel this queued row to remove the job.')
        self.publish('Queued', self.request_data['path']+'; captured inputs and limits, session-only. Waiting for idle app time.')

    def start(self):
        from showdown_library_ui import PreparationWorker
        from task_dashboard import watch_task
        data = self.request_data
        snapshot = data['snapshot']
        self.worker = PreparationWorker(data['path'],snapshot['inputs']['players'],
            self.remaining,data['limit'],snapshot['inputs']['recipe'].get('salary_cap',50000),
            data['scope'],snapshot if data['screen'] else None,data['storage_bytes'],data['full_screen'])
        self.pending = None
        self.yielded = False
        self.started = time.monotonic()
        self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.owner.history_refresh.report_started()
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.worker.error.connect(self.failure)
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        watch_task(self.owner,'Idle Showdown preparation / screening',self.worker,self.thread,self.cancel,self.describe)
        self.publish('Running',data['path']+'; working from captured inputs. Current build inputs are unchanged.')
        self.thread.start()
        return True

    @staticmethod
    def describe(result):
        if result.get('error'):return str(result['error'])
        if 'history_verification' in result:return result['history_verification']
        if 'screened' in result:
            return f"{result['screened']:,} screened; "+str(result.get('pause_reason') or ('screening complete' if result.get('screening_complete') else result.get('screening_phase','checkpoint saved')))
        return f"{result.get('saved',0):,} saved rosters; {result.get('checked',0):,} combinations checked. "+str(result.get('pause_reason') or ('roster coverage complete' if result.get('complete') else 'partial coverage'))

    def progress(self, result):
        if not self.worker.stop.is_set():self.publish('Running',self.describe(result))

    def receive(self, result):self.pending = result
    def failure(self, message):self.pending = dict(error=message)

    def yield_work(self):
        # A queued cancellation removes the request; an active yield resumes later.
        if self.worker:
            self.yielded = True
            self.worker.stop.set()
            self.publish('Yielding','Waiting for the existing checkpoint and worker retirement before other queued work starts.')
        else:
            self.request_data = None
            self.publish('Cancelled','Queued preparation cancelled; no worker started.')

    def cancel(self):
        self.paused = True
        queue = self.owner.background_queue
        if 'preparation' in queue.pending:queue.cancel('preparation')
        if self.worker:self.worker.stop.set()
        else:self.request_data = None
        self.publish('Paused','Preparation stopped for this session. Saved checkpoints remain available for explicit resume.')

    def retire(self):
        result = self.pending or dict(error='Worker retired without a result.')
        self.remaining = max(0,self.remaining-(time.monotonic()-self.started))
        thread = self.thread
        self.thread = self.worker = None
        thread.deleteLater()
        self.owner.history_refresh.report_finished(resume=not self.closed)
        self.owner.background_queue.release('preparation')
        complete = result.get('screening_complete') if self.request_data['screen'] else result.get('complete')
        # Only a cooperative priority/idle yield is automatically resumed. A
        # resource limit, exhausted budget, error or user stop requires review.
        reason=str(result.get('pause_reason','')).lower()
        resource_stop = any(word in reason for word in ('storage','progress','incomplete','interrupted')) or (not self.request_data['path'].endswith('.sdfull') and not result.get('complete') and result.get('saved',0)>=self.request_data['limit'])
        retry = not resource_stop and self.yielded and not self.paused and not self.closed and not result.get('error') and self.remaining > 0 and not complete
        self.publish('Queued' if retry else 'Needs attention' if result.get('error') else 'Paused' if not complete else 'Completed',
            self.describe(result)+f'; {self.remaining/60:.1f} minutes remain in the requested time budget. Final SIM and build validation run fresh.')
        if retry:QtCore.QTimer.singleShot(0,self.queue)
        else:self.request_data = None
        if self.closed:QtCore.QTimer.singleShot(0,self.owner.close)

    def shutdown(self):
        self.closed = True
        self.cancel()
        return self.thread is not None
