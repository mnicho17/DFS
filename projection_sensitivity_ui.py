"""Background projection comparison, with the same cancellation/save workflow."""
import datetime
from pathlib import Path
from PyQt5 import QtWidgets
from build_diagnostics import build_history_path
from ownership_sensitivity_ui import SensitivityWorker,SensitivityDialog
from projection_sensitivity import run_projection,save_projection,format_projection

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
        self.findChildren(QtWidgets.QLabel)[0].setText('Compare baseline, 15% lower production for the five most-used skill players, and wider limited-history outcomes (0.75x or 1.25x, equally likely). These are scoring stress assumptions, not automatic forecast corrections. Opponent rosters and underlying game scenarios stay fixed within each batch. Older banks are rescored. Reports save under history/projection-checks.')
        self.review_button.clicked.disconnect()
        from ownership_review_ui import open_review
        self.review_button.clicked.connect(lambda:open_review(self,comparison='projection'))
        if self.bank.count():self.text.setPlainText('Three profiles per batch. Workload shortfall uses a scoring proxy; touches are not redistributed. Missing usage history does not establish rookie status. Keep the app open; only complete batches count.')
