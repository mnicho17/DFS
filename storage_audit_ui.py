"""Explicit storage audit, run through the existing worker queue and task dashboard."""
import threading
from PyQt5 import QtCore,QtWidgets
from storage_audit import audit,configured_roots,render


def archive_summary(a):
    return (f"Legacy archives: {a['archived']} verified copies; {a['preserved']} preserved/skipped; "
            f"{a['errors']} validation failures. Freed: {a['reclaimed_files']} working copies "
            f"({a['reclaimed_bytes']/1024**2:.2f} MiB). Complete: {a['complete']}. Recovery archives retained.")


def task_text(*args):
    if len(args)==3:return str(args[2])
    result=args[0]
    if result.get('archive'):return archive_summary(result['archive'])
    return result.get('error') or result.get('report',{}).get('state','Storage operation retired.')


class AuditWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(int,int,str)
    finished=QtCore.pyqtSignal(dict)
    def __init__(self,roots,archive_destination=None):
        super().__init__();self.roots=roots;self.archive_destination=archive_destination;self.stop=threading.Event()
    def request_cancel(self):self.stop.set()
    @QtCore.pyqtSlot()
    def run(self):
        try:
            archive=None
            if self.archive_destination:
                from legacy_scenario_archive import validate_and_archive
                folder=next(r['path'] for r in self.roots if r['role']=='scenario')
                protected=[r['path'] for r in self.roots if r['role'] not in ('scenario','backup')]
                archive=validate_and_archive(folder,self.archive_destination,protected=protected,
                    cancelled=self.stop.is_set,progress=lambda text:self.progress.emit(0,0,text),reclaim=True)
            report=audit(self.roots,cancelled=self.stop.is_set,progress=lambda text:self.progress.emit(0,0,text))
            self.finished.emit(dict(report=report,archive=archive,errors=archive['errors'] if archive else 0,cancelled=report['cancelled'] or bool(archive and archive['cancelled']),complete=report['complete'] and (archive is None or archive['complete'])))
        except Exception:
            self.finished.emit(dict(failed=True,error='Operation stopped. Only verified archived legacy copies may have been retired; recovery archives remain in the backup folder.'))


