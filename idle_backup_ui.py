"""Idle-time, cooperative history backups with visible outcomes and safe retirement."""
import os
import threading
import time
from pathlib import Path
from PyQt5 import QtCore, QtWidgets

IDLE_SECONDS=60
QUIET_SECONDS=30
RECHECK_SECONDS=15*60


class BackupWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(int,int,str)
    finished=QtCore.pyqtSignal(dict)
    error=QtCore.pyqtSignal(str)

    def __init__(self, source, destination, verify=False):
        super().__init__();self.source,self.destination,self.verify=source,destination,verify
        self.stop=threading.Event()

    def request_cancel(self):self.stop.set()

    @QtCore.pyqtSlot()
    def run(self):
        from history_backup import backup_history, BackupCancelled, HistoryChanged
        paths=[]
        try:
            result=backup_history(self.source,self.destination,live=True,skip_if_current=True,
                verify_existing=self.verify,cancelled=self.stop.is_set,observed_paths=paths,
                progress=lambda text:self.progress.emit(0,0,text))
            self.finished.emit(dict(result,watch_paths=paths))
        except (BackupCancelled,HistoryChanged):
            self.finished.emit(dict(cancelled=True,pause_reason='Backup paused; no new manifest published. Earlier backups remain available.'))
        except Exception as exc:self.finished.emit(dict(error=str(exc),failed=True))


