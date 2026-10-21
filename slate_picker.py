"""Idle catalog discovery and a cached slate picker; no active-slate mutation."""
from pathlib import Path
import threading
import copy
import time
from PyQt5 import QtCore, QtWidgets
from slate_catalog import scan, rows


class DiscoveryWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(dict)

    def __init__(self, database, folder, smart=False, retry_request=None, scenario_snapshot=None):
        super().__init__()
        self.database, self.folder = database, folder
        self.scenario_snapshot=copy.deepcopy(scenario_snapshot)
        self.smart = smart
        self.retry_request = dict(retry_request or {})
        self.pause_reason = 'Waiting for idle time or higher-priority work.'
        self.stop = threading.Event()

    def run(self):
        last=[0.0]
        def notify(text):
            if time.monotonic()-last[0]>=1:
                last[0]=time.monotonic()
                self.progress.emit(text)
        try:
            result = scan(self.database, self.folder, self.stop.is_set, notify)
            if (self.smart or self.retry_request) and not self.stop.is_set():
                from smart_preparation import prepare_one
                from slate_catalog import record_preparation
                result.update(prepare_one(self.database,self.folder,Path(self.database).parent/'candidates',
                    self.stop.is_set,notify,target_digest=self.retry_request.get('digest'),
                    retry=self.retry_request.get('reset',False),scenario_snapshot=self.scenario_snapshot,
                    warm_scenarios=True,pause_reason=lambda:self.pause_reason))
                record_preparation(self.database,result)
        except Exception as exc:
            result = dict(error=str(exc))
        self.finished.emit(result)


class SlateDiscoveryController(QtCore.QObject):
    changed = QtCore.pyqtSignal()

    def __init__(self, owner, database):
        super().__init__(owner)
        self.owner, self.database = owner, str(database)
        self.worker = self.thread = None
        self.closed = self.manual = self.paused = False
        self.retry_request = self.running_retry = None
        self.message = 'Discovery waits for idle time. Browse remains available.'
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(300000)
        self.timer.timeout.connect(self.request)
        self.timer.start()
        QtCore.QTimer.singleShot(0, self.request)

    def folder(self):
        return str(self.owner.app_settings.value('learning/salary_folder', '') or '')

    def request(self, manual=False):
        if self.closed or self.thread or not self.folder() or (self.paused and not manual): return
        self.manual = False if self.retry_request else self.manual or manual
        self.owner.background_queue.request('slate-discovery', 'Discover / prepare salary slates', 35,
            self.start, self.yield_work, ready=self.ready, idle_only=True,
            on_cancel=self.cancel, detail='Catalog salary files and prepare upcoming NFL slates while idle. Unchanged discovery and completed preparation are skipped; current slate stays loaded.')

    def resume(self, row):
        if self.closed or self.thread is not None or row.get('sport')!='NFL' or not self.folder():
            return False
        self.retry_request=dict(digest=row['digest'],folder=self.folder(),reset=True)
        self.manual=self.paused=False
        self.message='Retry/Resume queued. Close this picker; preparation waits for idle time and existing workers to retire.'
        self.request()
        self.changed.emit()
        return True

    def ready(self):
        return not self.closed and (self.manual or self.owner.idle_preparation.ready('slate-discovery'))

    def start(self):
        from task_dashboard import watch_task
        smart=str(self.owner.app_settings.value('build/smart_preparation','true')).lower() in ('true','1')
        self.running_retry=dict(self.retry_request) if self.retry_request else None
        folder=self.running_retry['folder'] if self.running_retry else self.folder()
        captured=None
        if (smart and not self.manual) or self.running_retry:
            try:
                if not getattr(self.owner,'_snapshot_replay',False) and self.owner.chk_nfl_contest_sim.isChecked() and self.owner.combo_nfl_compute_mode.currentText().startswith('Deep'):
                    captured=self.owner._capture_snapshot()
            except (AttributeError,ValueError,TypeError):
                pass
        self.worker = DiscoveryWorker(self.database, folder, smart=smart and not self.manual,
                                      retry_request=self.running_retry,scenario_snapshot=captured)
        self.result = {}
        self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        watch_task(self.owner, 'Salary slate discovery / Smart preparation', self.worker, self.thread,
                   self.cancel, lambda value: (str(value.get('error') or value.get('pause_reason') or value.get('preparation') or
                       f"Discovery complete: {value.get('checked',0)} new/changed; {value.get('reused',0)} unchanged.")
                       if isinstance(value,dict) else str(value)))
        self.thread.start()
        return True

    def progress(self, message):
        self.message = message
        self.changed.emit()

    def receive(self, result):
        self.result = result
        self.message = (str(result['error']) if result.get('error') else
                        'Discovery paused; resumes during idle time.' if result.get('cancelled') else result['preparation'] if result.get('preparation') else
                        f"Discovery complete: {result['checked']} new/changed; {result['reused']} unchanged.")

    def retire(self):
        thread = self.thread
        self.thread = self.worker = None
        thread.deleteLater()
        self.manual = False
        if self.result.get('library') and self.result.get('library_saved',True):
            from build_preparation import remember_library
            remember_library(self.owner.app_settings,self.result['library'])
        retry=self.running_retry
        self.running_retry=None
        self.retry_request=None
        if retry and not self.paused and not self.closed and (self.result.get('state') in ('Queued','Paused') or
                (self.result.get('scenario_preparation') or {}).get('state') in ('Queued','Paused') or
                (self.result.get('cancelled') and not self.result.get('state'))):
            self.retry_request=dict(retry,reset=bool(retry.get('reset') and not self.result.get('retry_consumed')))
        self.owner.background_queue.release('slate-discovery')
        self.changed.emit()
        if self.closed: QtCore.QTimer.singleShot(0, self.owner.close)
        elif self.retry_request: QtCore.QTimer.singleShot(0, self.request)

    def yield_work(self):
        if self.worker:
            self.worker.pause_reason='Waiting for idle time or higher-priority work.'
            self.worker.stop.set()

    def cancel(self):
        self.paused = True
        self.manual = False
        self.retry_request=None
        if self.worker:
            self.worker.pause_reason='App closing; checkpoint retained.' if self.closed else 'Paused by user for this session. Use Retry/Resume to continue.'
            self.worker.stop.set()
        self.message='Preparation paused for this session. Use Retry/Resume for a selected slate.'
        self.changed.emit()

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        self.cancel()
        return self.thread is not None


