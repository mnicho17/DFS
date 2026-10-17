"""Shared, nonmodal task visibility. Worker signals never touch widgets directly."""
import time
from PyQt5 import QtCore, QtWidgets


class TaskHub(QtCore.QObject):
    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.clock, self.rows, self.bindings = clock, {}, []
        self.serial = 0

    def start(self, title, cancel=None):
        self.serial += 1
        key = self.serial
        now = self.clock()
        self.rows[key] = dict(title=title, state='Running', detail='Starting…',
                              started=now, updated=now, ended=None, cancel=cancel, log=[])
        # Retain all active tasks and the most recent 30 retired tasks.
        retired = [k for k, r in self.rows.items() if r['ended'] is not None]
        for old in retired[:-30]:
            del self.rows[old]
        self.changed.emit()
        return key

    def update(self, key, detail):
        row = self.rows[key]
        if row['ended'] is not None:
            return
        row['detail'], row['updated'] = str(detail), self.clock()
        row['log'] = (row['log'] + [str(detail)])[-100:]
        self.changed.emit()

    def finish(self, key, state, detail=None):
        row = self.rows[key]
        if row['ended'] is not None:
            return
        if detail is not None:
            self.update(key, detail)
        row.update(state=state, ended=self.clock(), cancel=None)
        self.changed.emit()

    def cancel(self, key):
        row = self.rows[key]
        callback = row['cancel']
        if callback is not None and row['ended'] is None:
            row['state'] = 'Stopping safely'
            row['cancel'] = None
            self.update(key, 'Stop requested; waiting for checkpoint and worker retirement.')
            callback()

    def watch(self, title, worker, thread, cancel=None, formatter=None):
        binding = WorkerTask(self, title, cancel, formatter)
        binding.stop_event = next((getattr(worker, name) for name in ('stop', '_cancel_event', 'cancelled')
                                   if hasattr(getattr(worker, name, None), 'is_set')), None)
        self.bindings.append(binding)
        worker.progress.connect(binding.progress)
        worker.finished.connect(binding.completed)
        if hasattr(worker, 'error'):
            worker.error.connect(binding.failed)
        thread.finished.connect(binding.retired)
        return binding


class WorkerTask(QtCore.QObject):
    def __init__(self, hub, title, cancel, formatter):
        super().__init__(hub)
        self.hub, self.formatter = hub, formatter
        self.key = hub.start(title, cancel)
        self.outcome = None

    def progress(self, *args):
        if self.formatter:
            text = self.formatter(*args)
        elif len(args) == 3:
            done, total, text = args
            text = f'{done:,}/{total:,} · {text}' if total else str(text)
        else:
            text = str(args[0]) if args else 'Working…'
        self.hub.update(self.key, text)

    def completed(self, result):
        state = 'Completed'
        if isinstance(result, dict):
            if result.get('cancelled'):
                state = 'Cancelled'
            elif result.get('failed') or result.get('error'):
                state = 'Failed'
            elif result.get('pause_reason') or result.get('complete') is False or result.get('screening_complete') is False:
                state = 'Paused / partial'
            elif 'requested' in result and len(result.get('lineups') or []) < result['requested']:
                state = 'Incomplete'
            elif result.get('errors') or result.get('unpaired') or result.get('username_history', {}).get('errors'):
                state = 'Needs review'
            text = (self.formatter(result) if self.formatter else
                    str(result.get('error') or result.get('pause_reason') or
                        (f"{len(result.get('lineups') or []):,}/{result['requested']:,} lineups · {state}"
                         if 'requested' in result else state)))
        else:
            text = str(result)
            if text.startswith('Search stopped:'):
                state = 'Failed'
        self.outcome = state
        self.hub.update(self.key, text + ' · Retiring worker…')

    def failed(self, message):
        self.outcome = 'Failed'
        self.hub.update(self.key, str(message) + ' · Retiring worker…')

    def retired(self):
        row = self.hub.rows[self.key]
        outcome = self.outcome or 'Failed'
        if self.outcome is None:
            self.hub.update(self.key, 'Worker exited without reporting a completion result.')
        if self.stop_event is not None and self.stop_event.is_set() and outcome == 'Completed':
            outcome = 'Stopped'
        if row['state'] == 'Stopping safely' and outcome == 'Completed':
            outcome = 'Stopped'
        detail = ('Stopped; worker retired safely.' if outcome == 'Stopped' else
                  row['detail'].removesuffix(' · Retiring worker…'))
        self.hub.finish(self.key, outcome, detail)
        self.hub.bindings.remove(self)
        self.deleteLater()


