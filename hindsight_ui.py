"""Thin read-only hindsight view; ResultsLearningDialog owns job retirement."""
import copy
import json
from pathlib import Path
import threading
import time
from PyQt5 import QtCore,QtWidgets

from analysis_imports import ImportCancelled
from review_report import Cancelled as ReaderCancelled
from hindsight_contract import Cancelled,SCOPE,points
from hindsight_evidence import capture,source_catalog,fingerprint
from hindsight_solver import calculate,summary
from entry_review_ui import NumberItem


class HindsightWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(int,int,str)
    finished=QtCore.pyqtSignal(dict)
    error=QtCore.pyqtSignal(str)

    def __init__(self,db_path,request):
        super().__init__();self.db_path=str(Path(db_path).absolute());self.history_root=str(Path(self.db_path).parent)
        self.request=copy.deepcopy(request);self.cancelled=threading.Event()

    def request_cancel(self):self.cancelled.set()

    @QtCore.pyqtSlot()
    def run(self):
        started=time.monotonic();progress=lambda text:self.progress.emit(0,0,text)
        try:
            request=self.request
            captured=capture(self.db_path,request['selection'],history_root=self.history_root,
                             cancelled=self.cancelled.is_set,progress=progress)
            report=calculate(captured,seconds=request['seconds'],tie_limit=request['tie_limit'],
                             cancelled=self.cancelled.is_set,progress=progress)
            statuses=[s['status'] for s in report.data['scopes'].values()]
            if any(s in ('solver_error','validation_failed') for s in statuses):
                self.finished.emit(dict(error='Solver or independent validation failed. No revised report was applied.'));return
            self.finished.emit(dict(report=report,fingerprint=request['fingerprint'],seconds=time.monotonic()-started))
        except (Cancelled,ImportCancelled,ReaderCancelled):self.finished.emit(dict(cancelled=True))
        except OverflowError:self.finished.emit(dict(error='Evidence read limit reached. No partial universe was solved.'))
        except (ValueError,UnicodeError):self.finished.emit(dict(error='Selected evidence is unavailable, changed or unsupported. Recheck Historical Coverage.'))
        except Exception:self.finished.emit(dict(error='Hindsight capture failed. Previous completed output retained.'))


