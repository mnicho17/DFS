"""Background projection comparison, with the same cancellation/save workflow."""
import datetime
from pathlib import Path
from PyQt5 import QtWidgets
from build_diagnostics import build_history_path
from ownership_sensitivity_ui import SensitivityWorker,SensitivityDialog
from projection_sensitivity import run_projection,save_projection,format_projection,comparison_targets
from ownership_sensitivity import load_sensitivity_bank

class ProjectionWorker(SensitivityWorker):
    def run(self):
        try:
            report=run_projection(self.path,batches=self.batches,scenarios=self.scenarios,cancelled=self.stop.is_set,progress=self.progress.emit)
            stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            path=Path(build_history_path()).parent/'projection-checks'/(report['bank_id'][:12]+'-'+stamp+'.json')
            save_projection(path,report);self.finished.emit(format_projection(report))
        except Exception as exc:self.finished.emit('Projection check stopped: '+str(exc))

class ProjectionDialog(SensitivityDialog):
    worker_type=ProjectionWorker
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Projection Sensitivity')
        self.findChildren(QtWidgets.QLabel)[0].setText('Compare baseline, 15% lower production for favorites together and one player at a time, plus wider limited-history outcomes. Up to eight profiles per batch. These are scoring stress assumptions, not automatic forecast corrections. Opponent rosters and underlying game scenarios stay fixed within each batch. Older banks are rescored. Reports save under history/projection-checks.')
        self.review_button.clicked.disconnect()
        from ownership_review_ui import open_review
        self.review_button.clicked.connect(lambda:open_review(self,comparison='projection'))
        if self.bank.count():self.text.setPlainText('Up to eight profiles per batch, including five individual-player tests. Workload shortfall uses a scoring proxy; touches are not redistributed. Missing usage history does not establish rookie status. Keep the app open; only complete batches count.')

        self.batches.valueChanged.disconnect()
        self.scenarios.currentIndexChanged.disconnect()
        self.batches.valueChanged.connect(self.describe_work)
        self.scenarios.currentIndexChanged.connect(self.describe_work)
        self.bank.currentIndexChanged.connect(self.describe_work)
        self.describe_work()

    def describe_work(self):
        try:
            profiles=len(comparison_targets(load_sensitivity_bank(self.bank.currentData())['payload']))
        except (OSError,ValueError,TypeError):
            profiles=8
        count=self.batches.value()*profiles
        self.work_label.setText(f'{profiles} profiles per batch; {count} simulations total; {count*self.scenarios.currentData():,} scenario/profile evaluations. Allow roughly {profiles/3:.1f}x the previous three-profile workload.')
