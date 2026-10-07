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

    def __init__(self,path,players,seconds,limit,salary_cap,captain_keys=None,screen_snapshot=None):
        super().__init__()
        self.path,self.players,self.seconds,self.limit,self.salary_cap=path,copy.deepcopy(players),seconds,limit,salary_cap
        self.stop=threading.Event();self.last_update=0
        self.captain_keys=captain_keys
        self.screen_snapshot=screen_snapshot

    def update(self,value):
        now=time.monotonic()
        if now-self.last_update>=1 or value.get('complete') or value.get('screening_complete'):
            self.last_update=now;self.progress.emit(value)

    @QtCore.pyqtSlot()
    def run(self):
        try:
            started=time.monotonic()
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
                    result.update(prepare_screening(self.path,players,limit=deep_candidate_budget(count,options,False),
                        salary_cap=self.salary_cap,salary_strategy=recipe.get('salary_strategy','Near Cap'),rules=rules,
                        screening=settings(options,recipe.get('nfl_sim_scenarios',1000)),seconds=remaining,
                        cancelled=self.stop.is_set,progress=self.update))
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
        note=QtWidgets.QLabel('Prepare reusable rosters while the PC is awake. With Captain locks, preparation covers those Captains; otherwise it covers all Captains. All FLEX combinations are considered, with current exclusions and rules applied later. Optional screening below scores a bounded sample using current inputs. Limits can leave partial coverage.')
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
        form=QtWidgets.QFormLayout();form.addRow('Maximum preparation time',self.hours);form.addRow('Stored roster limit',self.limit);layout.addLayout(form)
        self.screen=QtWidgets.QCheckBox('Also screen a bounded candidate sample for Deep reuse')
        self.screen.setToolTip('Only complete libraries can be screened. Completed batches are saved for resume. Final SIM and audits run fresh; millions of stored rosters are not all scored.')
        layout.addWidget(self.screen)
        self.message=QtWidgets.QLabel('Choose a new library or an existing checkpoint. Complete coverage is reported only after every combination is checked.')
        self.message.setWordWrap(True);layout.addWidget(self.message)
        self.start=QtWidgets.QPushButton('Start / Resume');self.pause=QtWidgets.QPushButton('Pause and save')
        self.pause.setEnabled(False)
        row=QtWidgets.QHBoxLayout();row.addWidget(self.start);row.addWidget(self.pause);layout.addLayout(row)
        self.start.clicked.connect(self.begin);self.pause.clicked.connect(self.cancel)
        self.controls=(self.new,self.open,self.hours,self.limit,self.start,self.screen)

    def choose_new(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'New Showdown Roster Library','showdown-rosters.sdlib','Showdown roster library (*.sdlib)')
        if path:
            if Path(path).exists():
                self.message.setText('Choose a new filename; existing libraries must be opened for resume.');return
            self.path.setText(path)
            self.scope=sorted(player_key(p) for p in self.snapshot['inputs']['players'] if p.get('LockCpt')) or None

    def choose_existing(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Resume Showdown Roster Library','','Showdown roster library (*.sdlib *.sqlite)')
        if not path:return
        try:
            recipe=self.snapshot['inputs']['recipe']
            validate_library(path,self.snapshot['inputs']['players'],salary_cap=recipe.get('salary_cap',50000),allow_partial=True)
            self.path.setText(path)
            saved=status(path);self.scope=saved['captains'];self.describe(saved)
        except Exception as exc:self.message.setText(str(exc))

    def describe(self,result):
        if 'screened' in result:
            self.message.setText(f"Screened {result['screened']:,}/{result['screening_total']:,} sampled candidates; "+
                ('screening complete. Final SIM remains fresh.' if result['screening_complete'] else 'partial screening; resume to continue.'))
            return
        self.message.setText(f"{result['saved']:,} saved rosters; {result['checked']:,} combinations checked. Coverage: "+('complete.' if result['complete'] else 'partial; resume to continue.'))

    def begin(self):
        if self.thread is not None or not self.path.text():return
        recipe=self.snapshot['inputs']['recipe']
        self.worker=PreparationWorker(self.path.text(),self.snapshot['inputs']['players'],
            self.hours.currentData()*3600,self.limit.currentData(),recipe.get('salary_cap',50000),self.scope,
            self.snapshot if self.screen.isChecked() else None)
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
