"""Current-slate QB fade controls and a cancellable conditional point review."""
from copy import deepcopy
import threading
from PyQt5 import QtCore, QtWidgets
from nfl_eligibility import eligible_players, apply_qb_eligibility
from portfolio_rules import player_key, lineup_players
from qb_coverage import normalize_coverage, coverage_report, format_coverage, paired_point_review, slate_identity


class PointReviewWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, rows, players, config, kind, elapsed, efficiency):
        super().__init__()
        self.args = deepcopy((rows,players,config))
        self.options = dict(kind=kind,elapsed=elapsed,receiver_efficiency=efficiency)
        self.cancel = threading.Event()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            self.finished.emit(paired_point_review(*self.args,**self.options,cancelled=self.cancel.is_set))
        except Exception as exc:
            self.failed.emit(str(exc))


class QBCoverageDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('QB Coverage')
        self.resize(760,660)
        self.setModal(True)
        self.window = parent
        self.kind = parent._contest_mode()
        self.players = eligible_players(apply_qb_eligibility(deepcopy(parent.players)))
        self.slate_id = slate_identity(parent.players)
        self.rows = deepcopy(parent.last_showdown if self.kind == 'showdown' else parent.last_classic)
        self.config = deepcopy(getattr(parent,'_qb_coverage',None) or {})
        self.thread = None
        self.worker = None
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('Select starting QBs explicitly. Fade targets are minimum entry counts and stay hard alongside your existing rules. A QB fade can still depend on his receivers. Controls apply to the current slate; ordinary builds stay unchanged while disabled.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.enabled = QtWidgets.QCheckBox('Enable QB fade coverage for builds')
        self.enabled.setObjectName('enableQBCoverage')
        self.enabled.setChecked(bool(self.config.get('enabled')))
        layout.addWidget(self.enabled)
        form = QtWidgets.QFormLayout()
        self.qbs = []
        for field,label in (('qb_a','QB A'),('qb_b','QB B')):
            combo = QtWidgets.QComboBox()
            combo.setObjectName(field)
            combo.addItem('Select starting QB','')
            for p in self.players:
                if str(p.get('Position') or '').upper() == 'QB':
                    combo.addItem(f"{p.get('Name')} ({p.get('Team')})",player_key(p))
            index = combo.findData(self.config.get(field,''))
            combo.setCurrentIndex(max(0,index))
            self.qbs.append(combo)
            form.addRow(label,combo)
        self.targets = {}
        for field,label in (('fade_a','At least this many without A'),('fade_b','At least this many without B'),('neither','At least this many without either QB')):
            spin = QtWidgets.QSpinBox()
            spin.setObjectName(field)
            spin.setRange(0,10000)
            spin.setValue(self.config.get(field,0))
            self.targets[field] = spin
            form.addRow(label,spin)
        self.elapsed = QtWidgets.QDoubleSpinBox()
        self.elapsed.setObjectName('exitElapsedPct')
        self.elapsed.setRange(0,100)
        self.elapsed.setValue(25)
        self.elapsed.setSuffix('%')
        form.addRow('Game elapsed when QB exits',self.elapsed)
        self.efficiency = QtWidgets.QDoubleSpinBox()
        self.efficiency.setObjectName('receiverRemainingEfficiency')
        self.efficiency.setRange(0,100)
        self.efficiency.setValue(75)
        self.efficiency.setSuffix('%')
        form.addRow('WR/TE efficiency for remaining game',self.efficiency)
        layout.addLayout(form)
        assumptions = QtWidgets.QLabel('Point review uses 250 paired draws on frozen current inputs. It assumes uniform production before exit and your WR/TE efficiency afterwards. RB receiving, K/DST effects and replacement playing time are unmodeled. It estimates neither injury likelihood nor cash coverage. Backup allocation is queued separately.')
        assumptions.setWordWrap(True)
        layout.addWidget(assumptions)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setObjectName('qbCoverageReport')
        self.output.setReadOnly(True)
        layout.addWidget(self.output,1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.review = buttons.addButton('Review current output',QtWidgets.QDialogButtonBox.ActionRole)
        self.review.setObjectName('reviewQBCoverage')
        self.review.clicked.connect(self.start_review)
        self.apply = buttons.addButton('Apply to builds',QtWidgets.QDialogButtonBox.AcceptRole)
        self.apply.setObjectName('applyQBCoverage')
        self.apply.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self, enabled=True):
        return normalize_coverage(dict(enabled=enabled,kind=self.kind,slate_id=self.slate_id,qb_a=self.qbs[0].currentData(),qb_b=self.qbs[1].currentData(),
                                       **{key:spin.value() for key,spin in self.targets.items()}))

    def save(self):
        try:
            config = self.chosen(self.enabled.isChecked())
            if config:
                coverage_report([],self.players,config,self.kind)
        except ValueError as exc:
            self.output.setPlainText(str(exc))
            return
        self.window._qb_coverage = config
        self.accept()

    def start_review(self):
        try:
            config = self.chosen()
            keys = {player_key(p) for p in self.players}
            if not self.rows or any(player_key(p) not in keys for lu in self.rows for p in lineup_players(lu,self.kind)):
                raise ValueError('Build output for the current slate before reviewing QB coverage.')
            self.output.setPlainText(format_coverage(coverage_report(self.rows,self.players,config,self.kind))+'\nCalculating paired conditional points…')
        except ValueError as exc:
            self.output.setPlainText(str(exc))
            return
        self.review.setEnabled(False)
        self.apply.setEnabled(False)
        for widget in self.qbs+list(self.targets.values())+[self.elapsed,self.efficiency,self.enabled]:
            widget.setEnabled(False)
        self.thread = QtCore.QThread(self)
        self.worker = PointReviewWorker(self.rows,self.players,config,self.kind,self.elapsed.value()/100,self.efficiency.value()/100)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.complete)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.idle)
        self.thread.start()

    def complete(self, result):
        lines = [format_coverage(result['coverage']), f"Paired scenarios: {result['scenarios']}; cancelled: {result['cancelled']}",
                 f"Exit at {result['elapsed_fraction']*100:g}% elapsed; remaining WR/TE efficiency {result['remaining_wr_te_efficiency']*100:g}%.",
                 result['competitive_threshold']]
        lines.extend(f"{case['label']}: mean {case['mean_entry_points']:.2f} pts ({case['mean_change']:+.2f}); competitive {case['competitive_entry_pct']:.1f}%" for case in result['cases'])
        lines.append(result['limitations'])
        self.output.setPlainText('\n'.join(lines))

    def failed(self,message):
        self.output.setPlainText(message)

    def idle(self):
        self.review.setEnabled(True)
        self.apply.setEnabled(True)
        for widget in self.qbs+list(self.targets.values())+[self.elapsed,self.efficiency,self.enabled]:
            widget.setEnabled(True)

    def reject(self):
        if self.thread and self.thread.isRunning():
            self.worker.cancel.set()
            self.output.appendPlainText('Cancelling point review; close when the review stops.')
            return
        super().reject()

    def closeEvent(self,event):
        if self.thread and self.thread.isRunning():
            self.worker.cancel.set()
            event.ignore()
        else:
            event.accept()


def open_qb_coverage(window):
    if window._current_sport() != 'NFL':
        window.status.showMessage('QB Coverage is available for NFL.',5000)
        return
    try:
        QBCoverageDialog(window).exec_()
    except ValueError as exc:
        QtWidgets.QMessageBox.warning(window,'QB Coverage',str(exc))
