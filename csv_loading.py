"""Detached CSV preparation with GUI-thread commit only after worker retirement."""
import logging
import threading
import time
from PyQt5 import QtCore, QtWidgets
from data_io import read_players_csv
from nfl_auto_data import apply_auto_nfl_context
from mlb_auto_data import apply_auto_mlb_context
from injury_api import enrich_players_with_injuries

logger = logging.getLogger(__name__)


def detect_sport(players):
    tokens = {part.strip() for p in players or []
              for part in str(p.get('Position', '') or '').upper().replace('/', ',').replace(';', ',').split(',')
              if part.strip()}
    if tokens & {'1B','2B','3B','SS','OF','LF','CF','RF','SP','RP'}:
        return 'MLB'
    if tokens & {'QB','RB','WR','TE','DST','K'}:
        return 'NFL'
    if tokens & {'PG','SG','SF','PF','G','F'}:
        return 'NBA'
    return 'NFL'


def detect_mode(players):
    return 'showdown' if any(str(p.get('CptID') or '').strip() or
                            str(p.get('CptNamePlusID') or '').strip() for p in players) else 'classic'


class LoadCancelled(Exception):
    pass


class CsvLoadWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)

    def __init__(self, path):
        super().__init__()
        self.path = str(path)
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()

    def check(self):
        if self.stop.is_set():
            raise LoadCancelled()

    @QtCore.pyqtSlot()
    def run(self):
        started = time.perf_counter()
        timings = {}
        def stage(name, callback):
            self.check()
            begin = time.perf_counter()
            value = callback()
            timings[name] = time.perf_counter() - begin
            self.check()
            return value
        try:
            self.progress.emit(5, 100, 'Reading DraftKings CSV…')
            players = stage('parse', lambda: read_players_csv(self.path))
            self.progress.emit(18, 100, 'Initializing player fields…')
            for p in players:
                self.check()
                for key in ('LockFlex','FadeFlex','LockCpt','FadeCpt','ConfirmedLineup'):
                    p.setdefault(key, False)
                for key in ('MaxCptPct','MaxPct','MaxFlexPct','MinCptPct','MinPct',
                            'CoreClassicMinPct','CoreShowdownMinFlexPct','CoreShowdownMinCptPct'):
                    p.setdefault(key, None)
                p.setdefault('BaseProjection', float(p.get('FlexProjection', 0.0) or 0.0))
                p.setdefault('BattingOrder', 0)
                p.setdefault('Bats', '')
                p.setdefault('LineupStatus', '')
            sport = detect_sport(players)
            summary = None
            self.progress.emit(40, 100, f'Refreshing {sport} injury/status data…')
            if sport == 'NFL':
                self.progress.emit(46, 100, 'Refreshing NFL role, usage, matchup, and weather context…')
                try:
                    summary = stage('nfl_context', lambda: apply_auto_nfl_context(players))
                except LoadCancelled:
                    raise
                except Exception:
                    logger.exception('NFL auto context failed; using the existing enrichment fallback')
            elif sport == 'MLB':
                stage('injuries', lambda: enrich_players_with_injuries(players, sport=sport))
            else:
                for p in players:
                    p.update(InjuryStatus='', InjuryBodyPart='', InjuryStartDate='', AutoFadeInjury=False)
            if sport == 'MLB':
                self.progress.emit(58, 100, 'Applying MLB ballpark factors and batting orders…')
                try:
                    stage('mlb_context', lambda: apply_auto_mlb_context(players))
                except LoadCancelled:
                    raise
                except Exception:
                    logger.exception('MLB auto context failed')
            self.check()
            timings['preparation'] = time.perf_counter() - started
            self.finished.emit(dict(path=self.path, players=players, sport=sport,
                mode=detect_mode(players), live_summary=summary, timings=timings))
        except LoadCancelled:
            self.finished.emit(dict(cancelled=True))
        except Exception as exc:
            logger.exception('CSV preparation failed')
            self.finished.emit(dict(failed=True, error=str(exc)))


