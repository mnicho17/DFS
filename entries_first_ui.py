"""Entries-first selection using the existing CSV worker and build controls."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from PyQt5 import QtCore, QtWidgets
from entries_first import LIMITS, choose, inspect_entries, metadata_key, profile_key
from contest_objectives import objective_label

KEY = 'build/entries_choices_json'


def remembered(owner):
    try:
        value = json.loads(str(owner.app_settings.value(KEY, '{}')))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


class InspectWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(dict)

    def __init__(self, path):
        super().__init__()
        self.path, self.stop = path, threading.Event()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            self.finished.emit(dict(plan=inspect_entries(self.path, self.stop.is_set)))
        except InterruptedError:
            self.finished.emit(dict(cancelled=True))
        except Exception as exc:
            self.finished.emit(dict(error=str(exc)))


class EntriesFirstDialog(QtWidgets.QDialog):
    def __init__(self, owner, path):
        super().__init__(owner)
        self.owner, self.path = owner, path
        self.plan = self.selection = None
        self.thread = self.worker = self.payload = None
        self.cancelled = self.closing = False
        self.profiles = deepcopy(owner._load_contest_profiles())
        self.salary_path = ''
        self.setWindowTitle('Build these entries')
        self.resize(780, 410)
        box = QtWidgets.QVBoxLayout(self)
        self.info = QtWidgets.QLabel('Reading a private copy of the entries file. The current slate stays available until loading succeeds.')
        self.info.setWordWrap(True);self.info.setTextFormat(QtCore.Qt.PlainText)
        box.addWidget(self.info)
        form = QtWidgets.QFormLayout()
        self.contest = QtWidgets.QComboBox();self.contest.setObjectName('entriesContest')
        self.limit = QtWidgets.QComboBox();self.limit.setObjectName('entriesLimit')
        self.limit.addItem('Choose the actual entry-limit preset', '')
        for name in LIMITS:self.limit.addItem(name, name)
        self.strategy = QtWidgets.QComboBox();self.strategy.setObjectName('entriesStrategy')
        self.strategy.addItem('Choose a strategy', '')
        self.strategy.addItem('Tournament — finish rates, no payout profile', 'default')
        for name, profile in self.profiles.items():
            self.strategy.addItem(f"{name} — {objective_label(profile['objective'])} / ${profile['entry_fee']:.2f}", 'profile:' + name)
        form.addRow('Contest', self.contest);form.addRow('Entry limit', self.limit);form.addRow('Strategy / saved profile', self.strategy)
        box.addLayout(form)
        self.salary = QtWidgets.QLabel('Salary slate not selected')
        self.salary.setTextFormat(QtCore.Qt.PlainText);self.salary.setWordWrap(True);box.addWidget(self.salary)
        self.select_salary = QtWidgets.QPushButton('Choose salary slate…')
        self.select_salary.clicked.connect(self.browse_salary);box.addWidget(self.select_salary)
        self.confirm = QtWidgets.QCheckBox('I confirm this is the salary slate for the selected contest')
        box.addWidget(self.confirm)
        note = QtWidgets.QLabel('The CSV establishes contest IDs, fees and your entry counts. It does not establish the entry limit, objective or payout table. Choose these once; successful loading remembers them for this contest. Saved profiles are managed in Settings > Contest-Aware SIM. Profile-backed Showdown uses the existing Baseline Deep profile if currently Fast.')
        note.setWordWrap(True);box.addWidget(note)
        self.feedback = QtWidgets.QLabel();self.feedback.setWordWrap(True);self.feedback.setTextFormat(QtCore.Qt.PlainText);box.addWidget(self.feedback)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Open | QtWidgets.QDialogButtonBox.Cancel)
        self.load = buttons.button(QtWidgets.QDialogButtonBox.Open);self.load.setText('Load contest setup')
        self.load.setObjectName('loadEntriesSetup');self.load.setEnabled(False)
        buttons.accepted.connect(self.accept_selection);buttons.rejected.connect(self.reject);box.addWidget(buttons)
        self.contest.currentIndexChanged.connect(self.recall)
        self.limit.currentIndexChanged.connect(self.refresh)
        self.strategy.currentIndexChanged.connect(self.refresh)
        self.confirm.toggled.connect(self.refresh)
        self.select_salary.setEnabled(False)
        self.task = owner.tasks.start('Reading entries setup', self.cancel)
        self.timer = QtCore.QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.dispatch)
        self.timer.start();owner.background_queue.pump();self.dispatch()

    def dispatch(self):
        if self.cancelled:
            self.timer.stop()
            if self.thread is None:self.retire()
            return
        if self.thread is not None or self.owner.background_queue.active is not None:return
        if any(key != self.task and row['ended'] is None and row['state'] != 'Queued'
               for key, row in self.owner.tasks.rows.items()):return
        self.timer.stop()
        self.owner.tasks.update(self.task, 'Reading entries and checking embedded salary metadata.')
        self.worker = InspectWorker(self.path);self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.receive)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire);self.thread.start()

    def receive(self, payload):
        self.payload = payload

    def retire(self):
        thread = self.thread
        self.thread = self.worker = None
        if thread:thread.deleteLater()
        payload = self.payload or {}
        cancelled = self.cancelled or payload.get('cancelled')
        state = 'Cancelled' if cancelled else 'Failed' if payload.get('error') or not payload.get('plan') else 'Completed'
        if self.task is not None:
            self.owner.tasks.finish(self.task, state, 'Entries inspection '+state.lower()+'.')
            self.task = None
        if cancelled:
            super().reject()
            if self.closing:QtCore.QTimer.singleShot(0, self.owner.close)
            return
        if state == 'Failed':
            self.feedback.setText(payload.get('error', 'Entries inspection returned no result.'))
            return
        self.plan = payload['plan']
        self.contest.addItem('Choose a contest', '')
        for row in self.plan['contests']:
            self.contest.addItem(f"{row['name']} | ${row['fee']} | {row['count']} {'entry' if row['count'] == 1 else 'entries'} | ID {row['id']}", row['id'])
        if self.plan['embedded_identity']:
            self.salary_path = self.plan['entries_path']
            self.confirm.hide()
        self.select_salary.setEnabled(True)
        self.info.setText('Select the contest to build. Loading rechecks the captured entries, salary identities and existing roster IDs before applying any settings.')
        if len(self.plan['contests']) == 1:self.contest.setCurrentIndex(1)
        self.refresh()

    def recall(self):
        if not self.plan:return
        self.limit.setCurrentIndex(0);self.strategy.setCurrentIndex(0)
        row = next((c for c in self.plan['contests'] if c['id'] == self.contest.currentData()), None)
        saved = remembered(self.owner).get(metadata_key(self.plan, row), {}) if row else {}
        if isinstance(saved, dict):
            name = saved.get('strategy')
            p = self.profiles.get(str(name).removeprefix('profile:')) if str(name).startswith('profile:') else None
            if name == 'default' or (p and saved.get('profile_key') == profile_key(p)):
                self.limit.setCurrentIndex(max(0, self.limit.findData(saved.get('limit'))))
                self.strategy.setCurrentIndex(max(0, self.strategy.findData(name)))
        self.refresh()

    def browse_salary(self):
        from slate_picker import SlatePicker
        picker = SlatePicker(self.owner.slate_discovery, self)
        if picker.exec_():
            self.salary_path = picker.path
            self.confirm.setChecked(False)
        picker.deleteLater();self.refresh()

    def selected(self):
        if not self.plan:raise ValueError('Wait for entries inspection to finish.')
        return choose(self.plan, self.contest.currentData(), self.limit.currentData(),
                      self.strategy.currentData(), self.profiles, self.salary_path, self.confirm.isChecked())

    def refresh(self):
        if not self.plan:return
        self.salary.setText(('Embedded salary table: ' if self.salary_path == self.plan['entries_path'] and self.plan['embedded_identity'] else 'Selected salary slate: ') + (Path(self.salary_path).name if self.salary_path else 'Choose a salary slate'))
        self.salary.setToolTip(self.salary_path)
        try:
            value = self.selected()
            detail = f"Load {value['kind'].title()} and request {value['contest']['count']} {'lineup' if value['contest']['count'] == 1 else 'lineups'} for this contest. Next: Generate, save your lineups, then Update Entries."
            enabled = True
        except (ValueError, OSError) as exc:
            detail, enabled = str(exc), False
        self.feedback.setText(detail);self.load.setEnabled(enabled)

    def accept_selection(self):
        try:self.selection = self.selected()
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc));return
        super().accept()

    def cancel(self):
        self.cancelled = True
        if self.worker:self.worker.stop.set()
        self.feedback.setText('Stopping safely; waiting for entries inspection to retire.')
        if self.thread is None:self.dispatch()

    def reject(self):
        if self.task is not None:
            self.cancel();return
        super().reject()

    def stop_for_close(self):
        if self.task is None:return True
        self.closing = True;self.cancel();return False

    def closeEvent(self, event):
        if self.task is not None:
            self.cancel();event.ignore()
        else:event.accept()


def open_entries(owner):
    if owner._snapshot_busy() or getattr(owner, '_build_flow_active', False) or getattr(owner, '_ownership_closing', False):
        owner.status.showMessage('Wait for the current load, build or ownership calculation to finish.', 5000);return
    path, _ = QtWidgets.QFileDialog.getOpenFileName(owner, 'Select DraftKings Entries CSV', '', 'CSV Files (*.csv)')
    if not path:return
    dialog = EntriesFirstDialog(owner, path)
    owner._entries_setup_dialog = dialog
    accepted = dialog.exec_()
    selection = dialog.selection if accepted else None
    owner._entries_setup_dialog = None
    dialog.deleteLater()
    if selection:
        from csv_loading import CsvLoadController
        if not hasattr(owner, 'csv_load'):owner.csv_load = CsvLoadController(owner)
        owner.csv_load.start(selection['salary_path'], entry_setup=selection)


def validate_commit(owner, setup):
    # Another app instance or a queued settings edit must not be overwritten by
    # a profile captured before CSV enrichment began.
    if setup['profile']:
        owner.app_settings.sync()
        current = owner._load_contest_profiles().get(setup['profile']['name'])
        if not current or profile_key(current) != profile_key(setup['profile']):
            raise ValueError('Saved contest profile changed during loading; reopen entries setup and choose its current version.')


def apply_setup(owner, setup):
    """Only called by the successful GUI commit after CSV worker retirement."""
    owner.chk_auto_entry_target.setChecked(False)
    owner.combo_field_preset.setCurrentText(setup['entry_limit'])
    (owner.spin_sd if setup['kind'] == 'showdown' else owner.spin_cl).setValue(setup['contest']['count'])
    profile = setup['profile']
    profiles = owner._load_contest_profiles()
    if profile:
        profiles[profile['name']] = deepcopy(profile)
    owner._store_contest_profiles(profiles, profile['name'] if profile else '')
    owner._set_contest_objective(profile['objective'] if profile else 'TOURNAMENT')
    if profile:
        owner.chk_nfl_contest_sim.setChecked(True)
        if setup['kind'] == 'showdown' and not owner.combo_nfl_compute_mode.currentText().startswith('Deep'):
            from compute_settings import DEEP_PROFILES
            baseline = next(iter(DEEP_PROFILES))
            owner.combo_compute_profile.setCurrentText(baseline)
            owner.combo_compute_profile.activated[str].emit(baseline)
    owner._entries_build_setup = deepcopy(setup)
    saved = remembered(owner)
    saved.pop(setup['metadata_key'], None)
    saved[setup['metadata_key']] = dict(limit=setup['entry_limit'], strategy=setup['strategy'], profile_key=profile_key(profile))
    owner.app_settings.setValue(KEY, json.dumps(dict(list(saved.items())[-200:])))
    owner._update_workspace_summary()


def setup_summary(owner):
    setup = getattr(owner, '_entries_build_setup', None)
    if not setup or owner._contest_mode() != setup['kind']:return ''
    count = owner.spin_sd.value() if setup['kind'] == 'showdown' else owner.spin_cl.value()
    changed = (count != setup['contest']['count'] or owner.combo_field_preset.currentText() != setup['entry_limit']
        or owner.chk_auto_entry_target.isChecked()
        or profile_key(owner._active_contest_profile()) != profile_key(setup['profile'])
        or owner._current_contest_objective() != (setup['profile']['objective'] if setup['profile'] else 'TOURNAMENT'))
    suffix = ' — setup changed; review before updating entries' if changed else ''
    return f"Entries: {setup['contest']['name']} / {setup['contest']['count']} captured {'entry' if setup['contest']['count'] == 1 else 'entries'}" + suffix