class SlatePicker(QtWidgets.QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller, self.path = controller, ''
        self.setWindowTitle('Load slate')
        screen=QtWidgets.QApplication.primaryScreen()
        space=screen.availableGeometry() if screen else None
        self.resize(min(880,space.width()-80) if space else 880,
                    min(610,space.height()-100) if space else 610)
        layout = QtWidgets.QVBoxLayout(self)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search teams, sport, slate type, or filename')
        layout.addWidget(self.search)
        self.all_dates = QtWidgets.QCheckBox('All dates (including unknown dates)')
        layout.addWidget(self.all_dates)
        self.smart = QtWidgets.QCheckBox('Prepare upcoming NFL slates automatically while idle (shared 8 GiB library budget)')
        self.smart.setChecked(str(controller.owner.app_settings.value('build/smart_preparation','true')).lower() in ('true','1'))
        self.smart.toggled.connect(self.set_smart)
        layout.addWidget(self.smart)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Slate', 'Game date', 'Preparation', 'File'])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(135)
        self.details.setMaximumHeight(190)
        self.details.setAccessibleName('Selected slate preparation details')
        layout.addWidget(self.details)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Open | QtWidgets.QDialogButtonBox.Cancel)
        browse = buttons.addButton('Browse…', QtWidgets.QDialogButtonBox.ActionRole)
        refresh = buttons.addButton('Refresh list', QtWidgets.QDialogButtonBox.ActionRole)
        self.resume_button=buttons.addButton('Retry / Resume',QtWidgets.QDialogButtonBox.ActionRole)
        self.resume_button.setToolTip('Recheck the selected slate and storage; retain compatible work and grant another bounded attempt. Runs when idle after this picker closes.')
        self.resume_button.clicked.connect(self.resume_selected)
        self.table.itemSelectionChanged.connect(self.update_details)
        layout.addWidget(buttons)
        buttons.accepted.connect(self.open_selected)
        buttons.rejected.connect(self.reject)
        browse.clicked.connect(self.browse)
        refresh.clicked.connect(lambda: controller.request(manual=True))
        self.table.doubleClicked.connect(self.open_selected)
        self.search.textChanged.connect(self.render)
        self.all_dates.toggled.connect(self.render)
        controller.changed.connect(self.render)
        self.render()
        controller.request(manual=True)

    def render(self):
        selected = self.table.currentRow()
        old=self.table.item(selected,0) if selected>=0 else None
        previous=(old.data(QtCore.Qt.UserRole),old.data(QtCore.Qt.UserRole+1).get('digest')) if old else None
        query = self.search.text().strip().casefold()
        try:
            data = rows(self.controller.database, self.controller.folder(), all_dates=self.all_dates.isChecked())
            data = [r for r in data if query in ' '.join(str(v) for v in r.values()).casefold()]
            self.status.setText(self.controller.message if self.controller.folder() else
                'Choose a Salary Files folder in Data and Learning, or use Browse. Default view shows today onward.')
        except Exception as exc:
            data = []
            self.status.setText('Catalog unavailable: '+str(exc)+'. Browse remains available.')
        blocker=QtCore.QSignalBlocker(self.table)
        self.table.clearSelection()
        self.table.setCurrentCell(-1,-1)
        self.table.setRowCount(len(data))
        for index, row in enumerate(data):
            values = [f"{row['label']} — {row['sport']} {row['kind'].title()}", row['date'] or 'Unknown', row.get('preparation',{}).get('state','Discovered'), Path(row['path']).name]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(row['path']+'\n'+row.get('preparation',{}).get('preparation',''))
                item.setData(QtCore.Qt.UserRole, row['path'])
                if col==0:item.setData(QtCore.Qt.UserRole+1,row)
                self.table.setItem(index, col, item)
            if (row['path'],row['digest']) == previous: self.table.selectRow(index)
        del blocker
        self.update_details()

    def selected(self):
        index=self.table.currentRow()
        item=self.table.item(index,0) if index>=0 else None
        return item.data(QtCore.Qt.UserRole+1) if item else None

    def update_details(self):
        row=self.selected()
        text=preparation_details(row,self.controller)
        digest=row['digest'] if row else None
        if self.details.toPlainText()!=text:
            scroll=self.details.verticalScrollBar().value()
            self.details.setPlainText(text)
            if digest==getattr(self,'_details_digest',None):
                self.details.verticalScrollBar().setValue(scroll)
        self._details_digest=digest
        pending=self.controller.retry_request
        queued=bool(row and pending and pending['digest']==row['digest'])
        self.resume_button.setEnabled(bool(row and row['sport']=='NFL' and
            self.controller.thread is None and not self.controller.closed and not queued))

    def resume_selected(self):
        row=self.selected()
        if row:self.controller.resume(row)

    def set_smart(self, enabled):
        self.controller.owner.app_settings.setValue('build/smart_preparation',enabled)
        self.controller.paused=False
        if not enabled:self.controller.cancel()
        else:self.controller.request()

    def open_selected(self, *args):
        index = self.table.currentRow()
        if index < 0: return
        self.path = self.table.item(index, 0).data(QtCore.Qt.UserRole)
        self.accept()

    def browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Select Player CSV', '', 'CSV Files (*.csv)')
        if path:
            self.path = path
            self.accept()


