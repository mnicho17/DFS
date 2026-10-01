"""Synthetic Hindsight screenshots through the production UI/worker/CBC path."""
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_environment import install,network_attempts
install()
from PyQt5 import QtGui,QtWidgets
from main_window import ResultsLearningDialog
from test_hindsight_evidence import SourceFixture
from test_portfolio_risk_evidence import logical_db,source_bytes
import historical_identity as hi


def main():
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    if sys.platform=='win32':
        QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf');app.setFont(QtGui.QFont('Segoe UI',10))
    f=SourceFixture();dialog=None
    try:
        f.snap['inputs']['rules']['groups']=[dict(type='never_together',player_keys=['100','101'])]
        f.snapshot();hi.reconcile(f.db)
        dialog=ResultsLearningDialog();view=dialog.hindsight
        dialog.db_path=str(f.db);dialog.coverage.db_path=str(f.db);view.db_path=str(f.db)
        dialog.results_tabs.setCurrentWidget(view);dialog.resize(1380,1120);dialog.show()
        view.reload();view.source.setCurrentIndex(1);view.compare.setChecked(True)
        def capture():
            before=logical_db(f.db);files=source_bytes(f.root)
            dialog.start_hindsight(view.request());end=time.monotonic()+35
            while dialog._import_thread is not None and time.monotonic()<end:app.processEvents();time.sleep(.003)
            assert dialog._import_thread is None and view.copy_button.isEnabled(),view.status.text()
            assert logical_db(f.db)==before and source_bytes(f.root)==files
            app.processEvents()
        out=Path(__file__).resolve().parents[1]/'docs'/'images'
        capture();assert view.report.data['scopes']['snapshot']['points']=='52'
        view.grab().save(str(out/'hindsight-overview.png'))
        # Actual source revision with an unsupported local rule: preserve the
        # independent supplied-pool result rather than manufacture UI state.
        f.snap['inputs']['rules']['unsupported_minimum_stack']=3;f.snapshot();hi.reconcile(f.db)
        view.reload();capture();assert view.report.data['scopes']['snapshot']['status']=='unsupported_rules'
        view.grab().save(str(out/'hindsight-unavailable.png'))
        print('Two production Qt/CBC captures; logical DB/source bytes unchanged; unexpected network attempts:',network_attempts)
    finally:
        if dialog:dialog.close();dialog.deleteLater();app.processEvents()
        f.close()


if __name__=='__main__':main()
