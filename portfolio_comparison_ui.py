"""Optional background portfolio experiment, separate from production builds."""
import datetime
import threading
from pathlib import Path
from PyQt5 import QtCore,QtWidgets
from repeatability_ui import RepeatabilityDialog
from ownership_sensitivity import load_sensitivity_bank
from portfolio_comparison import compare_portfolios,format_report
from repeatability import atomic_json
from build_diagnostics import build_history_path


class ComparisonWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    finished=QtCore.pyqtSignal(str)
    def __init__(self,path,count,scenarios):
        super().__init__();self.path=path;self.count=count;self.scenarios=scenarios;self.stop=threading.Event()
    def run(self):
        try:
            r=compare_portfolios(self.path,self.count,self.scenarios,self.stop.is_set,self.progress.emit)
            stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            path=Path(build_history_path()).parent/'portfolio-checks'/(r['bank_id'][:12]+'-'+stamp+'.json')
            atomic_json(path,r);text=format_report(r);path.with_suffix('.txt').write_text(text,encoding='utf-8')
            self.finished.emit(text)
        except Exception as exc:self.finished.emit('Comparison stopped: '+str(exc))


class PortfolioComparisonDialog(RepeatabilityDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Portfolio Comparison')
        self.layout().itemAt(0).widget().setText('Compare the current portfolio selector with an experimental repeated-core penalty. Two simulation passes: selection, then fresh evaluation. Allow several minutes. Original entries stay unchanged. Full reports save automatically under history/portfolio-checks. This is an optional development check, not a required contest step.')
        self.bank.clear()
        folder=Path(build_history_path()).parent/'ranking-banks'
        for path in sorted(folder.glob('*.dfsbank'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                b=load_sensitivity_bank(path);p=b['payload']
                self.bank.addItem(f"{b['created_at'][:19]} | {p['kind']} | {len(p['rows'])} candidates | {p['input_id'][:12]}",str(path))
            except Exception:continue
        self.batches.setRange(1,150);self.batches.setValue(150)
        self.layout().itemAt(2).layout().labelForField(self.batches).setText('Entries per portfolio')
        self.scenarios.setCurrentIndex(0)
        self.start.setEnabled(self.bank.count()>0)
        self.text.setPlainText('Choose a bank with more candidates than requested entries. Both selectors use common diagnostic rules; this does not reproduce your original build settings. Previously saved banks can be rescored under current code.' if self.bank.count() else 'No saved Deep candidate banks found. Run a Deep build first.')
    def begin(self):
        self.thread=QtCore.QThread(self)
        self.worker=ComparisonWorker(self.bank.currentData(),self.batches.value(),self.scenarios.currentData())
        self.worker.moveToThread(self.thread);self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.text.setPlainText);self.worker.finished.connect(self.text.setPlainText)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.done);self.thread.start()
        for w in (self.start,self.bank,self.batches,self.scenarios):w.setEnabled(False)
        self.cancel.setEnabled(True)