def preparation_details(row, controller):
    if not row:return 'Select a slate to see progress, the last pause reason, and how to continue.'
    p=row.get('preparation') or {}
    pending=controller.retry_request
    queued=bool(pending and pending['digest']==row['digest'])
    status='Retry/Resume queued; close picker and wait for idle time.' if queued else p.get('state','Discovered')
    count=p.get('count')
    progress=f"{count:,} / {p.get('target',12000):,} candidates" if isinstance(count,int) else 'Candidate count not yet recorded'
    ownership=('Captured for preparation; refreshed live inputs are checked for ownership reuse on load.' if p.get('ownership_complete') else
               'Capture not yet recorded; it will be checked when preparation runs.')
    reason=p.get('pause_reason') or p.get('error') or p.get('preparation') or 'No pause recorded. Preparation waits for idle time.'
    if controller.paused:reason+=' Automatic discovery is paused for this session; Retry/Resume queues this slate.'
    seconds=p.get('seconds')
    time_text=(f"{seconds/60:.1f} of 30 minutes used in this attempt" if isinstance(seconds,(int,float)) else 'Attempt time not yet recorded')
    if isinstance(p.get('total_seconds'),(int,float)):
        time_text+=f"; {p['total_seconds']/60:.1f} minutes across all attempts"
    action=('Automatic preparation supports NFL. This slate can still be loaded.' if row['sport']!='NFL' else
            'Candidate target reached. Load the slate and build for current-input validation; Retry/Resume rechecks the saved target.' if p.get('state')=='Prepared candidates' else
            'Resolve the storage limit, then Retry/Resume. Saved libraries are retained.' if p.get('pause_code')=='storage' else
            'Refresh the list and select the current source file.' if p.get('pause_code')=='source_changed' else
            'Retry/Resume grants another bounded attempt and retains compatible checkpoints. Storage and input checks still apply.')
    from preparation_audit import timing_text, ownership_text
    audit=('Captured checkpoint reused for this search batch.' if p.get('checkpoint_reused') else
           'Captured checkpoint was not reused in the last recorded batch.' if 'checkpoint_reused' in p else
           'Checkpoint reuse not recorded for older work.')
    timings=timing_text(p.get('phase_timings'))
    reuse=ownership_text(p.get('ownership_reuse'))
    warming=p.get('scenario_preparation') or {}
    warming_text=(f"Recorded {warming.get('state','Not recorded')}; {warming.get('scope','setup not recorded')}; attempts {warming.get('attempts',0)}/3; {warming.get('seconds',0)/60:.1f}/30 minutes used. "+str(warming.get('message','')) if warming else 'Queued after candidate preparation when Smart preparation runs; current inputs will still be checked during Generate.')
    return (f"{row['label']} | {row['sport']} {row['kind'].title()} | {row.get('date') or 'Date unknown'}\n"
            f"Status: {status}\nProgress: {progress} | {time_text}\nOwnership: {ownership}\n"
            f"Last pause / progress: {reason}\nNext: {action}\n"
            f"Reuse: {audit}\nOwnership decision: {reuse}\nRecorded timings:\n{timings}\nScenario warming: {warming_text}\n"
            f"Last updated: {p.get('updated_at') or 'Not recorded'}\nSalary file: {row['path']}\n"
            f"Library: {p.get('library') or 'Not recorded; checked during preparation'}\n"
            'Saved work is rechecked for the current app version. Final SIM and validation run when building.')
