"""Background comparison of saved NFL shortlists."""
import datetime
import threading
from pathlib import Path
from PyQt5 import QtCore, QtWidgets
from repeatability import load_bank, run_repeatability, save_report, format_report
from build_diagnostics import build_history_path


class RepeatWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    finished=QtCore.pyqtSignal(str)
    def __init__(self,path,batches,scenarios):
        super().__init__();self.path=path;self.batches=batches;self.scenarios=scenarios;self.stop=threading.Event()
    def run(self):
        try:
            report=run_repeatability(self.path,batches=self.batches,scenarios=self.scenarios,
                cancelled=self.stop.is_set,progress=self.progress.emit)
            stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            path=Path(build_history_path()).parent/'ranking-checks'/(report['bank_id'][:12]+'-'+stamp+'.json')
            save_report(path,report)
            self.finished.emit(format_report(report))
        except Exception as exc:self.finished.emit('Comparison stopped: '+str(exc))


class RepeatabilityDialog(QtWidgets.QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Ranking Repeatability');self.resize(850,600);self.thread=None
        layout=QtWidgets.QVBoxLayout(self)
        intro=QtWidgets.QLabel('Score one saved Deep shortlist against fresh scenarios and opponents in each batch. Allow several minutes per batch and keep the app open. '
            'Full results save under history/ranking-checks as text, CSV and JSON. Old snapshots alone cannot recover a shortlist; run Deep once after updating.')
        intro.setWordWrap(True);layout.addWidget(intro)
        self.bank=QtWidgets.QComboBox();folder=Path(build_history_path()).parent/'ranking-banks'
        for path in sorted(folder.glob('*.dfsbank'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                b=load_bank(path);p=b['payload']
                self.bank.addItem(f"{b['created_at'][:19]} | {p['kind']} | {len(p['rows'])} candidates | input {p['input_id'][:12]}",str(path))
            except Exception:continue
        layout.addWidget(self.bank)
        self.batches=QtWidgets.QSpinBox();self.batches.setRange(2,10);self.batches.setValue(5)
        self.scenarios=QtWidgets.QComboBox()
        for count in (2000,5000,10000):self.scenarios.addItem(f'{count:,}',count)
        self.scenarios.setCurrentIndex(1)
        form=QtWidgets.QFormLayout();form.addRow('Independent batches',self.batches);form.addRow('Scenarios per batch',self.scenarios);layout.addLayout(form)
        row=QtWidgets.QHBoxLayout();self.start=QtWidgets.QPushButton('Run comparison');self.cancel=QtWidgets.QPushButton('Cancel');self.copy=QtWidgets.QPushButton('Copy Report')
        for w in (self.start,self.cancel,self.copy):row.addWidget(w)
        layout.addLayout(row);self.cancel.setEnabled(False);self.start.setEnabled(self.bank.count()>0)
        self.text=QtWidgets.QPlainTextEdit();self.text.setReadOnly(True);layout.addWidget(self.text)
        if not self.bank.count():self.text.setPlainText('No compatible shortlist saved yet. Run an NFL Deep build, then reopen this window. Banks from different simulation code versions are not mixed.')
        self.start.clicked.connect(self.begin);self.cancel.clicked.connect(self.stop)
        self.copy.clicked.connect(lambda:QtWidgets.QApplication.clipboard().setText(self.text.toPlainText()))
    def begin(self):
        self.thread=QtCore.QThread(self);self.worker=RepeatWorker(self.bank.currentData(),self.batches.value(),self.scenarios.currentData())
        self.worker.moveToThread(self.thread);self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.text.setPlainText);self.worker.finished.connect(self.text.setPlainText)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.done);self.thread.start()
        for w in (self.start,self.bank,self.batches,self.scenarios):w.setEnabled(False)
        self.cancel.setEnabled(True)
    def stop(self):
        if self.thread is not None:self.worker.stop.set();self.cancel.setEnabled(False)
    def done(self):
        for w in (self.start,self.bank,self.batches,self.scenarios):w.setEnabled(True)
        self.cancel.setEnabled(False);self.thread.deleteLater();self.thread=None
    def reject(self):
        if self.thread is not None:self.stop();return
        super().reject()
    def closeEvent(self,event):
        if self.thread is not None:self.stop();event.ignore()
        else:event.accept()
