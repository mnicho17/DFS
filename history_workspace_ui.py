"""History navigation over existing import, evidence and username-history services."""
import threading
from PyQt5 import QtCore, QtWidgets


class ExploreHistoryWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, db_path, cutoff, contest_format, username):
        super().__init__()
        self.db_path, self.cutoff, self.contest_format, self.username = db_path, cutoff, contest_format, username
        self.stop = threading.Event()

    def request_cancel(self):
        self.stop.set()

    @QtCore.pyqtSlot()
    def run(self):
        from analysis_imports import ImportCancelled
        try:
            from opponent_history import profile_preview
            self.progress.emit(0, 0, 'Verifying saved username history and source evidence…')
            profile = profile_preview(self.db_path, self.cutoff, self.contest_format,
                                      self.username, self.stop.is_set)
            self.finished.emit(dict(cancelled=True) if self.stop.is_set() else dict(profile=profile))
        except ImportCancelled:
            self.finished.emit(dict(cancelled=True))
        except Exception as exc:
            self.finished.emit(dict(error=str(exc)))


class ExploreUsersWidget(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        box = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('Explore all indexed, mapped contests by exact username, or leave the search blank for the field. '
            'Each search revalidates source evidence. Historical success is descriptive; missing contests and profitability remain unknown.')
        note.setWordWrap(True); box.addWidget(note)
        row = QtWidgets.QHBoxLayout()
        self.username = QtWidgets.QLineEdit()
        self.username.setObjectName('historyExploreUsername')
        self.username.setPlaceholderText('Exact username; blank for the whole field')
        row.addWidget(self.username, 2)
        self.format = QtWidgets.QComboBox()
        self.format.addItem('Showdown', 'showdown'); self.format.addItem('Classic', 'classic')
        row.addWidget(self.format)
        row.addWidget(QtWidgets.QLabel('Strictly before'))
        self.cutoff = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.cutoff.setCalendarPopup(True); self.cutoff.setDisplayFormat('yyyy-MM-dd')
        row.addWidget(self.cutoff)
        self.search = QtWidgets.QPushButton('Explore history')
        self.search.clicked.connect(self.start); self.username.returnPressed.connect(self.start)
        row.addWidget(self.search); box.addLayout(row)
        self.status = QtWidgets.QLabel('Configured folders update automatically when monitoring is enabled. Use Check now / Retry on Overview to check new mappings, then explore. No search has run.')
        self.status.setWordWrap(True); box.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Username', 'Contests', 'Entries', 'Qualified success coverage', 'Top-1% contests', 'Historical cohort'])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(self.select_user)
        self.report = QtWidgets.QPlainTextEdit(); self.report.setReadOnly(True)
        self.report.setPlaceholderText('Construction distributions, Captain usage, salary usage, contest history, and exclusions appear here after a search.')
        from pane_styles import labelled_pane
        panes = QtWidgets.QSplitter(QtCore.Qt.Vertical); panes.setHandleWidth(8); panes.setChildrenCollapsible(False)
        panes.addWidget(labelled_pane('Observed users — double-click to select a username', self.table, 'historyUsersPane'))
        panes.addWidget(labelled_pane('Construction, contest history, and evidence limits', self.report, 'historyTrendsPane'))
        panes.setSizes([200, 300]); box.addWidget(panes, 1)
        actions = QtWidgets.QHBoxLayout()
        contest = QtWidgets.QPushButton('Inspect one contest…'); contest.clicked.connect(owner.open_opponent_portfolios)
        advanced = QtWidgets.QPushButton('Advanced history / field qualification…'); advanced.clicked.connect(self.advanced)
        copy = QtWidgets.QPushButton('Copy displayed analysis'); copy.clicked.connect(lambda:QtWidgets.QApplication.clipboard().setText(self.report.toPlainText()))
        actions.addWidget(contest); actions.addWidget(advanced); actions.addWidget(copy); box.addLayout(actions)

    def select_user(self, item):
        self.username.setText(self.table.item(item.row(), 0).text())
        self.status.setText('Username selected. Choose Explore history to verify and load its contest history.')

    def start(self):
        owner = self.owner
        if owner._import_thread is not None or owner.history_refresh.busy:
            return
        self.status.setText('Verifying history… Previous completed analysis remains visible until this search succeeds.')
        worker = ExploreHistoryWorker(owner.db_path, self.cutoff.date().toString('yyyy-MM-dd'),
                                      self.format.currentData(), self.username.text().strip())
        stop = worker.stop
        owner._start_background_import(worker, lambda result:self.finish(
            dict(cancelled=True) if stop.is_set() else result))

    def finish(self, result):
        if result.get('cancelled'):
            self.status.setText('Search cancelled. Previous completed analysis remains displayed.'); return
        if result.get('error'):
            self.status.setText('Search unavailable: ' + result['error'] + ' Use Check now / Retry on Overview or review Learning coverage. Previous analysis remains displayed.'); return
        from opponent_history import render_preview
        from opponent_analysis_ui import NumberItem
        profile = result['profile']
        self.report.setPlainText(render_preview(profile))
        self.table.setRowCount(len(profile['users']))
        for index, user in enumerate(profile['users']):
            self.table.setItem(index, 0, QtWidgets.QTableWidgetItem(user['username']))
            for col, key in enumerate(('contests', 'entries', 'qualifying_contests', 'top_one_pct_contests'), 1):
                self.table.setItem(index, col, NumberItem(user[key]))
            self.table.setItem(index, 5, QtWidgets.QTableWidgetItem('Included' if user['in_successful_cohort'] else 'Not included'))
        self.status.setText(f"Verified history before {profile['cutoff']} ({profile['format']}); search: {profile['username'] or 'whole field'}. "
            f"{profile['observed']['contests']} contests, {profile['observed']['entries']:,} entries; "
            f"{len(profile['excluded'])} excluded contests. Showing {len(profile['users'])} users; an exact username searches beyond the table limit. "
            'Source qualification applies to this completed search; it does not enable a field model.')

    def advanced(self):
        if self.owner._import_thread is not None or self.owner.history_refresh.busy:
            return
        from opponent_history_ui import OpponentHistoryDialog
        # The modal advanced tool protects the shared history controller too.
        self.owner.history_refresh.report_started()
        try:
            OpponentHistoryDialog(self.owner.db_path, self.username.text().strip(), self.owner).exec_()
        finally:
            self.owner.history_refresh.report_finished()


def add_item(layout, item):
    # Qt's generic addItem does not reparent widgets. Use the typed APIs when
    # moving controls between pages, and detach the former layout ownership.
    if item.widget() is not None:
        layout.addWidget(item.widget())
    elif item.layout() is not None:
        item.layout().setParent(None)
        layout.addLayout(item.layout())
    else:
        layout.addItem(item)


def collapsible(title, items, parent):
    body = QtWidgets.QWidget(parent); box = QtWidgets.QVBoxLayout(body); box.setContentsMargins(5, 3, 5, 3)
    for item in items:
        add_item(box, item)
    toggle = QtWidgets.QToolButton(parent); toggle.setText(title); toggle.setCheckable(True)
    toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon); toggle.setArrowType(QtCore.Qt.RightArrow)
    def show(expanded):
        body.setVisible(expanded); toggle.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
    toggle.toggled.connect(show); body.hide()
    return toggle, body


