"""Simple desktop controls for checkpointed candidate generation."""
import copy
from PyQt5 import QtCore, QtWidgets
from candidate_library import metadata, run_search, load_candidates

class SearchWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    finished=QtCore.pyqtSignal(str)
    def __init__(self,path,snapshot,seconds):
        super().__init__()
        import threading
        self.stop=threading.Event();self.path=path;self.snapshot=snapshot;self.seconds=seconds
    def run(self):
        try:
            count=run_search(self.path,self.snapshot,seconds=self.seconds,
                             cancelled=self.stop.is_set,progress=self.progress.emit)
            self.finished.emit(f'{count:,} unique lineups saved. Load this library and run Deep to score and rank them.')
        except Exception as exc:
            self.finished.emit('Search stopped: '+str(exc))

class LongSearchDialog(QtWidgets.QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('Long Search');self.resize(580,300)
        self.snapshot=None;self.thread=None
        layout=QtWidgets.QVBoxLayout(self)
        intro=QtWidgets.QLabel('Build a reusable candidate library for this NFL slate. All five styles run in small batches. '
            'Progress is saved after each batch. Leave the app open while searching; pause before closing. '
            'Outcomes and final rankings are calculated when you load the library and run Deep.')
        intro.setWordWrap(True);layout.addWidget(intro)
        row=QtWidgets.QHBoxLayout();self.path=QtWidgets.QLineEdit();self.path.setReadOnly(True);row.addWidget(self.path)
        new=QtWidgets.QPushButton('New library');resume=QtWidgets.QPushButton('Open existing');row.addWidget(new);row.addWidget(resume);layout.addLayout(row)
        new.clicked.connect(self.new_library);resume.clicked.connect(self.open_library)
        self.hours=QtWidgets.QComboBox()
        for label,h in [('1 hour',1),('2 hours',2),('4 hours',4),('8 hours',8),('12 hours',12)]:self.hours.addItem(label,h)
        layout.addWidget(self.hours)
        self.status=QtWidgets.QLabel('Choose a new library or an existing checkpoint.');self.status.setWordWrap(True);layout.addWidget(self.status)
        buttons=QtWidgets.QHBoxLayout();self.start=QtWidgets.QPushButton('Start / Resume');self.pause=QtWidgets.QPushButton('Pause and save');self.pause.setEnabled(False)
        buttons.addWidget(self.start);buttons.addWidget(self.pause);layout.addLayout(buttons)
        self.start.clicked.connect(self.begin);self.pause.clicked.connect(self.cancel)
        self.controls=[new,resume,self.hours,self.start]
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
        self.thread=QtCore.QThread(self);self.worker=SearchWorker(self.path.text(),copy.deepcopy(self.snapshot),self.hours.currentData()*3600)
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
