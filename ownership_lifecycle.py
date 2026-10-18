"""Session-local ownership job identity, result guards and cooperative retirement.

No estimator, optimizer or scoring policy lives here.
"""
import hashlib
import json
import logging
from PyQt5 import QtCore

logger = logging.getLogger(__name__)


def player_signature(players):
    # Strict session-local change detection, including unknown/nested fields.
    # This is never saved or used to qualify historical evidence.
    raw = json.dumps(players, sort_keys=True, separators=(',', ':'), default=repr)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def current_settings(owner):
    mode = owner._contest_mode()
    cap = owner.edit_sd_cap if mode == 'showdown' else owner.edit_cl_cap
    return dict(mode=mode, sport=owner._current_sport(),
                cap=float(owner._safe_float(cap.text(), 50000.0) or 50000.0),
                template=bool(mode=='showdown' and owner.chk_sd_template_sim.isChecked()))


class OwnershipRun(QtCore.QObject):
    def __init__(self, owner, worker):
        source, signature = owner.players, player_signature(owner.players)
        super().__init__(owner)
        self.owner, self.worker = owner, worker
        self.source, self.signature = source, signature
        self.settings = dict(mode=worker.mode, sport=worker.sport,
                             cap=worker.salary_cap, template=worker.template_sim)
        self.thread = QtCore.QThread(self)
        self.result = self.error = None
        self.cancelled = self.closing = self.retired = False
        self.task = None

    def start(self):
        owner, worker = self.owner, self.worker
        owner._own_progress.setValue(0)
        owner._own_progress.setMaximum(worker.num_sims)
        owner._own_progress.setVisible(True)
        owner._own_eta.setText('Starting…')
        owner._own_eta.setVisible(True)
        owner.status.showMessage(f'Simulating ownership ({worker.mode}, {worker.num_sims:,} lineups)…')
        self.task = owner.tasks.start('Ownership estimation', self.cancel)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self.progress)
        worker.finished.connect(self.receive)
        worker.error.connect(self.failed)
        worker.settled.connect(self.thread.quit)
        worker.settled.connect(worker.deleteLater)
        self.thread.finished.connect(self.retire)
        self.thread.start()

    def matches_scope(self):
        return (self.owner._ownership_run is self and self.owner.players is self.source
                and current_settings(self.owner)==self.settings)

    def progress(self, done, total, text):
        if self.cancelled or self.closing or self.retired:
            return
        self.owner.tasks.update(self.task, f'{done:,}/{total:,} · {text}')
        if self.matches_scope():
            self.owner._on_own_sim_progress(done, total, text)

    def receive(self, result):
        self.result = result

    def failed(self, message):
        self.error = str(message)

    def cancel(self):
        if self.retired or self.cancelled:
            return
        self.cancelled = True
        self.worker.cancel()
        if self.task is not None:
            self.owner.tasks.update(self.task, 'Stop requested; waiting for ownership worker retirement. Existing estimates remain available.')
        if self.owner._ownership_run is self:
            self.owner._own_eta.setText('Stopping safely…')

    def retire(self):
        if self.retired:
            return
        self.retired = True
        owner = self.owner
        active = owner._ownership_run is self
        state, detail = 'Cancelled', 'Ownership estimation stopped; existing estimates retained.'
        try:
            if not (self.cancelled or self.closing or self.worker._cancel or owner._ownership_closing):
                try:
                    matches = self.matches_scope() and player_signature(owner.players)==self.signature
                except (ValueError, TypeError):
                    matches = False
                if not matches:
                    state, detail = 'Discarded', 'Ownership inputs or slate changed; captured estimate was not applied. Recalculate ownership for the current inputs.'
                elif self.error is not None:
                    state, detail = 'Failed', self.error
                    owner._on_own_sim_error(self.error)
                elif self.result is None:
                    state, detail = 'Failed', 'Ownership worker exited without a result; existing estimates retained.'
                    owner.status.showMessage(detail, 6000)
                else:
                    owner._on_own_sim_finished(self.result)
                    state, detail = 'Completed', 'Ownership estimation applied to its unchanged captured slate after worker retirement.'
        except Exception as exc:
            logger.exception('Ownership completion display failed')
            state, detail = 'Failed', str(exc)
        finally:
            if owner._ownership_run is self:
                owner._own_thread = owner._own_worker = owner._ownership_run = None
                owner._own_progress.setVisible(False)
                owner._own_eta.setVisible(False)
                if state in ('Discarded','Cancelled') and not self.closing:
                    owner.status.showMessage(detail, 7000)
            owner.tasks.finish(self.task, state, detail)
            self.thread.deleteLater()
            self.result = self.error = self.source = self.worker = None
            self.deleteLater()
            if self.closing and active:
                QtCore.QTimer.singleShot(0, owner.close)

    def shutdown(self):
        self.closing = True
        self.cancel()
        return not self.retired
