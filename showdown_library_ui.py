"""Explicit desktop preparation with safe checkpointed pause and close."""
import copy
import threading
import time
from pathlib import Path
from PyQt5 import QtCore,QtWidgets
from showdown_library import prepare,status,validate_library
from portfolio_rules import player_key


class PreparationWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(dict)
    finished=QtCore.pyqtSignal(dict)
    error=QtCore.pyqtSignal(str)

    def __init__(self,path,players,seconds,limit,salary_cap,captain_keys=None,screen_snapshot=None,storage_bytes=8*1024**3,full_screen=False):
        super().__init__()
        self.path,self.players,self.seconds,self.limit,self.salary_cap=path,copy.deepcopy(players),seconds,limit,salary_cap
        self.stop=threading.Event();self.last_update=0
        self.captain_keys=captain_keys
        self.screen_snapshot=screen_snapshot
        self.storage_bytes=storage_bytes
        self.full_screen=full_screen

    def update(self,value):
        now=time.monotonic()
        if now-self.last_update>=1 or value.get('complete') or value.get('screening_complete'):
            self.last_update=now;self.progress.emit(value)

    @QtCore.pyqtSlot()
    def run(self):
        try:
            started=time.monotonic()
            if Path(self.path).suffix=='.sdfull':
                from showdown_full_library import prepare as prepare_full
                result=prepare_full(self.path,self.players,seconds=self.seconds,max_bytes=self.storage_bytes,
                    salary_cap=self.salary_cap,cancelled=self.stop.is_set,progress=self.update)
            else:
                result=prepare(self.path,self.players,seconds=self.seconds,max_candidates=self.limit,
                    salary_cap=self.salary_cap,cancelled=self.stop.is_set,progress=self.update,captain_keys=self.captain_keys)
            if self.screen_snapshot and result['complete'] and not self.stop.is_set():
                from captain_pool import prepare_captain_pool
                from compute_settings import deep_candidate_budget
                from showdown_screening import prepare_screening,settings
                inputs=self.screen_snapshot['inputs'];recipe=inputs['recipe']
                count=recipe.get('requested_lineups',150);options=recipe.get('deep_compute',{})
                players,rules,_=prepare_captain_pool(self.players,inputs['rules'],count)
                remaining=self.seconds-(time.monotonic()-started)
                if remaining>0:
                    if self.full_screen:
                        from showdown_full_screening import prepare as prepare_screening
                    result.update(prepare_screening(self.path,players,limit=deep_candidate_budget(count,options,False),
                        salary_cap=self.salary_cap,salary_strategy=recipe.get('salary_strategy','Near Cap'),rules=rules,
                        screening=settings(options,recipe.get('nfl_sim_scenarios',1000)),seconds=remaining,
                        cancelled=self.stop.is_set,progress=self.update))
            result['screening_requested']=bool(self.screen_snapshot)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class PreparationDialog(QtWidgets.QDialog):
    def __init__(self,snapshot,parent=None):
        super().__init__(parent)
        self.snapshot=copy.deepcopy(snapshot)
        self.scope=sorted(player_key(p) for p in self.snapshot['inputs']['players'] if p.get('LockCpt')) or None
        self.thread=self.worker=None;self.stop=None;self.pending=None;self.closing=False
        self.setWindowTitle('Prepare Showdown Roster Library')
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Prepare reusable rosters while the PC is awake. With Captain locks, preparation covers those Captains; otherwise it covers all Captains. All FLEX combinations are considered, with current exclusions and rules applied later. Optional screening can score a bounded sample or every currently legal roster using captured inputs. Limits can leave partial coverage.')
        note.setWordWrap(True);layout.addWidget(note)
        self.path=QtWidgets.QLineEdit();self.path.setReadOnly(True);layout.addWidget(self.path)
        row=QtWidgets.QHBoxLayout()
        self.new=QtWidgets.QPushButton('New library');self.open=QtWidgets.QPushButton('Open existing')
        row.addWidget(self.new);row.addWidget(self.open);layout.addLayout(row)
        self.new.clicked.connect(self.choose_new);self.open.clicked.connect(self.choose_existing)
        self.hours=QtWidgets.QComboBox()
        for value in (1,2,4,8,12): self.hours.addItem(f'{value} hours',value)
        self.limit=QtWidgets.QComboBox()
        for value in (250000,1000000,5000000,10000000,50000000):self.limit.addItem(f'{value:,} saved rosters',value)
        self.limit.setCurrentIndex(3)
        self.full=QtWidgets.QCheckBox('Full slate: prepare every Captain in separate partitions')
        self.full.setToolTip('Ignores Captain locks for roster preparation only. Current Captain locks and all rules still apply when building. Keep the manifest and its .parts folder together.')
        layout.addWidget(self.full)
        self.storage=QtWidgets.QComboBox()
        for gb in (4,8,16,32):self.storage.addItem(f'{gb} GiB',gb*1024**3)
        self.storage.setCurrentIndex(1);self.storage.setEnabled(False)
        self.full.toggled.connect(self.change_format)
        form=QtWidgets.QFormLayout();form.addRow('Maximum preparation time',self.hours);form.addRow('Stored roster limit',self.limit);layout.addLayout(form)
        form.addRow('Full-library storage budget',self.storage)
        self.screen=QtWidgets.QCheckBox('Also prepare screening for Deep reuse')
        self.screen.setToolTip('Only complete libraries can be screened. Completed batches are saved for resume. Select the option below to screen every currently legal roster. Final SIM and audits run fresh.')
        layout.addWidget(self.screen)
        self.full_screen=QtWidgets.QCheckBox('Screen every currently legal roster (may require multiple nights)')
        self.full_screen.setEnabled(False)
        self.screen.toggled.connect(self.change_screen)
        self.full_screen.setToolTip('Scores every legal roster under captured inputs and rules, retaining at most 20,000 leaders across Captains and constructions. Resume with identical inputs. Detailed SIM and audit remain fresh.')
        layout.addWidget(self.full_screen)
        self.message=QtWidgets.QLabel('Choose a new library or an existing checkpoint. Complete coverage is reported only after every combination is checked.')
        self.message.setWordWrap(True);layout.addWidget(self.message)
        self.start=QtWidgets.QPushButton('Start / Resume');self.pause=QtWidgets.QPushButton('Pause and save')
        self.pause.setEnabled(False)
        row=QtWidgets.QHBoxLayout();row.addWidget(self.start);row.addWidget(self.pause);layout.addLayout(row)
        self.start.clicked.connect(self.begin);self.pause.clicked.connect(self.cancel)
        self.controls=(self.new,self.open,self.hours,self.limit,self.start,self.screen,self.full,self.storage,self.full_screen)

    def change_screen(self,enabled):
        self.full_screen.setEnabled(enabled)
        if not enabled:self.full_screen.setChecked(False)

    def change_format(self,full):
        self.limit.setEnabled(not full);self.storage.setEnabled(full)
        if bool(Path(self.path.text()).suffix=='.sdfull')!=full:self.path.clear()

    def choose_new(self):
        full=self.full.isChecked()
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'New Showdown Roster Library',
            'full-showdown.sdfull' if full else 'showdown-rosters.sdlib',
            'Full Showdown manifest (*.sdfull)' if full else 'Showdown roster library (*.sdlib)')
        if path:
            if Path(path).exists():
                self.message.setText('Choose a new filename; existing libraries must be opened for resume.');return
            self.path.setText(path)
            self.scope=sorted(player_key(p) for p in self.snapshot['inputs']['players'] if p.get('LockCpt')) or None

    def choose_existing(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Resume Showdown Roster Library','','Showdown roster library (*.sdlib *.sdfull *.sqlite)')
        if not path:return
        try:
            recipe=self.snapshot['inputs']['recipe']
            validate_library(path,self.snapshot['inputs']['players'],salary_cap=recipe.get('salary_cap',50000),allow_partial=True)
            self.full.setChecked(Path(path).suffix=='.sdfull');self.path.setText(path)
            saved=status(path);self.scope=saved['captains'];self.describe(saved)
        except Exception as exc:self.message.setText(str(exc))

    def describe(self,result):
        if 'screened' in result:
            total=result.get('screening_total')
            scope=f"{total:,}" if total is not None else 'all legal rosters (counting)'
            self.message.setText(f"Screened {result['screened']:,}/{scope} candidates; "+
                ('screening complete. Final SIM remains fresh.' if result['screening_complete'] else 'partial screening; resume to continue.'))
            return
        extra=f" {result['completed_partitions']}/{result['partition_count']} Captain partitions complete." if 'partition_count' in result else ''
        if result.get('pause_reason'):extra+=' Paused: '+result['pause_reason']+'.'
        if result.get('screening_requested') is False:extra+=' Screening was not requested; roster completion does not mean screening is complete.'
        elif result.get('screening_requested') and 'screened' not in result:extra+=' Screening has not completed; resume with screening enabled.'
        self.message.setText(f"{result['saved']:,} saved rosters; {result['checked']:,} combinations checked. Coverage: "+('complete.' if result['complete'] else 'partial; resume to continue.')+extra)

    def begin(self):
        if self.thread is not None or not self.path.text():return
        recipe=self.snapshot['inputs']['recipe']
        self.worker=PreparationWorker(self.path.text(),self.snapshot['inputs']['players'],
            self.hours.currentData()*3600,self.limit.currentData(),recipe.get('salary_cap',50000),self.scope,
            self.snapshot if self.screen.isChecked() else None,self.storage.currentData(),self.full_screen.isChecked())
        self.stop=self.worker.stop;self.pending=None
        self.thread=QtCore.QThread(self);self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.receive_progress)
        self.worker.finished.connect(self.receive)
        self.worker.error.connect(self.failure)
        self.worker.finished.connect(self.thread.quit);self.worker.error.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater);self.worker.error.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        for control in self.controls:control.setEnabled(False)
        self.pause.setEnabled(True);self.message.setText('Preparing and saving rosters…')
        self.thread.start()

    def receive_progress(self,result):
        if self.stop is not None and not self.stop.is_set():self.describe(result)

    def receive(self,result):self.pending=result
    def failure(self,message):self.pending=dict(error=message)

    def cancel(self):
        if self.thread is not None:
            self.stop.set();self.pause.setEnabled(False)
            self.message.setText('Pausing; waiting for the current checkpoint and worker retirement…')

    def retire(self):
        thread=self.thread;self.thread=self.worker=None
        thread.deleteLater()
        for control in self.controls:control.setEnabled(True)
        self.limit.setEnabled(not self.full.isChecked());self.storage.setEnabled(self.full.isChecked())
        self.full_screen.setEnabled(self.screen.isChecked())
        self.pause.setEnabled(False)
        if self.closing:
            self.accept();return
        if self.pending is None or 'error' in self.pending:
            self.message.setText('Preparation stopped: '+str((self.pending or {}).get('error','No result')))
        else:self.describe(self.pending)

    def reject(self):
        if self.thread is not None:
            self.closing=True;self.cancel();return
        super().reject()

    def closeEvent(self,event):
        if self.thread is not None:
            self.closing=True;self.cancel();event.ignore()
        else:super().closeEvent(event)
