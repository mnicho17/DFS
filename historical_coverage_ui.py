"""Historical Coverage tab and workers owned by Results & Learning."""
import threading
import time
from PyQt5 import QtCore, QtWidgets

from analysis_imports import ImportCancelled
from historical_identity import STATES, reconcile
from historical_coverage import aggregate, categories, changes, detail_text, load_saved, reconciliation_text


class CoverageWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, db_path, snapshot_choices=None):
        super().__init__()
        self.db_path, self.snapshot_choices = db_path, snapshot_choices
        from compute_ledger import folder
        self.ledger_root = folder()
        self.cancelled = threading.Event()

    def request_cancel(self):
        self.cancelled.set()

    @QtCore.pyqtSlot()
    def run(self):
        started = time.monotonic()
        try:
            before = load_saved(self.db_path)['contests']
            after = reconcile(self.db_path, cancelled=self.cancelled.is_set,
                snapshot_choices=self.snapshot_choices, progress=lambda text:self.progress.emit(0,0,text), ledger_root=self.ledger_root)
            result = changes(before, after)
            result.update(committed=True, seconds=time.monotonic()-started)
            result['message'] = reconciliation_text(result)
            self.finished.emit(result)
        except ImportCancelled:
            self.finished.emit(dict(cancelled=True, committed=False,
                message='Reconciliation cancelled. Previous derived evidence and saved choices were preserved.'))
        except ValueError as exc:
            self.error.emit(str(exc))
        except Exception:
            self.error.emit('Reconciliation failed. Previous derived evidence was preserved. Check saved source access and retry.')


