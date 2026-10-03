"""Reproducible synthetic screenshots through the production risk worker."""
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_environment import install, network_attempts
install()
from PyQt5 import QtCore, QtGui, QtWidgets
from main_window import ResultsLearningDialog
from test_portfolio_risk_evidence import qualified_patterns, logical_db, source_bytes
import test_historical_identity as fixtures


def main():
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    if sys.platform=='win32':
        QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        app.setFont(QtGui.QFont('Segoe UI',10))
    out=Path(__file__).resolve().parents[1]/'docs'/'images'
    f=fixtures.HistoricalIdentityTests();f.setUp()
    dialog=None
    try:
        qualified_patterns(f)
        # Add a separate descriptive-only original export in this disposable DB.
        from learning_db import record_export
        players=f.snap['inputs']['players']
        record_export(kind='showdown',sport='NFL',lineups=[dict(Captain=players[0],Flex=players[1:6])],
            rows=[],salary_cap=50000,export_path='synthetic.csv',validation={},db_path=str(f.db))
        dialog=ResultsLearningDialog();risk=dialog.risk
        dialog.db_path=str(f.db);dialog.coverage.db_path=str(f.db);risk.db_path=str(f.db)
        dialog.results_tabs.setCurrentWidget(risk)
        dialog.resize(1320,1280);dialog.show();risk.reload();risk.source.setCurrentIndex(1)
        before=logical_db(f.db);files=source_bytes(f.root)
        def capture():
            dialog.start_portfolio_risk(risk.request())
            deadline=time.monotonic()+20
            while dialog._import_thread is not None and time.monotonic()<deadline:
                app.processEvents();time.sleep(.003)
            assert dialog._import_thread is None and risk.copy_button.isEnabled(),risk.status.text()
            app.processEvents()
        capture()
        risk.target_a.setCurrentIndex(risk.target_a.findData('100'))
        risk.target_b.setCurrentIndex(risk.target_b.findData('101'))
        for i in range(risk.alternatives.count()):
            item=risk.alternatives.item(i)
            item.setSelected(item.data(QtCore.Qt.UserRole) in ('106','108'))
        capture()
        risk.tabs.setCurrentIndex(0);app.processEvents();risk.grab().save(str(out/'portfolio-risk-overview.png'))
        risk.tabs.setCurrentIndex(1);app.processEvents();risk.grab().save(str(out/'portfolio-risk-stress.png'))
        risk.tabs.setCurrentIndex(2);app.processEvents();risk.grab().save(str(out/'portfolio-risk-coverage.png'))
        risk.mode.setCurrentIndex(risk.mode.findData('saved_export'));risk.source.setCurrentIndex(1);capture()
        risk.tabs.setCurrentIndex(1);app.processEvents();risk.grab().save(str(out/'portfolio-risk-export.png'))
        assert logical_db(f.db)==before and source_bytes(f.root)==files
        print('Four production-widget captures; source bytes and logical DB unchanged; network attempts:',network_attempts)
    finally:
        if dialog:
            dialog.close();dialog.deleteLater();app.processEvents()
        f.tearDown()


if __name__=='__main__':main()
