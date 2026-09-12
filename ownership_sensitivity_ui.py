"""Paired ownership stress-test controls, independent of live build state."""
import datetime
import threading
from pathlib import Path
from PyQt5 import QtCore,QtWidgets
from repeatability_ui import RepeatabilityDialog
from ownership_sensitivity import load_sensitivity_bank,run_sensitivity,save_sensitivity,format_sensitivity
from build_diagnostics import build_history_path


class SensitivityWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    finished=QtCore.pyqtSignal(str)
    def __init__(self,path,batches,scenarios):
        super().__init__();self.path=path;self.batches=batches;self.scenarios=scenarios;self.stop=threading.Event()
    def run(self):
        try:
            r=run_sensitivity(self.path,batches=self.batches,scenarios=self.scenarios,cancelled=self.stop.is_set,progress=self.progress.emit)
            stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            path=Path(build_history_path()).parent/'ownership-checks'/(r['bank_id'][:12]+'-'+stamp+'.json')
            save_sensitivity(path,r);self.finished.emit(format_sensitivity(r))
        except Exception as exc:self.finished.emit('Sensitivity check stopped: '+str(exc))


class SensitivityDialog(RepeatabilityDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Ownership Sensitivity')
        self.findChildren(QtWidgets.QLabel)[0].setText('Compare baseline, higher ownership for saved favorites, and a concentrated field. Each batch runs all three profiles with identical scoring inputs and seeds. Allow several minutes per profile. Older candidate banks can be re-evaluated; all baselines are recalculated. Reports save under history/ownership-checks.')
        self.batches.setRange(1,5);self.batches.setValue(3)
        for label in self.findChildren(QtWidgets.QLabel):
            if label.text()=='Scenarios per batch':label.setText('Scenarios per profile per batch')
        self.work_label=QtWidgets.QLabel()
        self.layout().insertWidget(3,self.work_label)
        def describe_work():
            count=self.batches.value()*3
            self.work_label.setText(f'{count} simulations total; {count*self.scenarios.currentData():,} scenario/profile evaluations. Keep the app open.')
        self.batches.valueChanged.connect(describe_work);self.scenarios.currentIndexChanged.connect(describe_work);describe_work()
        self.bank.clear();folder=Path(build_history_path()).parent/'ranking-banks'
        for path in sorted(folder.glob('*.dfsbank'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                b=load_sensitivity_bank(path);p=b['payload']
                self.bank.addItem(f"{b['created_at'][:19]} | {p['kind']} | {len(p['rows'])} candidates | bank {b['bank_id'][:12]}",str(path))
            except Exception:continue
        self.start.setEnabled(self.bank.count()>0)
        self.text.setPlainText('Three hypothetical ownership profiles per batch. Original lineups and forecasts remain unchanged.' if self.bank.count() else 'No saved candidate bank found. Complete an NFL Deep build first.')
    def begin(self):
        self.thread=QtCore.QThread(self);self.worker=SensitivityWorker(self.bank.currentData(),self.batches.value(),self.scenarios.currentData())
        self.worker.moveToThread(self.thread);self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.text.setPlainText);self.worker.finished.connect(self.text.setPlainText)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.done);self.thread.start()
        for w in (self.start,self.bank,self.batches,self.scenarios):w.setEnabled(False)
        self.cancel.setEnabled(True)
