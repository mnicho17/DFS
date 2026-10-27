"""Explicit Showdown shortcuts and cancellable, read-only historical review."""
import threading
from PyQt5 import QtCore,QtWidgets
from showdown_common_groups import RULES,catalog,selection


class ReviewWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    result=QtCore.pyqtSignal(object)
    error=QtCore.pyqtSignal(str)
    done=QtCore.pyqtSignal()
    def __init__(self,path,username,preset,outcomes=False):
        super().__init__();self.args=(path,username,preset);self.outcomes=outcomes;self.stop=threading.Event()
    @QtCore.pyqtSlot()
    def run(self):
        from showdown_group_review import review_history
        try:
            path,user,preset=self.args
            if self.outcomes:
                from construction_outcomes import review_outcomes
                result=review_outcomes(path,self.stop.is_set,self.progress.emit,preset=preset)
            else:result=review_history(path,self.stop.is_set,self.progress.emit,username=user,preset=preset)
            if not self.stop.is_set():self.result.emit(result)
        except Exception as exc:
            if not self.stop.is_set():self.error.emit(str(exc))
        finally:self.done.emit()


class CommonGroupsDialog(QtWidgets.QDialog):
    def __init__(self,players,groups,db_path,parent=None):
        super().__init__(parent);self.setWindowTitle('Common Showdown Groups & Historical Trends');self.resize(1000,720)
        self.options=catalog(players);self._thread=self._worker=None;self._closing=False;self.db_path=db_path
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Choose restrictions for this slate. They cover Captain and FLEX together. All are optional; historical observations never enable a rule automatically. Custom player groups are preserved, including matching custom restrictions you uncheck here.')
        note.setWordWrap(True);layout.addWidget(note)
        selected=selection(players,groups);self.checks={}
        for rule,label in RULES.items():
            check=QtWidgets.QCheckBox(label);check.setObjectName('commonGroup_'+rule)
            check.setChecked(rule in selected);check.setEnabled(bool(self.options[rule]))
            check.setToolTip('\n'.join(g['label']+' — '+', '.join(g['player_keys']) for g in self.options[rule]) or 'No applicable player combination in this slate.')
            self.checks[rule]=check;layout.addWidget(check)
        note=QtWidgets.QLabel('These are hard construction restrictions when applied. They can reduce available lineups or conflict with locks and other groups. Saved lineups are not rewritten.')
        note.setWordWrap(True);layout.addWidget(note)
        row=QtWidgets.QHBoxLayout();self.username=QtWidgets.QLineEdit();self.username.setPlaceholderText('Your exact DraftKings username (optional)');row.addWidget(self.username)
        self.preset=QtWidgets.QComboBox()
        for label,value in [('All entry limits',''),('150-Max','150-Max'),('20-Max','20-Max'),('Single Entry','Single Entry'),('3-Max','3-Max')]:self.preset.addItem(label,value)
        row.addWidget(self.preset)
        self.review=QtWidgets.QPushButton('Review saved contest trends');menu=QtWidgets.QMenu(self.review)
        menu.addAction('Construction usage and top-finish cohorts',lambda:self.start())
        menu.addAction('Finish rates matched by Captain and salary',lambda:self.start(outcomes=True))
        self.review.setMenu(menu);row.addWidget(self.review)
        self.cancel_review=QtWidgets.QPushButton('Stop review');self.cancel_review.setEnabled(False);self.cancel_review.clicked.connect(self.cancel);row.addWidget(self.cancel_review)
        layout.addLayout(row)
        self.status=QtWidgets.QLabel('Review verifies existing indexed sources. It never imports files or changes SIM, ownership or defaults.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.report=QtWidgets.QPlainTextEdit();self.report.setReadOnly(True);self.report.setPlaceholderText('Compare field usage, top-rank cutoffs, rank-1 ties and the combination of all four restrictions. Sources with missing/conflicting identity evidence are excluded.');layout.addWidget(self.report,1)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.apply_button=buttons.addButton('Apply selected groups',QtWidgets.QDialogButtonBox.AcceptRole)
        copy=buttons.addButton('Copy trend report',QtWidgets.QDialogButtonBox.ActionRole);copy.clicked.connect(lambda:QtWidgets.QApplication.clipboard().setText(self.report.toPlainText()))
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def selected(self):return {key for key,check in self.checks.items() if check.isChecked()}
    def start(self,outcomes=False):
        if self._thread is not None:return
        self._pending=None;self._error='';self._review_outcomes=outcomes
        for widget in (self.review,self.username,self.preset,self.apply_button):widget.setEnabled(False)
        self.cancel_review.setEnabled(True);self.status.setText('Verifying saved sources and reviewing indexed entries…')
        worker=self._worker=ReviewWorker(self.db_path,self.username.text().strip(),self.preset.currentData(),outcomes=outcomes)
        thread=self._thread=QtCore.QThread(self);worker.moveToThread(thread)
        thread.started.connect(worker.run);worker.progress.connect(self.status.setText)
        worker.result.connect(self.receive);worker.error.connect(self.receive_error)
        worker.done.connect(thread.quit);worker.done.connect(worker.deleteLater);thread.finished.connect(self.retire);thread.start()
    def receive(self,result):
        if self._worker is not None and not self._worker.stop.is_set():self._pending=result
    def receive_error(self,text):self._error=text
    def retire(self):
        stopped=self._worker.stop.is_set();thread=self._thread;self._thread=self._worker=None;thread.deleteLater()
        for widget in (self.review,self.username,self.preset,self.apply_button):widget.setEnabled(True)
        self.cancel_review.setEnabled(False)
        if stopped:self.status.setText('Review stopped. No partial findings or rule changes were applied.')
        elif self._pending is None:self.status.setText('Review unavailable: '+self._error)
        else:
            if self._review_outcomes:
                from construction_outcomes import render
            else:
                from showdown_group_review import render
            self.report.setPlainText(render(self._pending));self.status.setText('Review complete. Findings are descriptive; select rules yourself before applying.')
        if self._closing:super().reject()
    def cancel(self):
        if self._worker is not None:self._worker.stop.set();self._pending=None;self.cancel_review.setEnabled(False)
    def accept(self):
        if self._thread is None:super().accept()
    def reject(self):
        if self._thread is not None:self._closing=True;self.cancel()
        else:super().reject()
    def closeEvent(self,event):
        if self._thread is not None:self._closing=True;self.cancel();event.ignore()
        else:super().closeEvent(event)
