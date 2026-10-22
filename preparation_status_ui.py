"""Read-only preparation status from existing UI state; no scans or source validation."""
from PyQt5 import QtCore, QtWidgets
from build_snapshots import freshness_text


def preparation_rows(w):
    """Keep configured, recorded, and verified states distinct."""
    replay = bool(getattr(w, '_snapshot_replay', False))
    players = bool(w.players)
    nfl = w._current_sport() == 'NFL'
    deep = nfl and w.chk_nfl_contest_sim.isChecked() and w.combo_nfl_compute_mode.currentText().startswith('Deep')
    rows = []
    def add(key, stage, state, detail, action):
        rows.append(dict(key=key, stage=stage, state=state, detail=detail, action=action))
    from preparation_readiness import current_view
    if hasattr(w,'slate_readiness'):
        view=current_view(w)
        add('slate-preparation','This slate preparation',view['summary'],view['detail'],'Tasks dashboard')
    from build_workflow import preparation_plan
    active = bool(getattr(w, '_build_flow_active', False))
    add('workflow', 'Generate workflow', 'Underway' if active else 'Next Generate',
        w.lbl_build_flow.text() if active else preparation_plan(w), 'Tasks dashboard')
    add('inputs', 'Inputs', 'Snapshot replay' if replay else 'Live inputs',
        freshness_text(w.last_live_check_summary, replay)
        + '\nRecorded timestamps describe source checks; they do not prove every source succeeded.', 'Data freshness')
    from preparation_audit import timing_text, ownership_text
    timing = getattr(w, 'last_csv_load_timing', None)
    add('csv-timing', 'Last CSV load timings', 'Recorded' if timing else 'Not recorded',
        timing_text(timing) + '\nSource phases are included in source preparation; totals overlap. Ownership runs separately.', 'Tasks dashboard')
    from ownership_ui import ownership_status
    ownership=ownership_status(w)
    add('ownership','Current ownership',ownership['state'],ownership['detail'],'Review ownership')
    reuse = getattr(w, 'last_ownership_reuse', None)
    add('ownership-reuse', 'Last applied ownership',
        str(reuse.get('status', 'Unknown')).title() if reuse else 'Not recorded',
        ownership_text(reuse) + '\nRecorded for the last applied estimate. Later player or setting edits require a new check; this view does not validate reuse.', 'Tasks dashboard')
    report = getattr(w, 'last_readiness_report', {}) or {}
    details = []
    for check in report.get('checks', []):
        if check.get('status') in ('block', 'review'):
            details.append(f"{check.get('label', 'Check')}: {check.get('summary', '')}"
                           + (' Next: ' + check['action'] if check.get('action') else ''))
    readiness = ('Load a slate' if not players else
                 f"{report.get('title', 'Review')} · {report.get('blockers', 0)} blockers · {report.get('reviews', 0)} reviews" if report else 'Not yet checked')
    add('readiness', 'Readiness', readiness,
        '\n'.join(details) or 'Open Slate Readiness for the report. This summary does not relax rules or certify portfolio feasibility.', 'Review readiness')
    selected = str(getattr(w, '_candidate_library', '') or '')
    if selected:
        state = 'Selected; rechecked during Generate' if deep else 'Selected; NFL Deep + SIM required'
        detail = selected + '\n'
        detail += ('Explicit partial coverage selected; missing candidates can affect feasibility.' if getattr(w, '_candidate_library_allow_partial', False) else
                   'Current rules, coverage, and screening evidence are checked during Generate.')
    elif replay:
        state, detail = 'Automatic discovery off for replay', 'Saved snapshots retain their existing replay behavior.'
    elif not deep:
        state, detail = 'Fresh candidate search', 'Automatic library discovery applies to NFL Deep builds with SIM enabled.'
    else:
        state, detail = 'Automatic discovery configured', 'Generate checks remembered paths and the standard library folder. No library is verified by this summary. Incompatible automatic candidates are skipped with reasons in Tasks. Saved-portfolio repair does not automatically discover libraries.'
    add('library', 'Candidate library', state, detail, 'Open Advanced')
    if nfl and w._contest_mode() == 'showdown':
        requested = w.chk_showdown_history.isChecked()
        profile = (getattr(w, '_snapshot_calibration_data', {}).get('showdown_field_profile') if replay else
                   getattr(w, 'showdown_field_profile', None))
        state = 'Not requested'
        if requested:
            state = 'Deep + SIM required' if not deep else 'Profile missing' if not profile else 'Requested; sources rechecked during build'
        add('field', 'Historical field', state,
            'A configured profile is not current source validation. Generate retains the existing qualification, reconciliation, and source checks.', 'Prepare historical field')
    controller = getattr(w, 'history_refresh', None)
    text = str(getattr(controller, 'text', '') or '')
    busy = bool(controller and controller.busy)
    stopping = bool(busy and controller.stop is not None and controller.stop.is_set())
    watch = getattr(controller, 'watch', None)
    state = 'Stopping safely' if stopping else 'Refresh underway' if busy else 'Refresh queued' if controller and controller.queued else 'Last recorded refresh' if text else 'No recorded refresh'
    add('history', 'Results / salary history', state,
        (text or 'Choose source folders and refresh in Results and Learning.')
        + ('\n' + watch.message if watch else '')
        + '\nA recorded refresh is not proof the folders or source files are unchanged now. Historical-field qualification is checked separately.', 'Results and Learning')
    library = (getattr(w, 'last_sim_report', {}) or {}).get('candidate_library') or {}
    if library:
        add('previous', 'Previous build (historical)', 'Previous build evidence',
            str(library.get('library_path') or 'See the last Build Report for the selected source.')
            + f"\nAccepted: {library.get('accepted', 'not recorded')}; screening reused: {library.get('screening_reused', 'not recorded')}."
            + '\nThis describes the previous build, not the currently loaded slate.', 'Tasks dashboard')
    backup = getattr(w, 'idle_backup', None)
    if backup:
        add('backup', 'History backups', backup.state, backup.message, 'Backups')
    queue=getattr(w,'background_queue',None)
    if queue:add('queue','Background queue','Prioritized',queue.summary(),'Tasks dashboard')
    idle=getattr(w,'idle_preparation',None)
    if idle:add('idle-preparation','Idle candidate preparation',idle.state,idle.message,'Tasks dashboard')
    hub = getattr(w, 'tasks', None)
    active = [row for row in hub.rows.values() if row['ended'] is None] if hub else []
    add('tasks', 'Background work', f'{len(active)} tasks underway',
        '\n'.join(row['title'] + ': ' + row['detail'] for row in active) or 'Open Tasks for recent outcomes and any failures or partial work.', 'Tasks dashboard')
    return rows