class HindsightWidget(QtWidgets.QWidget):
    requested=QtCore.pyqtSignal(dict)

    def __init__(self,db_path,parent=None):
        super().__init__(parent);self.db_path=db_path;self.report=None;self.cancel_token=None;self.started=None
        layout=QtWidgets.QVBoxLayout(self)
        self.scope=QtWidgets.QLabel(SCOPE);self.scope.setWordWrap(True);layout.addWidget(self.scope)
        choices=QtWidgets.QHBoxLayout();self.source=QtWidgets.QComboBox()
        self.source.setMinimumContentsLength(24);self.source.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.compare=QtWidgets.QCheckBox('Compare exact qualified snapshot restrictions')
        self.archive=QtWidgets.QComboBox();self.reload_button=QtWidgets.QPushButton('Refresh cached choices')
        for w in (self.source,self.compare,self.archive,self.reload_button):choices.addWidget(w)
        layout.addLayout(choices)
        self.source_note=QtWidgets.QLabel('Cached choices are prerequisites only. Capture requalifies immutable sources; opening this tab does not solve.')
        self.source_note.setWordWrap(True);layout.addWidget(self.source_note)
        self.run_button=QtWidgets.QPushButton('Capture / Solve');layout.addWidget(self.run_button)
        self.status=QtWidgets.QLabel('No hindsight capture. One shared solver budget: 30 seconds; at most 20 tie examples per scope.')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.tabs=QtWidgets.QTabWidget();layout.addWidget(self.tabs,1)
        overview=QtWidgets.QWidget();inner=QtWidgets.QVBoxLayout(overview)
        self.overview=QtWidgets.QPlainTextEdit();self.overview.setReadOnly(True)
        self.lineups=QtWidgets.QTableWidget();self.lineups.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        inner.addWidget(self.overview,1);inner.addWidget(self.lineups,1);self.tabs.addTab(overview,'Scores and witnesses')
        self.evidence=QtWidgets.QPlainTextEdit();self.evidence.setReadOnly(True);self.tabs.addTab(self.evidence,'Evidence and rules')
        row=QtWidgets.QHBoxLayout();self.private=QtWidgets.QCheckBox('Include player names and roster witnesses (private)')
        self.copy_button=QtWidgets.QPushButton('Copy Summary');self.copy_button.setEnabled(False)
        row.addWidget(self.private);row.addWidget(self.copy_button);layout.addLayout(row)
        self.timer=QtCore.QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self._tick)
        self.source.currentIndexChanged.connect(self._choices);self.compare.toggled.connect(self._invalidate)
        self.archive.currentIndexChanged.connect(self._invalidate);self.reload_button.clicked.connect(self.reload)
        self.run_button.clicked.connect(lambda:self.requested.emit(self.request()));self.copy_button.clicked.connect(self.copy_summary)
        self.reload()

    def reload(self):
        selected=(self.source.currentData() or {}).get('contest_id');self.source.blockSignals(True);self.source.clear()
        self.source.addItem('Choose one historical contest',None)
        try:catalog=source_catalog(self.db_path)
        except Exception:catalog=[]
        for source in catalog:
            i=source['identity'];self.source.addItem(f"{i.get('sport') or '?'} / {i.get('format') or '?'} / {i.get('slate_date') or '?'} | {source['contest_id']}",source)
            if source['contest_id']==selected:self.source.setCurrentIndex(self.source.count()-1)
        self.source.blockSignals(False);self._choices()

    def _choices(self):
        self.archive.blockSignals(True);self.archive.clear();self.archive.addItem('Snapshot only; no build selected',None)
        source=self.source.currentData() or {}
        for a in source.get('archives',[]):self.archive.addItem(f"{a['recorded_at']} | {a['output_count']} outputs | {a['archive_id']}",a['archive_id'])
        self.archive.blockSignals(False)
        s=source.get('snapshot')
        self.source_note.setText(('Qualified cached snapshot: '+s['recorded_at']+' | '+s['snapshot_digest'] if s else
            'No qualified snapshot in cached evidence; the supplied-pool scope is independently requalified.')+
            ' Sources are revalidated only on Capture / Solve; submission is not established.')
        self._invalidate()

    def suggest_contest(self,ident):
        if self.source.currentData():return
        for i in range(1,self.source.count()):
            if self.source.itemData(i)['contest_id']==ident:self.source.setCurrentIndex(i);return

    def request(self):
        source=self.source.currentData() or {};s=source.get('snapshot') or {}
        selection=dict(contest_id=source.get('contest_id'),snapshot_digest=(s.get('snapshot_digest') or 'unavailable') if self.compare.isChecked() else None,
                       archive_id=self.archive.currentData() if self.compare.isChecked() else None)
        return dict(selection=selection,seconds=30,tie_limit=20,fingerprint=fingerprint(selection))

    def _invalidate(self):
        if self.cancel_token:self.cancel_token.set()
        self.copy_button.setEnabled(False);self.run_button.setEnabled(bool(self.source.currentData()))
        self.archive.setEnabled(self.compare.isChecked())
        if self.report:self.status.setText('Prior completed output retained; selections changed. Capture again before copying.')

    def begin(self,token):
        self.cancel_token=token;self.started=time.monotonic();self.phase='Capturing immutable evidence'
        self.copy_button.setEnabled(False);self.timer.start();self._tick()
    def progress(self,text):
        if self.started is not None:self.phase=text;self._tick()
    def _tick(self):
        if self.started is not None:self.status.setText(f'{self.phase}… {time.monotonic()-self.started:.0f}s elapsed. Cancel Operation remains available.')

    def finish(self,result,cancelled=False):
        self.timer.stop();self.started=None;self.cancel_token=None
        if cancelled or result.get('cancelled') or result.get('error') or result.get('fingerprint')!=self.request()['fingerprint']:
            self.copy_button.setEnabled(False);self.status.setText((result.get('error') or 'Cancelled or superseded.')+
                ' Prior completed output retained; copying is unavailable until a matching capture completes.');return
        self.report=result['report'];self._render();self.copy_button.setEnabled(True)
        self.status.setText(f"Frozen hindsight report complete in {result['seconds']:.3f}s. Later disk changes require a new capture.")

    def _render(self):
        d=self.report.data;c=d['capture'];o=c['observed'];names={p['key']:p['name'] for p in c['pool']}
        coverage=o['coverage'];gaps=d['gaps']
        def difference(units):return points(units)+' points' if units is not None else 'Unavailable; compatible exact scores are required'
        text=[summary(self.report),
            'Supplied-pool minus snapshot-local optimum: '+difference(gaps['snapshot_restriction_units'])+'.',
            'Supplied-pool minus validated highest reported score: '+difference(gaps['observed_units'])+'.',
            f"Observed field: {coverage.get('supplied_entry_rows',0)} supplied rows; {coverage.get('accepted_entries',0)} accepted entries; "
            f"{coverage.get('conflicting_entry_ids',0)} conflicting IDs; {coverage.get('known_reported_scores',0)} reported scores; "
            f"{coverage.get('valid_roster_witnesses',0)} validated and {coverage.get('unavailable_roster_witnesses',0)} unavailable roster witnesses. "
            +('Recorded field size: '+str(o['field_size'])+'. ' if o['field_size'] is not None else 'Field size unverified. ')
            +('The supplied entry count matches the recorded field size.' if o['field_completeness']=='reported_count_matches' else 'Field coverage is partial or unverified.'),
            f"Highest reported tie: {o['highest_tied_entries']} entries, {o['highest_tied_unique_valid_rosters']} distinct validated roster witnesses.",
            f"Highest reported total exactly reconstructed: {o['highest_exact_validated']}.",
            'Score/scope discrepancies: '+(', '.join(gaps['issues']) if gaps['issues'] else 'None detected.')]
        self.overview.setPlainText('\n\n'.join(text));rows=[]
        for scope in ('supplied','snapshot'):
            for r in d['scopes'][scope]['lineups']:rows.append([scope,float(r['points']),r['salary'],
                ' | '.join(s['role']+' '+names[s['key']] for s in r['roster'])])
        for r in o['highest_witnesses']:rows.append(['Observed highest witness',float(r['points']) if r['points'] is not None else None,
            r['salary'],' | '.join(s['role']+' '+names[s['key']] for s in r['roster'])])
        self.lineups.setSortingEnabled(False);self.lineups.clear();self.lineups.setColumnCount(4);self.lineups.setRowCount(len(rows))
        self.lineups.setHorizontalHeaderLabels(['Scope','Actual points','Salary','Validated roster'])
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                item=NumberItem(value,str(value)) if isinstance(value,(int,float)) else QtWidgets.QTableWidgetItem('Unavailable' if value is None else str(value))
                self.lineups.setItem(i,j,item)
        self.lineups.resizeColumnsToContents();self.lineups.horizontalHeader().setStretchLastSection(True);self.lineups.setSortingEnabled(True)
        # Evidence view preserves identities but omits machine-local receipt paths.
        provenance={k:v for k,v in c['provenance'].items() if k!='source_receipts'}
        provenance['source_hash_receipts']=sorted(c['provenance']['source_receipts'].values())
        self.evidence.setPlainText(json.dumps(dict(provenance=provenance,universe=c['salary_coverage'],
            actual_coverage=c['actual_coverage'],observed=o,rules=c['rules'],restricted=c['restricted'],
            scope_gates=c['gates'],solver_statuses=d['scopes'],numerical_policy=d['numerical_policy'],limits=c['limits']),indent=2))

    def copy_summary(self):
        if self.report and self.copy_button.isEnabled():QtWidgets.QApplication.clipboard().setText(summary(self.report,self.private.isChecked()))