class StorageAuditDialog(QtWidgets.QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.thread=self.worker=self.pending=None
        self.archive_result=None;self.archive_destination=None;self.report=None;self.queued=self.closing=self.yielded=False;self.key='storage-audit-'+str(id(self))
        self.setWindowTitle('Storage audit and legacy archives');self.resize(1050,650)
        box=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Observe configured history, source, library, cache and backup locations. Scanning is read-only. Archive and free space verifies recoverable legacy-cache copies in your backup folder before removing their working copies. Research and prepared libraries stay protected. '
            'Age does not establish last use. Backup integrity and research dependencies are not verified by this inventory.')
        note.setWordWrap(True);box.addWidget(note)
        self.status=QtWidgets.QLabel('Waiting to scan…');self.status.setWordWrap(True);box.addWidget(self.status)
        self.table=QtWidgets.QTreeWidget();self.table.setHeaderLabels(['MiB','Preservation guidance','File']);box.addWidget(self.table,1)
        self.table.setColumnWidth(0,90);self.table.setColumnWidth(1,240)
        self.details=QtWidgets.QPlainTextEdit();self.details.setReadOnly(True);box.addWidget(self.details,1)
        self.table.currentItemChanged.connect(self.selection)
        row=QtWidgets.QHBoxLayout();self.refresh=QtWidgets.QPushButton('Scan again');self.refresh.clicked.connect(self.scan)
        self.copy=QtWidgets.QPushButton('Copy local report');self.copy.setEnabled(False);self.copy.setToolTip('Includes local file paths; review before sharing.');self.copy.clicked.connect(self.copy_report)
        self.stop_button=QtWidgets.QPushButton('Stop task');self.stop_button.clicked.connect(self.cancel)
        self.archive_button=QtWidgets.QPushButton('Archive and free legacy cache space');self.archive_button.clicked.connect(self.archive)
        close=QtWidgets.QPushButton('Close');close.clicked.connect(self.reject)
        for button in (self.refresh,self.copy,self.archive_button,self.stop_button,close):row.addWidget(button)
        box.addLayout(row)
        from task_dashboard import add_task_button
        add_task_button(self,box)
        QtCore.QTimer.singleShot(0,self.scan)

    def archive(self):
        if self.thread is not None or self.queued or self.closing:return
        backup=getattr(self.owner,'idle_backup',None)
        destination=backup.destination() if backup else None
        if not destination:
            self.status.setText('Configure an available backup folder in Backups first.');return
        self.archive_destination=destination
        self.scan()

    def scan(self):
        if self.thread is not None or self.queued or self.closing:return
        self.roots=configured_roots(self.owner);self.queued=True;self.yielded=False
        self.report=None;self.archive_result=None;self.copy.setEnabled(False);self.table.clear();self.details.clear()
        self.refresh.setEnabled(False);self.archive_button.setEnabled(False);self.stop_button.setEnabled(True)
        self.status.setText('Queued until current manual work and history operations retire.')
        queue=getattr(self.owner,'background_queue',None)
        if queue:
            queue.request(self.key,'Legacy cache archive' if self.archive_destination else 'Read-only storage audit',5,self.start,self.yield_work,
                ready=self.ready,on_cancel=self.queue_cancelled)
            queue.pump()
        elif self.ready():self.start()
        else:self.queue_cancelled();self.status.setText('Current work is active; retry after it finishes.')

    def ready(self):
        history=getattr(self.owner,'history_refresh',None)
        return not self.closing and not (history and (history.busy or history._reports))

    def start(self):
        self.queued=False;self.pending=None
        self.worker=AuditWorker(self.roots,self.archive_destination);self.thread=QtCore.QThread(self)
        self.worker.moveToThread(self.thread);self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress);self.worker.finished.connect(self.receive)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        history=getattr(self.owner,'history_refresh',None)
        if history:history.report_started()
        from task_dashboard import watch_task
        watch_task(self.owner,'Legacy cache archive' if self.archive_destination else 'Read-only storage audit',self.worker,self.thread,self.cancel,formatter=task_text)
        self.status.setText('Validating legacy caches and recovery copies before freeing working space.' if self.archive_destination else 'Scanning metadata in a worker; file contents and restore integrity are not being verified.')
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
        else:self.refresh.setEnabled(True);self.archive_button.setEnabled(True);self.archive_destination=None;self.stop_button.setEnabled(False)
    def cancel(self):
        queue=getattr(self.owner,'background_queue',None)
        if self.queued and queue:queue.cancel(self.key)
        elif self.worker:self.worker.request_cancel()
        self.status.setText('Stop requested; completed recovery archives remain available and results may be partial.')

    def retire(self):
        payload=self.pending or dict(failed=True,error='Audit worker exited without a result.')
        thread=self.thread;self.thread=self.worker=None;self.pending=None;thread.deleteLater()
        history=getattr(self.owner,'history_refresh',None)
        if history:history.report_finished(resume=not self.closing)
        queue=getattr(self.owner,'background_queue',None)
        if queue:queue.release(self.key)
        self.refresh.setEnabled(True);self.archive_button.setEnabled(True);self.archive_destination=None;self.stop_button.setEnabled(False)
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
        if payload.get('archive'):
            a=self.archive_result=payload['archive'];self.status.setText(archive_summary(a))
            self.details.appendPlainText('Archive folder: '+a['archive_root']+'\n'+'\n'.join(a['messages']))
        if self.closing:super().reject()
        if getattr(self.owner,'_close_after_storage_audit',False):QtCore.QTimer.singleShot(0,self.owner.close)

    def selection(self,item,*_):
        if item:
            row=item.data(0,QtCore.Qt.UserRole)
            self.details.setPlainText('\n'.join([row['path'],row['policy'],'Modified: '+row['modified_utc'],
                'Modified over 30 days ago: '+str(row['older_than_30_days'])+' (not last use)',
                'Last use: '+row['last_use'],'Cache format hint: '+row['cache_format_hint'],row['rebuild'],row['dependencies'],'Backup: '+row['backup']]))
    def copy_report(self):
        if self.report:
            text=render(self.report)
            if self.archive_result:text+='\n\n'+archive_summary(self.archive_result)+'\nArchive folder: '+self.archive_result['archive_root']+'\n'+'\n'.join(self.archive_result['messages'])
            QtWidgets.QApplication.clipboard().setText(text)
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