class CsvLoadController(QtCore.QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.thread = self.worker = self.task = self.dialog = self.payload = None
        self.closed = self.cancelled = False
        self.wait_timer = QtCore.QTimer(self)
        self.wait_timer.setInterval(100)
        self.wait_timer.timeout.connect(self.dispatch)

    @property
    def busy(self):
        return self.task is not None

    def start(self, path):
        if self.busy or self.closed:
            return False
        self.started = time.perf_counter()
        self.dispatched = None
        self.path, self.baseline = str(path), self.owner.players
        self.cancelled = False
        self.payload = None
        self.task = self.owner.tasks.start('Loading player CSV', self.cancel)
        self.dialog = QtWidgets.QProgressDialog('Waiting for current maintenance to retire…', 'Cancel', 0, 100, self.owner)
        label = QtWidgets.QLabel('Waiting for current maintenance to retire…')
        label.setTextFormat(QtCore.Qt.PlainText)
        label.setWordWrap(True)
        label.setMinimumSize(460, 48)
        self.dialog.setLabel(label)
        self.dialog.setMinimumWidth(500)
        self.dialog.setWindowTitle('Loading Player CSV')
        self.dialog.setWindowModality(QtCore.Qt.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.canceled.connect(self.cancel)
        self.dialog.show()
        self.owner.tasks.update(self.task, 'Waiting for background workers to retire safely before reading and enriching the CSV.')
        self.owner.background_queue.pump()
        self.wait_timer.start()
        self.dispatch()
        return True

    def dispatch(self):
        if self.cancelled or self.closed:
            self.wait_timer.stop()
            self.finish(dict(cancelled=True))
            return
        if self.thread is not None:
            return
        # A foreground lease prevents further maintenance dispatch. Never overlap
        # the old worker while it is still committing a checkpoint.
        if self.owner.background_queue.active is not None:
            return
        if any(k != self.task and row['ended'] is None and row['state'] != 'Queued'
               for k, row in self.owner.tasks.rows.items()):
            return
        self.wait_timer.stop()
        self.dispatched = time.perf_counter()
        self.worker = CsvLoadWorker(self.path)
        self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        self.thread.start()

    def progress(self, done, total, text):
        if self.cancelled or self.closed:
            return
        self.owner.tasks.update(self.task, text)
        if self.dialog:
            self.dialog.setLabelText(text)
            self.dialog.setValue(done)

    def receive(self, payload):
        self.payload = payload

    def cancel(self):
        self.cancelled = True
        if self.worker:
            self.worker.cancel()
        if self.busy:
            self.owner.tasks.update(self.task, 'Stopping safely; waiting for the current preparation step and worker retirement. The current slate is retained.')
        if self.dialog:
            self.dialog.setLabelText('Stopping safely; waiting for the current preparation step…')
        if self.thread is None:
            self.wait_timer.stop()
            self.finish(dict(cancelled=True))

    def retire(self):
        thread = self.thread
        stopped = self.worker.stop.is_set()
        self.thread = self.worker = None
        thread.deleteLater()
        payload = self.payload or dict(failed=True, error='CSV worker exited without a result.')
        if stopped:
            payload = dict(cancelled=True)
        self.finish(payload)
        if self.closed:
            QtCore.QTimer.singleShot(0, self.owner.close)

    def finish(self, payload):
        if self.task is None:
            return
        state, detail = 'Cancelled', 'CSV load cancelled; the previous slate remains available.'
        error = None
        if not (self.cancelled or self.closed or payload.get('cancelled')):
            if self.owner.players is not self.baseline:
                detail = 'CSV load discarded because the current slate changed.'
            elif payload.get('failed'):
                state, detail, error = 'Failed', payload['error'], payload['error']
            else:
                begin = time.perf_counter()
                try:
                    self.owner._apply_csv_payload(payload)
                    timings = dict(payload['timings'], gui_commit=time.perf_counter()-begin)
                    timings.update(wait=max(0, (self.dispatched or self.started)-self.started),
                                   total=time.perf_counter()-self.started)
                    self.owner.last_csv_load_timing = timings
                    logger.info('CSV load phase timings (seconds): %s', timings)
                    state, detail = 'Completed', f"Loaded {len(payload['players'])} players; source preparation ran off the UI thread."
                except Exception as exc:
                    logger.exception('Could not display prepared CSV')
                    state, detail, error = 'Failed', str(exc), str(exc)
        self.owner.tasks.finish(self.task, state, detail)
        self.task = self.payload = None
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        self.owner._finish_load_progress()
        if state == 'Completed':
            self.owner.idle_preparation.loaded_slate()
        if state != 'Completed':
            self.owner.status.showMessage(detail, 7000)
        if error and not self.closed:
            QtWidgets.QMessageBox.critical(self.owner, 'Load Error', error)

    def shutdown(self):
        self.closed = True
        if self.busy:
            self.cancel()
        return self.thread is not None
