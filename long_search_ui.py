"""Simple desktop controls for checkpointed candidate generation."""
import copy
from PyQt5 import QtCore, QtGui, QtWidgets
from candidate_library import metadata, run_search, load_candidates

class SearchWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    finished=QtCore.pyqtSignal(str)
    def __init__(self,path,snapshot,seconds,candidate_limit=20000,prepare=False):
        super().__init__()
        import threading
        self.stop=threading.Event();self.path=path;self.snapshot=snapshot;self.seconds=seconds
        self.candidate_limit=candidate_limit
        self.prepare=prepare
    def run(self):
        try:
            import time
            from compute_settings import normalize_deep_settings
            started=time.monotonic()
            options=normalize_deep_settings(self.snapshot.get('inputs',{}).get('recipe',{}).get('deep_compute'))
            reserve=min(self.seconds*.25,options['minutes']*60) if self.prepare else 0
            count=run_search(self.path,self.snapshot,seconds=max(1,self.seconds-reserve),
                             cancelled=self.stop.is_set,progress=self.progress.emit,candidate_limit=self.candidate_limit)
            prepared=''
            if self.prepare and count and not self.stop.is_set():
                from scenario_preparation import prepare_scenarios
                prepared=prepare_scenarios(self.path,self.snapshot,seconds=max(0,self.seconds-(time.monotonic()-started)),
                    cancelled=self.stop.is_set,progress=self.progress.emit)
            self.finished.emit(f'{count:,} unique lineups saved. '+prepared+' Load this library and run Deep to score and rank current inputs.')
        except Exception as exc:
            self.finished.emit('Search stopped: '+str(exc))

class LongSearchDialog(QtWidgets.QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('Overnight Preparation / Long Search');self.resize(620,440)
        self.snapshot=None;self.thread=None
        layout=QtWidgets.QVBoxLayout(self)
        intro=QtWidgets.QLabel('Build a reusable candidate library for this NFL slate. All five styles run in small batches. '
            'Progress is saved after each batch and saved combinations are excluded from later searches. '
            'Stops at the candidate target or time limit, whichever comes first. Leave the app open and the PC awake; pause before closing. '
            'Optional scenario preparation uses the saved inputs. Load the library and run Deep to check current inputs and select your portfolio.')
        intro.setWordWrap(True);layout.addWidget(intro)
        row=QtWidgets.QHBoxLayout();self.path=QtWidgets.QLineEdit();self.path.setReadOnly(True);row.addWidget(self.path)
        new=QtWidgets.QPushButton('New library');resume=QtWidgets.QPushButton('Open existing');row.addWidget(new);row.addWidget(resume);layout.addLayout(row)
        new.clicked.connect(self.new_library);resume.clicked.connect(self.open_library)
        self.hours=QtWidgets.QComboBox()
        for label,h in [('1 hour',1),('2 hours',2),('4 hours',4),('8 hours',8),('12 hours',12)]:self.hours.addItem(label,h)
        limits=QtWidgets.QFormLayout();limits.addRow('Maximum search time',self.hours)
        self.target=QtWidgets.QComboBox()
        for count in (12000,20000,50000,100000):self.target.addItem(f'{count:,} candidates',count)
        self.target.setCurrentIndex(1);limits.addRow('Saved candidate target',self.target);layout.addLayout(limits)
        self.prepare=QtWidgets.QCheckBox('Also prepare reusable scenarios (within this time limit)')
        self.prepare.setChecked(True);layout.addWidget(self.prepare)
        self.cache_folder_button=QtWidgets.QPushButton('Open reusable scenario cache folder')
        self.cache_folder_button.clicked.connect(self.open_cache_folder);layout.addWidget(self.cache_folder_button)
        note=QtWidgets.QLabel('Larger libraries can make the later simulation slower. Start with 12,000–20,000; the target is for candidates, not submitted entries.')
        note.setWordWrap(True);layout.addWidget(note)
        self.status=QtWidgets.QLabel('Choose a new library or an existing checkpoint.');self.status.setWordWrap(True);layout.addWidget(self.status)
        buttons=QtWidgets.QHBoxLayout();self.start=QtWidgets.QPushButton('Start / Resume');self.pause=QtWidgets.QPushButton('Pause and save');self.pause.setEnabled(False)
        buttons.addWidget(self.start);buttons.addWidget(self.pause);layout.addLayout(buttons)
        self.start.clicked.connect(self.begin);self.pause.clicked.connect(self.cancel)
        self.controls=[new,resume,self.hours,self.target,self.prepare,self.cache_folder_button,self.start]
    def open_cache_folder(self):
        try:
            from scenario_cache import cache_folder
            folder=cache_folder();folder.mkdir(parents=True,exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))
        except Exception as exc:self.status.setText('Could not open scenario cache: '+str(exc))
    def new_library(self):
        try:
            self.snapshot=self.parent()._capture_snapshot()
            from pathlib import Path
            from build_diagnostics import build_history_path
            folder=Path(build_history_path()).parent/'candidates'
            folder.mkdir(parents=True,exist_ok=True)
            path,_=QtWidgets.QFileDialog.getSaveFileName(self,'New Candidate Library',str(folder/(self.snapshot['input_id'][:12]+'.dfslib')),'Candidate library (*.dfslib)')
            if path:
                from pathlib import Path
                if Path(path).exists():raise ValueError('Choose a new filename; existing libraries are resumed, never overwritten.')
                self.path.setText(path)
        except Exception as exc: self.status.setText(str(exc))
    def open_library(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Resume Candidate Library','','Candidate library (*.dfslib)')
        if not path:return
        try:
            info=metadata(path);self.snapshot=info['snapshot'];self.path.setText(path)
            self.status.setText(f"{info['count']:,} saved candidates; {info['batches']} completed batches. Resume uses the original saved player inputs.")
        except Exception as exc:self.status.setText(str(exc))
    def begin(self):
        if not self.path.text() or self.snapshot is None:return
        self.thread=QtCore.QThread(self);self.worker=SearchWorker(self.path.text(),copy.deepcopy(self.snapshot),self.hours.currentData()*3600,self.target.currentData(),self.prepare.isChecked())
        self.worker.moveToThread(self.thread);self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status.setText);self.worker.finished.connect(self.status.setText)
        self.worker.finished.connect(self.thread.quit);self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.done_search);self.thread.start()
        for c in self.controls:c.setEnabled(False)
        self.pause.setEnabled(True)
    def cancel(self):
        self.worker.stop.set();self.pause.setEnabled(False);self.status.setText('Finishing the current step and saving the checkpoint...')
    def done_search(self):
        for c in self.controls:c.setEnabled(True)
        self.pause.setEnabled(False)
        self.thread.deleteLater();self.thread=None
    def reject(self):
        if self.thread is not None:
            self.cancel();return
        super().reject()
    def closeEvent(self,event):
        if self.thread is not None:self.cancel();event.ignore()
        else:event.accept()