class IdleBackupController(QtCore.QObject):
    changed=QtCore.pyqtSignal()

    def __init__(self, owner, settings, source, *, clock=time.monotonic):
        super().__init__(owner)
        self.owner,self.settings,self.source,self.clock=owner,settings,Path(source).resolve(),clock
        self.thread=self.worker=self.pending=self.key=self.dialog=None
        self.closed=self.paused=self.manual=self.verify=self.preempted=self.starting=False
        self.dirty=True;self.revision=0
        self.last_input=self.last_change=clock();self.next_attempt=0;self.next_check=clock()+RECHECK_SECONDS
        self.state='Changes pending' if self.destination() else 'Not configured'
        self.message='Waiting for an idle check.' if self.destination() else 'Choose a backup folder, or use the folder supplied by your launcher.'
        self.watcher=QtCore.QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.source_changed);self.watcher.directoryChanged.connect(self.source_changed)
        self.timer=QtCore.QTimer(self);self.timer.setInterval(15000);self.timer.timeout.connect(self.tick);self.timer.start()
        self.owner.tasks.changed.connect(self.other_work_changed)
        app=QtWidgets.QApplication.instance()
        if app:app.installEventFilter(self)
        self.arm();self.publish()

    def destination(self):
        return str(self.settings.value('backup/destination',os.environ.get('DFS_BACKUP_ROOT','')) or '').strip()

    def enabled(self):
        return str(self.settings.value('backup/idle_enabled','true')).lower() in ('true','1')

    def publish(self):
        label=getattr(self.owner,'backup_summary',None)
        if label:
            label.setText('Backups: '+self.state);label.setToolTip(self.message)
        self.changed.emit()

    def set_status(self,state,message):
        if (self.state,self.message)!=(state,message):
            self.state,self.message=state,message;self.publish()

    def eventFilter(self, obj, event):
        if event.type() in (QtCore.QEvent.KeyPress,QtCore.QEvent.MouseButtonPress,QtCore.QEvent.Wheel,QtCore.QEvent.TouchBegin,QtCore.QEvent.MouseMove,QtCore.QEvent.MouseButtonDblClick):
            self.last_input=self.clock()
        return False

    def arm(self, paths=()):
        previous=self.watcher.files()+self.watcher.directories()
        if previous:self.watcher.removePaths(previous)
        if self.closed:return
        candidates=list(paths) or [str(self.source if self.source.is_dir() else self.source.parent)]
        self.watcher.addPaths(candidates[:4096])

    def source_changed(self,*_):
        if self.closed:return
        self.revision+=1;self.dirty=True;self.last_change=self.clock()
        if self.thread is None and not self.paused and self.enabled() and self.state!='Needs attention':
            self.set_status('Changes pending','History changed; waiting for quiet files and idle app time.')

    def busy(self):
        history=self.owner.history_refresh
        if history.busy or history._reports:return True
        if any(k!=self.key and row['ended'] is None and row['state']!='Queued' for k,row in self.owner.tasks.rows.items()):return True
        for name in ('_build_thread','_own_thread'):
            thread=getattr(self.owner,name,None)
            try:
                if thread is not None and thread.isRunning():return True
            except RuntimeError:pass
        modal=QtWidgets.QApplication.activeModalWidget()
        queue=getattr(self.owner,'background_queue',None)
        return modal is not None and modal is not self.dialog and not (queue and queue.idle_override('backup'))

    def other_work_changed(self):
        if self.starting or self.worker is None:return
        other=any(k!=self.key and row['ended'] is None and row['state']!='Queued' for k,row in self.owner.tasks.rows.items())
        if other:
            self.preempted=True;self.worker.request_cancel()
            self.set_status('Yielding','Pausing backup for current app work; waiting for worker retirement.')

    def tick(self):
        if self.closed or self.thread is not None:return
        now=self.clock()
        if not self.destination():
            self.set_status('Not configured','Choose a backup folder, or use the launcher backup folder.');return
        if self.paused or (not self.enabled() and not self.manual):return
        if not self.manual and (now<self.next_attempt or (not self.dirty and now<self.next_check)):return
        queue=getattr(self.owner,'background_queue',None)
        lower=bool(queue and queue.active and queue.active['priority']>20)
        if self.busy() and not lower:
            if self.manual:self.set_status('Queued','Backup queued until current app work retires.')
            if not queue:return
        from background_queue import idle_ready
        if not self.manual and (not idle_ready(self.owner,'backup') or now-self.last_change<QUIET_SECONDS):return
        queue=getattr(self.owner,'background_queue',None)
        if queue:
            from background_queue import BACKUP_PRIORITY
            queue.request('backup','History backup',BACKUP_PRIORITY,self.start,self.yield_work,
                ready=lambda:not self.closed and bool(self.destination()) and not self.paused and (self.manual or self.enabled()) and (self.manual or (idle_ready(self.owner,'backup') and self.clock()-self.last_change>=QUIET_SECONDS)) and (not self.busy() or bool(queue.active and (queue.active['key']=='backup' or queue.active['priority']>20))),
                idle_only=not self.manual, on_cancel=self.cancel,
                detail='Waiting for idle time and higher-priority history work.')
            queue.pump()
            if 'backup' in queue.pending:self.set_status('Queued','Backup queued; waiting for idle time and higher-priority work to retire.')
        else:self.start()

    def yield_work(self):
        if self.worker:
            self.preempted=True;self.worker.request_cancel()
            self.set_status('Yielding','Pausing backup at a safe boundary for other app work.')

    def request(self, verify=False):
        if self.closed:return
        self.paused=False;self.manual=True;self.verify=verify;self.next_attempt=0;self.dirty=True
        queue=getattr(self.owner,'background_queue',None)
        if queue and 'backup' in queue.pending:
            queue.pending['backup']['idle_only']=False
        self.tick()
        queue=getattr(self.owner,'background_queue',None)
        if queue:queue.pump()

    def configure(self):
        self.paused=False;self.dirty=True;self.last_change=self.clock();self.next_attempt=0
        self.set_status('Changes pending' if self.enabled() else 'Off','Backup preferences updated. Existing backups remain unchanged.')

    def start(self):
        self.start_revision=self.revision;self.preempted=False;self.pending=None
        self.manual=False
        self.worker=BackupWorker(self.source,self.destination(),self.verify);self.verify=False
        self.thread=QtCore.QThread(self);self.worker.moveToThread(self.thread)
        self.owner.history_refresh.report_started()
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        from task_dashboard import watch_task
        self.starting=True
        binding=watch_task(self.owner,'Incremental history backup',self.worker,self.thread,self.cancel)
        self.key=binding.key if binding else None;self.starting=False
        self.set_status('Running','Checking history and preparing verified incremental content…')
        self.thread.start()
        return True

    def progress(self,a,b,text):
        if self.worker is not None and not self.worker.stop.is_set():self.set_status('Running',text)

    def receive(self,result):self.pending=result

    def cancel(self):
        self.paused=True;self.manual=False;self.verify=False
        queue=getattr(self.owner,'background_queue',None)
        if queue and 'backup' in queue.pending:queue.cancel('backup')
        if self.worker:self.worker.request_cancel()
        self.set_status('Paused','Backup paused for this session. Back up now resumes; previous complete manifests are preserved.')

    def retire(self):
        result=self.pending or dict(failed=True,error='Backup worker exited without a result.')
        stopped=self.worker.stop.is_set();thread=self.thread
        self.thread=self.worker=None;self.key=None;self.pending=None;thread.deleteLater()
        self.owner.history_refresh.report_finished(resume=not self.closed)
        now=self.clock()
        if (stopped or result.get('cancelled')) and not result.get('manifest'):
            self.dirty=True;self.last_change=now
            if not self.paused:self.set_status('Changes pending','Backup yielded safely. It will retry after app work finishes and files settle.')
        elif result.get('error'):
            self.dirty=True;self.next_attempt=now+RECHECK_SECONDS
            self.set_status('Needs attention',result['error']+' Earlier backups remain unchanged. Automatic retry waits 15 minutes; Back up now retries explicitly.')
        else:
            self.dirty=self.revision!=self.start_revision;self.next_check=now+RECHECK_SECONDS
            self.settings.setValue('backup/last_manifest',result['manifest'])
            stamp=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.ISODate)
            self.settings.setValue('backup/last_success',stamp)
            self.arm(result.get('watch_paths',()))
            self.set_status('Paused' if self.paused else 'Changes pending' if self.dirty else 'Recorded current',
                ('A complete manifest was retained before the stop; automatic backups remain paused. ' if self.paused else '')
                + f"Last checked {stamp}. {result.get('copied',0)} new objects; {result.get('skipped_unchanged',0)} unchanged files skipped. "
                f"Manifest: {result['manifest']}. Full object verification remains a separate action; restores always verify content.")
        if self.closed and getattr(self.owner,'_close_after_backup',False):QtCore.QTimer.singleShot(0,self.owner.close)
        queue=getattr(self.owner,'background_queue',None)
        if queue:queue.release('backup')
        self.publish()

    def shutdown(self):
        self.closed=True;self.manual=False;self.timer.stop();self.arm()
        app=QtWidgets.QApplication.instance()
        if app:app.removeEventFilter(self)
        if self.worker:self.worker.request_cancel()
        return self.thread is not None


