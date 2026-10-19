"""Idle catalog discovery and a cached slate picker; no active-slate mutation."""
from pathlib import Path
import threading
import time
from PyQt5 import QtCore, QtWidgets
from slate_catalog import scan, rows


class DiscoveryWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(dict)

    def __init__(self, database, folder, smart=False):
        super().__init__()
        self.database, self.folder = database, folder
        self.smart = smart
        self.stop = threading.Event()

    def run(self):
        last=[0.0]
        def notify(text):
            if time.monotonic()-last[0]>=1:
                last[0]=time.monotonic()
                self.progress.emit(text)
        try:
            result = scan(self.database, self.folder, self.stop.is_set, notify)
            if self.smart and not self.stop.is_set():
                from smart_preparation import prepare_one
                from slate_catalog import record_preparation
                result.update(prepare_one(self.database,self.folder,Path(self.database).parent/'candidates',
                    self.stop.is_set,notify))
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
        self.manual = self.manual or manual
        self.owner.background_queue.request('slate-discovery', 'Discover / prepare salary slates', 35,
            self.start, self.yield_work, ready=self.ready, idle_only=True,
            on_cancel=self.cancel, detail='Catalog salary files and prepare upcoming NFL slates while idle. Unchanged discovery and completed preparation are skipped; current slate stays loaded.')

    def ready(self):
        return not self.closed and (self.manual or self.owner.idle_preparation.ready())

    def start(self):
        from task_dashboard import watch_task
        smart=str(self.owner.app_settings.value('build/smart_preparation','true')).lower() in ('true','1')
        self.worker = DiscoveryWorker(self.database, self.folder(), smart=smart and not self.manual)
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
                   self.cancel, lambda value: str(value))
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
        if self.result.get('library'):
            from build_preparation import remember_library
            remember_library(self.owner.app_settings,self.result['library'])
        self.owner.background_queue.release('slate-discovery')
        self.changed.emit()
        if self.closed: QtCore.QTimer.singleShot(0, self.owner.close)

    def yield_work(self):
        if self.worker: self.worker.stop.set()

    def cancel(self):
        self.paused = True
        self.manual = False
        self.yield_work()

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
        self.resize(840, 430)
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
        self.table.setHorizontalHeaderLabels(['Slate', 'Game date', 'Discovery', 'File'])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Open | QtWidgets.QDialogButtonBox.Cancel)
        browse = buttons.addButton('Browse…', QtWidgets.QDialogButtonBox.ActionRole)
        refresh = buttons.addButton('Refresh list', QtWidgets.QDialogButtonBox.ActionRole)
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
        previous = self.table.item(selected, 0).data(QtCore.Qt.UserRole) if selected >= 0 else None
        query = self.search.text().strip().casefold()
        try:
            data = rows(self.controller.database, self.controller.folder(), all_dates=self.all_dates.isChecked())
            data = [r for r in data if query in ' '.join(str(v) for v in r.values()).casefold()]
            self.status.setText(self.controller.message if self.controller.folder() else
                'Choose a Salary Files folder in Data and Learning, or use Browse. Default view shows today onward.')
        except Exception as exc:
            data = []
            self.status.setText('Catalog unavailable: '+str(exc)+'. Browse remains available.')
        self.table.setRowCount(len(data))
        for index, row in enumerate(data):
            values = [f"{row['label']} — {row['sport']} {row['kind'].title()}", row['date'] or 'Unknown', row.get('preparation',{}).get('state','Discovered'), Path(row['path']).name]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(row['path']+'\n'+row.get('preparation',{}).get('preparation',''))
                item.setData(QtCore.Qt.UserRole, row['path'])
                self.table.setItem(index, col, item)
            if row['path'] == previous: self.table.selectRow(index)

    def set_smart(self, enabled):
        self.controller.owner.app_settings.setValue('build/smart_preparation',enabled)
        self.controller.paused=False
        if not enabled:self.controller.yield_work()
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