def history_update_view(controller,report_busy=False):
    """Present existing controller/watcher state without inspecting source files."""
    watch=controller.watch
    configured=any(str(controller.settings.value(key,'') or '').strip()
        for key in ('learning/results_folder','learning/salary_folder'))
    busy=controller.busy
    stopping=busy and controller.stop is not None and controller.stop.is_set()
    queued=controller.queued
    automatic=controller.enabled()
    monitoring=watch.enabled()
    armed=bool(watch.watcher.files() or watch.watcher.directories()) if monitoring and not (busy or queued or watch.paused) else False
    if watch.closed:state='Closing'
    elif stopping:state='Stopping safely'
    elif busy:state='Checking history'
    elif queued:state='Update queued'
    elif not configured:state='Choose source folders'
    elif watch.paused:state='Paused'
    elif not automatic:state='Automatic updates off'
    elif not monitoring:state='Startup checks only; folder monitoring off'
    elif armed:state='Watching for changes'
    else:state='Monitoring not armed'
    action='sources' if not configured else 'check'
    label='Choose source folders…' if not configured else 'Retry / Resume' if watch.paused else 'Check now'
    detail=(f"History updates: {state}. Automatic updates: {'on' if automatic else 'off'}.\n"
        +watch.message+'\n'
        +('Current progress: ' if busy else 'Last recorded check: ')
        +(controller.text.split('\n')[0] if controller.text else 'No completed check recorded.')
        +'\nMonitoring and saved import status do not establish current source or field-model qualification; analysis retains its existing validation.')
    return dict(state=state,label=label,action=action,detail=detail,
        can_check=not busy and not report_busy and not watch.closed,
        can_pause=not stopping and (busy or queued) and not watch.closed)