class SnapshotChoiceDialog(QtWidgets.QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Resolve ambiguous pregame snapshot')
        self.resize(840,440)
        self.choice = None
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel('Choose the exact compatible pregame revision for this contest. '
            'This records an evidence association, not a submitted build. The worker revalidates it before saving.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.options = QtWidgets.QComboBox()
        seen = set()
        for c in data['snapshot_candidates']:
            if c['snapshot_digest'] in seen:
                continue
            seen.add(c['snapshot_digest'])
            self.options.addItem(f"{c['format']} | {c['recorded_at']} | input {c['input_id'][:12]} | revision {c['snapshot_digest'][:12]}",c)
        layout.addWidget(self.options)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.confirm = QtWidgets.QCheckBox('I confirm this exact pregame snapshot belongs to this contest.')
        layout.addWidget(self.confirm)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        self.save = buttons.button(QtWidgets.QDialogButtonBox.Save)
        buttons.accepted.connect(self._select)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.options.currentIndexChanged.connect(self._update)
        self.confirm.toggled.connect(lambda checked:self.save.setEnabled(checked and self.options.count()>0))
        self._update()

    def _update(self):
        self.confirm.setChecked(False)
        self.save.setEnabled(False)
        c = self.options.currentData()
        self.details.setPlainText('No qualifying choices. Reconcile again.' if not c else
            f"Format: {c['format']}\nRecorded: {c['recorded_at']}\nEarliest game: {c['earliest_game']}\n"
            f"Pregame timing: qualified\nFull salary pool: {c['player_count']} players\n"
            f"Completed pregame builds sharing input: {c['build_count']}\n"
            f"Input ID: {c['input_id']}\nExact revision: {c['snapshot_digest']}\n"
            'All listed candidates pass the existing salary-pool, roster-role, game and contest-ID checks.')

    def _select(self):
        if self.confirm.isChecked() and self.options.currentData():
            self.choice = self.options.currentData()['snapshot_digest']
            self.accept()


class HistoricalCoverageWidget(QtWidgets.QWidget):
    reconcile_requested = QtCore.pyqtSignal(object)
    salary_requested = QtCore.pyqtSignal(str)
    report_requested = QtCore.pyqtSignal()

    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.rows = []
        self.by_id = {}
        self.labels = {}
        self.started = None
        self.phase = ''
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        filters = QtWidgets.QHBoxLayout()
        self.filter = QtWidgets.QComboBox()
        for label,key in [('All','all'),('Ready (full evidence chain)','ready'),('Needs review','needs_review'),
                          ('Missing evidence','missing_evidence'),('Conflict','conflict')]:
            self.filter.addItem(label,key)
        for state in STATES:
            self.filter.addItem(state,state)
        self.format_filter = QtWidgets.QComboBox()
        self.format_filter.addItems(['All formats','classic','showdown','Unknown'])
        self.sport_filter = QtWidgets.QComboBox()
        self.sport_filter.addItems(['All sports','NFL','Unknown'])
        filters.addWidget(self.filter,2)
        filters.addWidget(self.sport_filter)
        filters.addWidget(self.format_filter)
        layout.addLayout(filters)
        self.table = QtWidgets.QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(['Historical contest','Sport','Format','Date','Evidence state'])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table,1)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details,2)
        actions = QtWidgets.QHBoxLayout()
        self.reconcile_button = QtWidgets.QPushButton('Reconcile All Evidence')
        self.reconcile_button.setToolTip('Revalidate every contest, including previously qualified evidence; commit all changes together.')
        self.salary_button = QtWidgets.QPushButton('Review Salary Matches')
        self.snapshot_button = QtWidgets.QPushButton('Resolve Snapshot…')
        self.report_button = QtWidgets.QPushButton('Share Coverage Report…')
        for button in (self.reconcile_button,self.salary_button,self.snapshot_button,self.report_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QtWidgets.QLabel('Saved coverage only. Reconcile to verify current source evidence.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        for control in (self.filter,self.format_filter,self.sport_filter):
            control.currentIndexChanged.connect(self._filter)
        self.table.itemSelectionChanged.connect(self._detail)
        self.reconcile_button.clicked.connect(lambda:self.reconcile_requested.emit(None))
        self.salary_button.clicked.connect(self._salary)
        self.snapshot_button.clicked.connect(self._snapshot)
        self.report_button.clicked.connect(self.report_requested)
        self.reload()

    def reload(self):
        try:
            saved = load_saved(self.db_path)
            self.rows = [c.data for c in saved['contests']]
            self.by_id = {d['identity_id']:d for d in self.rows}
            self.labels = saved['labels']
            summary = aggregate(saved['contests'])
            levels = summary['evidence_levels']
            cats = summary['categories']
            self.summary.setText(f"Historical contests reconciled: {len(self.rows)} | " +
                ' | '.join(f'{label}: {levels.get(k,0)}' for label,k in
                           [('Results','results'),('Salary','salary'),('Snapshot','snapshot'),('Build','build'),('Complete scores','outcome')]) +
                f"\nFull chain ready: {cats.get('ready',0)} | Needs review: {cats.get('needs_review',0)} | "
                f"Missing evidence: {cats.get('missing_evidence',0)} | Conflicts: {cats.get('conflict',0)} (included in review). "
                'Stage counts are independent.' +
                f"\nLast evidence change: {saved['updated_at'] or 'not yet reconciled'}. "
                f"Imports awaiting first reconciliation: {saved['pending_imports']}; unreadable derived rows: {saved['invalid_rows']}. "
                'Saved states may be stale. Generated builds do not establish submission.')
            self._filter()
        except Exception:
            self.rows = []
            self.summary.setText('Saved coverage could not be read. Check the history database and retry reconciliation.')
            self._filter()

    def _filter(self):
        mode = self.filter.currentData()
        fmt, sport = self.format_filter.currentText(), self.sport_filter.currentText()
        filtered = [d for d in self.rows if (mode=='all' or d['state']==mode or categories(d).get(mode,False))
            and (fmt=='All formats' or (d['identity']['format'] or 'Unknown')==fmt)
            and (sport=='All sports' or (d['identity']['sport'] or 'Unknown')==sport)]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(filtered))
        for row, data in enumerate(filtered):
            ident = data['identity']
            values = [self.labels.get(data['import_id']) or f"Contest {self.rows.index(data)+1:03d} [{data['identity_id'][:8]}]",
                ident['sport'] or 'Unknown',ident['format'] or 'Unknown',ident['slate_date'] or 'Unknown',data['state']]
            for col,value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole,data['identity_id'])
                self.table.setItem(row,col,item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        if filtered:
            self.table.selectRow(0)
        self._detail()

    def selected(self):
        items = self.table.selectedItems()
        return self.by_id.get(items[0].data(QtCore.Qt.UserRole)) if items else None

    def _detail(self):
        data = self.selected()
        self.details.setPlainText(detail_text(data) if data else
            'No contests match this filter. Import Results & Salaries, then Reconcile All Evidence to populate saved coverage.')
        self.salary_button.setEnabled(bool(data and data['results_evidence'].get('source_hash')))
        self.snapshot_button.setEnabled(bool(data and not data.get('snapshot_resolution') and
            set(data['conflicts']) & {'conflicting_latest_snapshots','snapshot_timestamp_conflict'} and data['snapshot_candidates']))

    def _salary(self):
        data = self.selected()
        if data:
            self.salary_requested.emit(data['results_evidence'].get('source_hash') or '')

    def _snapshot(self):
        data = self.selected()
        if not data or not self.snapshot_button.isEnabled():
            return
        dialog = SnapshotChoiceDialog(data,self)
        if dialog.exec_()==QtWidgets.QDialog.Accepted and dialog.choice:
            self.reconcile_requested.emit({data['identity_id']:dialog.choice})

    def start_progress(self):
        self.started = time.monotonic()
        self.phase = 'Preparing reconciliation'
        self.timer.start()
        self._tick()

    def progress(self, text):
        if self.started is not None:
            self.phase = text
            self._tick()

    def _tick(self):
        if self.started is not None:
            self.status.setText(f'{self.phase}… {time.monotonic()-self.started:.0f}s elapsed. Cancel remains available.')

    def finish(self, text):
        self.timer.stop()
        self.started = None
        self.status.setText(text)
        self.reload()