class PreparationStatusDialog(QtWidgets.QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('Build preparation')
        self.resize(950, 580)
        self.owner, self.rows = window, []
        box = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('Current setup and recorded evidence. Generate performs the existing live, identity, coverage, and source checks.')
        note.setWordWrap(True)
        box.addWidget(note)
        self.table = QtWidgets.QTreeWidget()
        self.table.setHeaderLabels(['Preparation', 'Status'])
        self.table.setObjectName('preparationTable')
        self.table.setAlternatingRowColors(True)
        self.table.setRootIsDecorated(False)
        self.table.setColumnWidth(0, 270)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setObjectName('preparationText')
        from pane_styles import labelled_pane
        self.panes = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.panes.setHandleWidth(8)
        self.panes.setChildrenCollapsible(False)
        self.panes.addWidget(labelled_pane('Preparation overview', self.table, 'preparationOverview'))
        self.panes.addWidget(labelled_pane('Selected item details and next steps', self.details, 'preparationDetails'))
        self.panes.setSizes([250, 230])
        box.addWidget(self.panes)
        buttons = QtWidgets.QHBoxLayout()
        self.action = QtWidgets.QPushButton('Open selected tool')
        self.action.clicked.connect(self.open_tool)
        buttons.addWidget(self.action)
        close = QtWidgets.QPushButton('Close')
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        box.addLayout(buttons)
        self.table.currentItemChanged.connect(self.selection)
        self.refresh()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.finished.connect(self.timer.stop)

    def closeEvent(self,event):
        self.timer.stop()
        super().closeEvent(event)

    def refresh(self):
        rows = preparation_rows(self.owner)
        if rows == self.rows:
            return
        item = self.table.currentItem()
        key = item.data(0, QtCore.Qt.UserRole) if item else 'slate-preparation'
        self.rows = rows
        blocker = QtCore.QSignalBlocker(self.table)
        self.table.clear()
        for row in rows:
            item = QtWidgets.QTreeWidgetItem([row['stage'], row['state']])
            item.setData(0, QtCore.Qt.UserRole, row['key'])
            item.setToolTip(0, row['stage'])
            item.setToolTip(1, row['state'])
            self.table.addTopLevelItem(item)
            if row['key'] == key:
                self.table.setCurrentItem(item)
        del blocker
        self.selection()

    def selection(self, *_):
        item = self.table.currentItem()
        key = item.data(0, QtCore.Qt.UserRole) if item else None
        row = next((row for row in self.rows if row['key'] == key), None)
        text = row['detail'] if row else ''
        if self.details.toPlainText() != text:
            self.details.setPlainText(text)
        self.action.setEnabled(row is not None)
        self.action.setText(row['action'] if row else 'Open selected tool')

    def open_tool(self):
        item = self.table.currentItem()
        if not item:
            return
        key = item.data(0, QtCore.Qt.UserRole)
        w = self.owner
        self.accept()
        if key == 'inputs':
            w.on_data_freshness()
        elif key == 'ownership':
            from ownership_ui import show_ownership_controls
            show_ownership_controls(w)
        elif key == 'readiness':
            w.on_slate_readiness()
        elif key == 'library':
            w.action_show_build_controls.setChecked(True)
            w.tabs_workspace_controls.setCurrentIndex(0)
        elif key == 'field':
            w.on_prepare_showdown_history()
        elif key == 'backup':
            from idle_backup_ui import open_backups
            open_backups(w)
        elif key == 'history':
            w.on_results_learning()
        else:
            w.task_dashboard.show()


def open_preparation_status(window):
    # Readiness computation is report-only; never refresh sources on open.
    window._update_readiness_badge()
    dialog = PreparationStatusDialog(window)
    dialog.exec_()
    dialog.deleteLater()
