"""Shared, nonmodal task visibility. Worker signals never touch widgets directly."""
import time
from PyQt5 import QtCore, QtWidgets
from task_attention import ATTENTION_STATES, needs_attention, result_reason, guidance


class TaskHub(QtCore.QObject):
    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.clock, self.rows, self.bindings = clock, {}, []
        self.serial = 0

    def start(self, title, cancel=None, *, state='Running', background=False):
        self.serial += 1
        key = self.serial
        now = self.clock()
        self.rows[key] = dict(title=title, state=state, background=background, detail='Starting…',
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

    def mark_reviewed(self, key):
        row = self.rows.get(key)
        if not row or not needs_attention(row):
            return False
        row['attention_reviewed'] = True
        row['log'] = (row['log'] + ['Notification marked reviewed; recorded outcome and unfinished work are unchanged.'])[-100:]
        self.changed.emit()
        return True

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
                state = 'Paused for manual work' if result.get('background_yield') else 'Cancelled'
            elif result.get('failed') or result.get('error'):
                state = 'Failed'
            elif result.get('pause_reason') or result.get('complete') is False or result.get('screening_complete') is False:
                state = 'Paused / partial'
            elif 'requested' in result and len(result.get('lineups') or []) < result['requested']:
                state = 'Incomplete'
            elif result.get('errors') or result.get('unpaired') or result.get('changed_sources') or result.get('username_history', {}).get('errors'):
                state = 'Needs review'
            elif result.get('no_work'):
                state = 'No current work'
            text = (self.formatter(result) if self.formatter else
                    str(result.get('error') or result.get('pause_reason') or
                        (f"{len(result.get('lineups') or []):,}/{result['requested']:,} lineups · {state}"
                         if 'requested' in result else result.get('next_check') or state)))
        else:
            text = str(result)
            if text.startswith('Search stopped:'):
                state = 'Failed'
        if isinstance(result, dict):
            reason = result_reason(result)
            self.hub.rows[self.key]['attention_reason'] = reason
            if state in ATTENTION_STATES and reason and reason not in text:
                self.hub.update(self.key, reason)
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
        if self.stop_event is not None and self.stop_event.is_set() and outcome in ('Completed','No current work'):
            outcome = 'Stopped'
        if row['state'] == 'Stopping safely' and outcome in ('Completed','No current work'):
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
    preparation = QtWidgets.QPushButton('Preparation…')
    preparation.setObjectName('preparationStatusButton')
    preparation.setToolTip('Review library policy, recorded freshness, readiness, historical field, and background work together.')
    from preparation_status_ui import open_preparation_status
    preparation.clicked.connect(lambda: open_preparation_status(window))
    window.backup_summary = QtWidgets.QLabel('Backups: not configured')
    bar.addWidget(window.backup_summary)
    backups=QtWidgets.QPushButton('Backups…');backups.setObjectName('backupsButton')
    from idle_backup_ui import open_backups
    backups.clicked.connect(lambda:open_backups(window))
    bar.addWidget(backups)
    bar.addWidget(preparation)
    bar.addWidget(button)
    layout.addLayout(bar)
    dock = QtWidgets.QDockWidget('Task dashboard', window)
    dock.setObjectName('taskDashboard')
    dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
    panel = task_panel(hub, dock, compact=True)
    dock.setWidget(panel)
    window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    window.resizeDocks([dock],[420],QtCore.Qt.Horizontal)
    dock.topLevelChanged.connect(lambda floating: dock.resize(440,720) if floating else None)
    dock.hide()
    def show_dashboard():
        showing = not dock.isVisible()
        dock.setVisible(showing)
        if showing:panel.focus_attention()
    button.clicked.connect(show_dashboard)
    def summary_refresh():
        active = sum(r['ended'] is None and r['state']!='Queued' for r in hub.rows.values())
        queued = sum(r['ended'] is None and r['state']=='Queued' for r in hub.rows.values())
        flagged = [r for r in hub.rows.values() if needs_attention(r)]
        attention = len(flagged)
        summary.setToolTip('\n\n'.join(r['title'] + ' — ' + r['state'] + '\n' + guidance(r)['reason'] + '\nNext: ' + guidance(r)['next_step'] for r in flagged[:5])
                           or 'No unreviewed task notifications. Open Tasks for recorded outcomes.')
        button.setToolTip('Open the flagged task and its next step.' if attention else 'Open current tasks and recorded outcomes.')
        summary.setText(f'Tasks: {active} underway · {queued} queued · {attention} need attention' if hub.rows else 'Tasks: idle')
    hub.changed.connect(summary_refresh)
    window.task_dashboard = dock
    return hub


def task_panel(hub, parent, compact=False):
    body = QtWidgets.QWidget(parent)
    box = QtWidgets.QVBoxLayout(body)
    overview = QtWidgets.QLabel()
    overview.setObjectName('taskQueueOverview')
    overview.setWordWrap(True)
    box.addWidget(overview)
    table = QtWidgets.QTableWidget(0, 5)
    table.setObjectName('taskTable')
    table.setHorizontalHeaderLabels(['Task', 'State', 'Elapsed', 'Last update', 'Progress / outcome'])
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
    table.setAlternatingRowColors(True)
    table.setWordWrap(compact)
    if compact:
        body.setMinimumWidth(340)
        table.setHorizontalHeaderLabels(['Tasks','State','Elapsed','Last update','Progress / outcome'])
        for column in range(1,5):table.setColumnHidden(column,True)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
    for column, width in enumerate((285, 140, 85, 170)):
        table.setColumnWidth(column, width)
    details = QtWidgets.QPlainTextEdit()
    details.setReadOnly(True)
    details.setPlaceholderText('Select a task above to see recent progress messages.')
    details.setObjectName('taskText')
    next_step = QtWidgets.QLabel()
    next_step.setObjectName('taskNextAction')
    next_step.setTextFormat(QtCore.Qt.PlainText)
    next_step.setWordWrap(True)
    box.addWidget(next_step)
    details.setMinimumHeight(65)
    run = QtWidgets.QPushButton('Run now')
    run.setObjectName('taskRunNow')
    run.setToolTip('Recheck this queued task now. Manual workers, priority and safety checks still apply.')
    run.setEnabled(False)
    def get_queue():
        return getattr(hub.parent(), 'background_queue', None)
    stop = QtWidgets.QPushButton('Pause selected' if compact else 'Cancel / pause selected task')
    stop.setObjectName('taskStop')
    stop.setEnabled(False)
    from pane_styles import labelled_pane
    panes = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    panes.setObjectName('taskPaneSplitter')
    panes.setHandleWidth(8)
    panes.setChildrenCollapsible(False)
    panes.addWidget(labelled_pane('Task progress and outcomes', table, 'taskOverview'))
    panes.addWidget(labelled_pane('Selected task messages', details, 'taskDetails'))
    panes.setSizes([230, 110])
    box.addWidget(panes)
    actions = QtWidgets.QHBoxLayout()
    actions.addWidget(run)
    actions.addWidget(stop)
    box.addLayout(actions)
    review_actions = QtWidgets.QHBoxLayout()
    open_review = QtWidgets.QPushButton('Open review')
    open_review.setObjectName('taskOpenReview')
    acknowledge = QtWidgets.QPushButton('Mark reviewed')
    acknowledge.setObjectName('taskMarkReviewed')
    acknowledge.setToolTip('Clear this notification only. The original failure or partial outcome stays visible; no work is retried or marked successful.')
    review_actions.addWidget(open_review)
    review_actions.addWidget(acknowledge)
    box.addLayout(review_actions)

    def review_callback(target):
        owner = hub.parent()
        if owner is None:return None
        if target == 'history':return getattr(owner, 'on_results_learning', None)
        if target == 'readiness':return getattr(owner, 'on_slate_readiness', None)
        if target == 'backup' and hasattr(owner, 'idle_backup'):
            from idle_backup_ui import open_backups
            return lambda: open_backups(owner)
        if target == 'preparation' and hasattr(owner, 'idle_preparation'):
            from preparation_status_ui import open_preparation_status
            return lambda: open_preparation_status(owner)
        return None


    def selected():
        item = table.item(table.currentRow(), 0)
        return item.data(QtCore.Qt.UserRole) if item else None

    def selection():
        row = hub.rows.get(selected())
        queue = get_queue()
        run.setEnabled(bool(queue and any(j['task'] == selected() for j in queue.pending.values())))
        stop.setEnabled(bool(row and row['cancel']))
        info = guidance(row or {})
        flagged = bool(row and row['state'] in ATTENTION_STATES)
        reviewed = bool(row and row.get('attention_reviewed'))
        next_step.setText(('Notification reviewed; recorded ' + row['state'] + ' outcome remains.\n' if reviewed else '')
            + ('Why: ' + info['reason'] + '\nNext: ' + info['next_step'] if flagged else
               'Select a task needing attention to see its reason and next step.'))
        next_step.setVisible(flagged)
        callback = review_callback(info['target'])
        open_review.setText({'history':'Results & Learning…', 'readiness':'Slate Readiness…',
                            'backup':'Backups…', 'preparation':'Preparation…'}.get(info['target'], 'Open review'))
        open_review.setEnabled(callable(callback))
        acknowledge.setEnabled(bool(row and needs_attention(row)))
        text = '\n'.join(row['log']) if row else ''
        if details.toPlainText() != text:
            details.setPlainText(text)

    def refresh():
        key = selected()
        queue=get_queue()
        schedules=[]
        owner=hub.parent()
        discovery=getattr(owner,'slate_discovery',None)
        if discovery and not discovery.paused and discovery.timer.isActive():
            seconds=max(0,discovery.timer.remainingTime()//1000)
            schedules.append(f'Slate recheck in {seconds//60}m {seconds%60}s')
        history=getattr(owner,'history_refresh',None)
        if history and history.watch.enabled() and not history.watch.paused:
            schedules.append('History: watching for file changes')
        backup=getattr(owner,'idle_backup',None)
        if backup and not backup.paused and backup.enabled() and backup.destination():
            seconds=max(0,int(backup.next_check-backup.clock()))
            schedules.append('Backup: '+('changes pending' if backup.dirty else f'recheck in {seconds//60}m {seconds%60}s'))
        active=any(r['ended'] is None and r['state']!='Queued' for r in hub.rows.values())
        message=queue.summary() if queue and (queue.active or queue.pending) else 'Manual work underway; background tasks wait for worker retirement.' if active else 'No current work.'
        overview.setText(message+('\n'+' | '.join(schedules) if schedules else ''))
        rows = sorted(((k,r) for k,r in hub.rows.items() if not compact or r['state']!='Dispatched'), key=lambda pair: (pair[1]['ended'] is not None, -pair[0]))
        blocker = QtCore.QSignalBlocker(table)
        table.setRowCount(len(rows))
        for i, (task, r) in enumerate(rows):
            now = hub.clock()
            elapsed = int((r['ended'] if r['ended'] is not None else now) - r['started'])
            age = int(now-r['updated'])
            update = f'{age}s ago' + (' · no recent update' if age >= 60 and r['ended'] is None and r['state']!='Queued' else '')
            values = [r['title'], r['state'] + (' · reviewed' if r.get('attention_reviewed') else ''), f'{elapsed//60}m {elapsed%60}s', update, r['detail']]
            if compact:
                values[0]=f"{r['title']}\n{values[1]} | {elapsed//60}m {elapsed%60}s | {update}\n{r['detail']}"
            for j, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(value)
                if j == 0:
                    item.setData(QtCore.Qt.UserRole, task)
                table.setItem(i, j, item)
            if task == key:
                table.setCurrentCell(i, 0)
        if compact:table.resizeRowsToContents()
        del blocker
        selection()

    def focus_attention():
        refresh()
        for index in range(table.rowCount()):
            key = table.item(index, 0).data(QtCore.Qt.UserRole)
            if needs_attention(hub.rows[key]):
                table.setCurrentCell(index, 0)
                table.scrollToItem(table.item(index, 0))
                return
    body.focus_attention = focus_attention

    def open_selected_review():
        callback = review_callback(guidance(hub.rows.get(selected(), {}))['target'])
        if callable(callback):callback()
    open_review.clicked.connect(open_selected_review)
    acknowledge.clicked.connect(lambda: hub.mark_reviewed(selected()))

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
    run.clicked.connect(lambda: get_queue().run_now(selected()) if get_queue() else None)
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