def install_history_workspace(w, layout):
    """Move original controls intact; all importer handlers and worker ownership stay in place."""
    items = [layout.takeAt(0) for _ in range(layout.count())]
    def contains(item, widget):
        if item.widget() is widget: return True
        return bool(item.layout() and any(contains(item.layout().itemAt(i), widget) for i in range(item.layout().count())))
    def take(widget):
        for item in list(items):
            if contains(item, widget): items.remove(item); return item
        raise ValueError('History layout item missing: ' + widget.objectName())
    source_items = [items.pop(0), take(w.username_edit), take(w.results_folder), take(w.salary_folder)]
    tools = [take(w.analyze_button), take(w.import_button), take(w.review_report_button),take(w.import_new_button)]
    summary = take(w.summary); evidence = take(w.results_tabs)
    progress = take(w.import_progress); progress_text = take(w.import_status_label)
    refresh = take(w.auto_history); status = take(w.history_status)
    if items: raise ValueError('Unplaced History controls')
    # Keep Close reachable from every view instead of inside advanced tools.
    close = next(tools[1].layout().itemAt(i).widget() for i in range(tools[1].layout().count())
                 if isinstance(tools[1].layout().itemAt(i).widget(), QtWidgets.QPushButton)
                 and tools[1].layout().itemAt(i).widget().text() == 'Close')
    tools[1].layout().removeWidget(close)
    w.results_tabs.removeTab(w.results_tabs.indexOf(w.report))
    w.results_tabs.setTabText(w.results_tabs.indexOf(w.coverage), 'Evidence coverage')
    w.history_workspace = QtWidgets.QTabWidget(w); w.history_workspace.setObjectName('historyWorkspace')
    # These buttons move out of detached rows; explicitly remove the former
    # layout membership because Qt cannot find those rows through the root now.
    refresh.layout().removeWidget(w.history_refresh_button)
    tools[1].layout().removeWidget(w.attach_salary_button)
    overview = QtWidgets.QWidget(); overview_box = QtWidgets.QVBoxLayout(overview)
    w.history_update_summary=QtWidgets.QLabel();w.history_update_summary.setObjectName('historyUpdateSummary')
    w.history_update_summary.setWordWrap(True);w.history_update_summary.setTextFormat(QtCore.Qt.PlainText)
    overview_box.addWidget(w.history_update_summary)
    top = QtWidgets.QHBoxLayout()
    refresh.layout().removeWidget(w.history_cancel_button)
    top.addWidget(w.history_refresh_button);top.addWidget(w.history_cancel_button);top.addWidget(w.attach_salary_button)
    review = QtWidgets.QPushButton('Review learning coverage'); top.addWidget(review)
    overview_box.addLayout(top)
    toggle, body = collapsible('Sources and account settings', source_items, overview)
    w.history_sources_toggle=toggle
    refresh.layout().removeWidget(w.auto_history);body.layout().addWidget(w.auto_history)
    overview_box.addWidget(toggle); overview_box.addWidget(body)
    add_item(overview_box, status)
    w.history_attention = QtWidgets.QLabel(); w.history_attention.setWordWrap(True)
    overview_box.addWidget(w.history_attention)
    add_item(overview_box, summary); overview_box.addWidget(w.report, 1)
    w.report.show()  # removeTab explicitly hid the original report widget.
    toggle, body = collapsible('More reports and analysis tools', tools, overview)
    overview_box.addWidget(toggle); overview_box.addWidget(body)
    overview_box.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
    w.report.setMinimumHeight(180)
    overview_scroll = QtWidgets.QScrollArea(); overview_scroll.setWidgetResizable(True)
    overview_scroll.setFrameShape(QtWidgets.QFrame.NoFrame); overview_scroll.setWidget(overview)
    w.history_workspace.addTab(overview_scroll, 'Overview')
    w.history_explore = ExploreUsersWidget(w)
    w.history_workspace.addTab(w.history_explore, 'Explore users')
    learning = QtWidgets.QWidget(); learning_box = QtWidgets.QVBoxLayout(learning)
    note = QtWidgets.QLabel('Saved evidence coverage for results, salary, pregame snapshots, builds, and outcomes. '
        'These states may be stale and are distinct from field-model qualification. Reconcile to verify sources; '
        'Explore users → Advanced history / field qualification provides the existing pregame-field tests and exclusions.')
    note.setWordWrap(True); learning_box.addWidget(note); add_item(learning_box, evidence)
    w.history_workspace.addTab(learning, 'Learning coverage')
    review.clicked.connect(lambda:w.history_workspace.setCurrentWidget(learning))
    layout.addWidget(w.history_workspace, 1); add_item(layout, progress); add_item(layout, progress_text)
    footer = QtWidgets.QHBoxLayout(); footer.addStretch(); footer.addWidget(close); layout.addLayout(footer)
    w.setWindowTitle('History — Results and Learning'); w.resize(1120, 850)
    def update_attention():
        from historical_coverage import categories
        rows = w.coverage.rows
        counts = {key:sum(bool(categories(row)[key]) for row in rows) for key in ('ready','needs_review','missing_evidence','conflict')}
        text = (f"Saved coverage: {len(rows)} contests; {counts['ready']} full evidence chains; "
                f"{counts['needs_review']} need review ({counts['conflict']} conflicts); {counts['missing_evidence']} missing evidence. "
                'Saved states do not establish current field-model qualification. Review Learning coverage for reasons and actions.'
                if rows else 'No reconciled coverage is recorded. Wait for automatic updates or choose Check now, then review Learning coverage for imported contests and missing evidence.')
        if w.history_attention.text() != text: w.history_attention.setText(text)
    w.refresh_history_overview = update_attention
    w.history_workspace.currentChanged.connect(lambda _:update_attention())
    w.history_refresh.changed.connect(update_attention)
    update_attention()
    w.update_history_status()
