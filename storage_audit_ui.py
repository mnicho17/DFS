"""Explicit storage audit, run through the existing worker queue and task dashboard."""
import threading
from PyQt5 import QtCore,QtWidgets
from storage_audit import audit,configured_roots,render


class AuditWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(int,int,str)
    finished=QtCore.pyqtSignal(dict)
    def __init__(self,roots):
        super().__init__();self.roots=roots;self.stop=threading.Event()
    def request_cancel(self):self.stop.set()
    @QtCore.pyqtSlot()
    def run(self):
        try:
            report=audit(self.roots,cancelled=self.stop.is_set,progress=lambda text:self.progress.emit(0,0,text))
            self.finished.emit(dict(report=report,cancelled=report['cancelled'],complete=report['complete']))
        except Exception:
            self.finished.emit(dict(failed=True,error='Storage audit unavailable; no files were changed.'))


class StorageAuditDialog(QtWidgets.QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.thread=self.worker=self.pending=None
        self.report=None;self.queued=self.closing=self.yielded=False;self.key='storage-audit-'+str(id(self))
        self.setWindowTitle('Storage audit — read-only');self.resize(1050,650)
        box=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Observe configured history, source, library, cache and backup locations. No files are deleted, moved or archived. '
            'Age does not establish last use. Backup integrity and research dependencies are not verified by this inventory.')
        note.setWordWrap(True);box.addWidget(note)
        self.status=QtWidgets.QLabel('Waiting to scan…');self.status.setWordWrap(True);box.addWidget(self.status)
        self.table=QtWidgets.QTreeWidget();self.table.setHeaderLabels(['MiB','Preservation guidance','File']);box.addWidget(self.table,1)
        self.table.setColumnWidth(0,90);self.table.setColumnWidth(1,240)
        self.details=QtWidgets.QPlainTextEdit();self.details.setReadOnly(True);box.addWidget(self.details,1)
        self.table.currentItemChanged.connect(self.selection)
        row=QtWidgets.QHBoxLayout();self.refresh=QtWidgets.QPushButton('Scan again');self.refresh.clicked.connect(self.scan)
        self.copy=QtWidgets.QPushButton('Copy local report');self.copy.setEnabled(False);self.copy.setToolTip('Includes local file paths; review before sharing.');self.copy.clicked.connect(self.copy_report)
        self.stop_button=QtWidgets.QPushButton('Cancel scan');self.stop_button.clicked.connect(self.cancel)
        close=QtWidgets.QPushButton('Close');close.clicked.connect(self.reject)
        for button in (self.refresh,self.copy,self.stop_button,close):row.addWidget(button)
        box.addLayout(row)
        from task_dashboard import add_task_button
        add_task_button(self,box)
        QtCore.QTimer.singleShot(0,self.scan)

    def scan(self):
        if self.thread is not None or self.queued or self.closing:return
        self.roots=configured_roots(self.owner);self.queued=True;self.yielded=False
        self.report=None;self.copy.setEnabled(False);self.table.clear();self.details.clear()
        self.refresh.setEnabled(False);self.stop_button.setEnabled(True)
        self.status.setText('Queued until current manual work and history operations retire.')
        queue=getattr(self.owner,'background_queue',None)
        if queue:
            queue.request(self.key,'Read-only storage audit',5,self.start,self.yield_work,
                ready=self.ready,on_cancel=self.queue_cancelled)
            queue.pump()
        elif self.ready():self.start()
        else:self.queue_cancelled();self.status.setText('Current work is active; retry after it finishes.')

    def ready(self):
        history=getattr(self.owner,'history_refresh',None)
        return not self.closing and not (history and (history.busy or history._reports))

    def start(self):
        self.queued=False;self.pending=None
        self.worker=AuditWorker(self.roots);self.thread=QtCore.QThread(self)
        self.worker.moveToThread(self.thread);self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress);self.worker.finished.connect(self.receive)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        history=getattr(self.owner,'history_refresh',None)
        if history:history.report_started()
        from task_dashboard import watch_task
        watch_task(self.owner,'Read-only storage audit',self.worker,self.thread,self.cancel)
        self.status.setText('Scanning metadata in a worker; file contents and restore integrity are not being verified.')
        self.thread.start();return True

    def progress(self,done,total,text):self.status.setText(text)
    def receive(self,payload):
        if self.yielded:payload['background_yield']=True
        self.pending=payload
    def yield_work(self):
        self.yielded=True
        if self.worker:self.worker.request_cancel()
    def queue_cancelled(self):
        self.queued=False
        if self.worker:self.worker.request_cancel()
        else:self.refresh.setEnabled(True);self.stop_button.setEnabled(False)
    def cancel(self):
        queue=getattr(self.owner,'background_queue',None)
        if self.queued and queue:queue.cancel(self.key)
        elif self.worker:self.worker.request_cancel()
        self.status.setText('Stop requested; any observed inventory is partial.')

    def retire(self):
        payload=self.pending or dict(failed=True,error='Audit worker exited without a result.')
        thread=self.thread;self.thread=self.worker=None;self.pending=None;thread.deleteLater()
        history=getattr(self.owner,'history_refresh',None)
        if history:history.report_finished(resume=not self.closing)
        queue=getattr(self.owner,'background_queue',None)
        if queue:queue.release(self.key)
        self.refresh.setEnabled(True);self.stop_button.setEnabled(False)
        if payload.get('report'):
            self.report=payload['report'];self.table.clear();self.table.setUpdatesEnabled(False)
            for row in self.report['rows']:
                item=QtWidgets.QTreeWidgetItem([f"{row['bytes']/1024**2:.2f}",row['policy'],row['path']])
                item.setData(0,QtCore.Qt.UserRole,row);self.table.addTopLevelItem(item)
            self.table.setUpdatesEnabled(True);self.copy.setEnabled(True)
            self.status.setText(f"{self.report['state']}: {self.report['files']:,} observed files; {self.report['bytes']/1024**3:.3f} GiB. "
                                f"{self.report['rows_omitted']:,} smaller files omitted from the table. No cleanup is authorized.")
            self.details.setPlainText(render(self.report).split('\n\n',1)[0]+'\n'+'\n'.join(self.report['issues']))
        else:self.status.setText(payload.get('error','Audit unavailable.'))
        if self.closing:super().reject()
        if getattr(self.owner,'_close_after_storage_audit',False):QtCore.QTimer.singleShot(0,self.owner.close)

    def selection(self,item,*_):
        if item:
            row=item.data(0,QtCore.Qt.UserRole)
            self.details.setPlainText('\n'.join([row['path'],row['policy'],'Modified: '+row['modified_utc'],
                'Modified over 30 days ago: '+str(row['older_than_30_days'])+' (not last use)',
                'Last use: '+row['last_use'],'Cache format hint: '+row['cache_format_hint'],row['rebuild'],row['dependencies'],'Backup: '+row['backup']]))
    def copy_report(self):
        if self.report:QtWidgets.QApplication.clipboard().setText(render(self.report))
    def shutdown(self):
        self.closing=True;self.cancel();return self.thread is not None
    def reject(self):
        if self.thread is not None:self.closing=True;self.cancel()
        else:
            self.closing=True;self.cancel();super().reject()


def open_storage_audit(window):
    current=getattr(window,'_storage_audit_dialog',None)
    if current is not None:current.raise_();return
    dialog=window._storage_audit_dialog=StorageAuditDialog(window)
    try:dialog.exec_()
    finally:window._storage_audit_dialog=None;dialog.deleteLater()