class BackupsDialog(QtWidgets.QDialog):
    def __init__(self, controller, parent):
        super().__init__(parent);self.controller=controller
        self.setWindowTitle('History backups');self.resize(780,380)
        box=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Idle backups use the existing incremental store and verified new objects. SQLite databases are copied through consistent snapshots. '
            'Earlier manifests stay recoverable; no backup retention/deletion is enabled. The launcher retains its final post-exit check.')
        note.setWordWrap(True);box.addWidget(note)
        source=QtWidgets.QLabel('History source: '+str(controller.source))
        source.setTextFormat(QtCore.Qt.PlainText);source.setWordWrap(True);box.addWidget(source)
        self.destination=QtWidgets.QLineEdit(controller.destination());self.destination.setReadOnly(True);box.addWidget(self.destination)
        self.choose=QtWidgets.QPushButton('Choose backup folder…');self.choose.clicked.connect(self.choose_folder);box.addWidget(self.choose)
        self.enabled=QtWidgets.QCheckBox('Back up automatically when the app is idle');self.enabled.setChecked(controller.enabled())
        self.enabled.toggled.connect(self.toggle);box.addWidget(self.enabled)
        self.status=QtWidgets.QPlainTextEdit();self.status.setReadOnly(True);box.addWidget(self.status,1)
        row=QtWidgets.QHBoxLayout()
        self.now=QtWidgets.QPushButton('Back up now');self.now.clicked.connect(lambda:controller.request())
        self.verify=QtWidgets.QPushButton('Verify current backup');self.verify.clicked.connect(lambda:controller.request(True))
        self.cancel=QtWidgets.QPushButton('Pause backup');self.cancel.clicked.connect(controller.cancel)
        for button in (self.now,self.verify,self.cancel):row.addWidget(button)
        box.addLayout(row)
        from task_dashboard import add_task_button
        add_task_button(self,box)
        close=QtWidgets.QPushButton('Close');close.clicked.connect(self.accept);box.addWidget(close)
        controller.changed.connect(self.refresh);self.refresh()

    def refresh(self):
        c=self.controller;self.status.setPlainText(c.state+'\n'+c.message)
        for control in (self.choose,self.enabled,self.now,self.verify):control.setEnabled(c.thread is None)
        self.cancel.setEnabled(c.thread is not None or c.manual)

    def choose_folder(self):
        folder=QtWidgets.QFileDialog.getExistingDirectory(self,'Choose backup folder',self.destination.text())
        if folder:
            self.controller.settings.setValue('backup/destination',folder)
            self.destination.setText(folder);self.controller.configure()

    def toggle(self,value):
        self.controller.settings.setValue('backup/idle_enabled',bool(value));self.controller.configure()


def open_backups(window):
    c=window.idle_backup
    if c.dialog is not None:c.dialog.raise_();return
    dialog=c.dialog=BackupsDialog(c,window)
    try:dialog.exec_()
    finally:c.dialog=None;dialog.deleteLater()