def install_dashboard(window, layout):
    hub = window.tasks = TaskHub(window)
    bar = QtWidgets.QHBoxLayout()
    summary = QtWidgets.QLabel('Tasks: idle')
    summary.setObjectName('taskSummary')
    button = QtWidgets.QPushButton('Tasks…')
    button.setObjectName('taskDashboardButton')
    bar.addWidget(summary, 1)
    bar.addWidget(button)
    layout.addLayout(bar)
    dock = QtWidgets.QDockWidget('Task dashboard', window)
    dock.setObjectName('taskDashboard')
    dock.setWidget(task_panel(hub, dock))
    window.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dock)
    dock.hide()
    button.clicked.connect(lambda: dock.setVisible(not dock.isVisible()))
    def summary_refresh():
        active = sum(r['ended'] is None for r in hub.rows.values())
        attention = sum(r['state'] in ('Failed', 'Needs review', 'Incomplete', 'Paused / partial') for r in hub.rows.values())
        summary.setText(f'Tasks: {active} underway · {attention} need attention' if hub.rows else 'Tasks: idle')
    hub.changed.connect(summary_refresh)
    window.task_dashboard = dock
    return hub


def task_panel(hub, parent):
    body = QtWidgets.QWidget(parent)
    box = QtWidgets.QVBoxLayout(body)
    table = QtWidgets.QTableWidget(0, 5)
    table.setObjectName('taskTable')
    table.setHorizontalHeaderLabels(['Task', 'State', 'Elapsed', 'Last update', 'Progress / outcome'])
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
    table.setWordWrap(False)
    for column, width in enumerate((285, 140, 85, 170)):
        table.setColumnWidth(column, width)
    details = QtWidgets.QPlainTextEdit()
    details.setReadOnly(True)
    details.setMaximumHeight(110)
    stop = QtWidgets.QPushButton('Cancel / pause selected task')
    stop.setObjectName('taskStop')
    stop.setEnabled(False)
    box.addWidget(table)
    box.addWidget(details)
    box.addWidget(stop)

    def selected():
        item = table.item(table.currentRow(), 0)
        return item.data(QtCore.Qt.UserRole) if item else None

    def selection():
        row = hub.rows.get(selected())
        stop.setEnabled(bool(row and row['cancel']))
        details.setPlainText('\n'.join(row['log']) if row else '')

    def refresh():
        key = selected()
        rows = sorted(hub.rows.items(), key=lambda pair: (pair[1]['ended'] is not None, -pair[0]))
        blocker = QtCore.QSignalBlocker(table)
        table.setRowCount(len(rows))
        for i, (task, r) in enumerate(rows):
            now = hub.clock()
            elapsed = int((r['ended'] if r['ended'] is not None else now) - r['started'])
            age = int(now-r['updated'])
            update = f'{age}s ago' + (' · no recent update' if age >= 60 and r['ended'] is None else '')
            values = [r['title'], r['state'], f'{elapsed//60}m {elapsed%60}s', update, r['detail']]
            for j, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                if j == 0:
                    item.setData(QtCore.Qt.UserRole, task)
                table.setItem(i, j, item)
            if task == key:
                table.setCurrentCell(i, 0)
        del blocker
        selection()

    class Receiver(QtCore.QObject):
        @QtCore.pyqtSlot()
        def update(self):
            if not getattr(self, 'pending', False):
                self.pending = True
                QtCore.QTimer.singleShot(100, self.flush)

        @QtCore.pyqtSlot()
        def flush(self):
            self.pending = False
            refresh()
    receiver = Receiver(body)
    hub.changed.connect(receiver.update)
    table.itemSelectionChanged.connect(selection)
    stop.clicked.connect(lambda: hub.cancel(selected()) if selected() in hub.rows else None)
    timer = QtCore.QTimer(body)
    timer.timeout.connect(refresh)
    timer.start(1000)
    refresh()
    return body


def task_hub(owner):
    while owner is not None:
        if hasattr(owner, 'tasks'):
            return owner.tasks
        owner = owner.parent()
    return None


def watch_task(owner, title, worker, thread, cancel=None, formatter=None):
    hub = task_hub(owner)
    return hub.watch(title, worker, thread, cancel, formatter) if hub else None


def add_task_button(dialog, layout):
    """Keep the shared dashboard reachable while a preparation dialog is modal."""
    hub = task_hub(dialog)
    if hub is None:
        return
    button = QtWidgets.QPushButton('Tasks…')
    def show():
        view = QtWidgets.QDialog(dialog)
        view.setWindowTitle('Task dashboard')
        view.resize(1000, 420)
        box = QtWidgets.QVBoxLayout(view)
        box.addWidget(task_panel(hub, view))
        view.exec_()
        view.deleteLater()
    button.clicked.connect(show)
    layout.addWidget(button)
