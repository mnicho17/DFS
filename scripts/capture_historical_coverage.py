"""Reproduce guide screenshots from the synthetic 19-contest acceptance history."""
from pathlib import Path
import json
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_environment import install, network_attempts
install()
from PyQt5 import QtGui, QtWidgets
from test_historical_coverage import acceptance_history
from historical_coverage import load_saved, aggregate
from historical_coverage_ui import SnapshotChoiceDialog
from main_window import ResultsLearningDialog


def main():
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    if sys.platform=='win32':
        QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        app.setFont(QtGui.QFont('Segoe UI',10))
    out=Path(__file__).resolve().parents[1]/'docs'/'images'
    with tempfile.TemporaryDirectory(prefix='rl05b-guide-') as tmp:
        db=acceptance_history(Path(tmp))
        saved=load_saved(db)
        with patch('learning_db.history_db_path',return_value=str(db)):
            dialog=ResultsLearningDialog()
        dialog.resize(1140,1060)
        dialog.results_tabs.setCurrentIndex(1)
        dialog.coverage.filter.setCurrentIndex(dialog.coverage.filter.findData('OUTCOME_QUALIFIED'))
        started=time.monotonic()
        dialog.show();app.processEvents()
        dialog.grab().save(str(out/'historical-coverage.png'))
        elapsed=time.monotonic()-started
        data=next(c.data for c in saved['contests'] if 'conflicting_latest_snapshots' in c.data['conflicts'])
        choice=SnapshotChoiceDialog(data)
        choice.show();app.processEvents()
        choice.grab().save(str(out/'historical-snapshot-choice.png'))
        choice.close();dialog.close();app.processEvents()
        print(json.dumps(dict(coverage=aggregate(saved['contests']),show_seconds=elapsed,
                              network_attempts=network_attempts),indent=2))


if __name__=='__main__':main()
