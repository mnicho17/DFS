"""Local field-history indexing and explicit, non-applying profile previews."""
import threading
from PyQt5 import QtCore, QtWidgets
from opponent_history import sync_saved, profile_preview, render_preview, save_profile


class HistoryWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    result=QtCore.pyqtSignal(object)
    error=QtCore.pyqtSignal(str)
    done=QtCore.pyqtSignal()

    def __init__(self, db_path, action, cutoff='', contest_format='showdown', username='', profile=None, config=None):
        super().__init__()
        self.db_path,self.action=db_path,action
        self.cutoff,self.contest_format,self.username=cutoff,contest_format,username
        self.profile=profile
        self.config=config or {}
        self.stop=threading.Event()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            if self.action=='sync':
                result=sync_saved(self.db_path,self.stop.is_set,self.progress.emit)
            elif self.action=='save':
                result={'profile_id':save_profile(self.db_path,self.profile,self.stop.is_set)}
            else:
                result=profile_preview(self.db_path,self.cutoff,self.contest_format,self.username,self.stop.is_set,**self.config)
            if not self.stop.is_set():self.result.emit(result)
        except Exception as exc:
            if not self.stop.is_set():self.error.emit(str(exc))
        finally:self.done.emit()


class OpponentHistoryDialog(QtWidgets.QDialog):
    def __init__(self, db_path, username='', parent=None):
        super().__init__(parent)
        self.db_path=db_path
        self.setWindowTitle('Username History & Field Profiles')
        self.resize(1050,750)
        self._thread=self._worker=None
        self._pending=None;self._error='';self._closing=False;self.profile=None
        layout=QtWidgets.QVBoxLayout(self)
        label=QtWidgets.QLabel('Index all saved mapped contests into local username history. Preview historical construction profiles; saving a profile does not change SIM or ownership.')
        label.setWordWrap(True);layout.addWidget(label)
        self.sync_button=QtWidgets.QPushButton('Index / refresh all mapped contests')
        self.sync_button.clicked.connect(lambda:self.start('sync'));layout.addWidget(self.sync_button)
        row=QtWidgets.QHBoxLayout()
        self.username=QtWidgets.QLineEdit(username)
        self.username.setPlaceholderText('Exact username; clear for the entire field');row.addWidget(self.username)
        self.format=QtWidgets.QComboBox();self.format.addItem('Showdown','showdown');self.format.addItem('Classic','classic');row.addWidget(self.format)
        row.addWidget(QtWidgets.QLabel('History strictly before'))
        self.cutoff=QtWidgets.QDateEdit(QtCore.QDate.currentDate());self.cutoff.setCalendarPopup(True);self.cutoff.setDisplayFormat('yyyy-MM-dd');row.addWidget(self.cutoff)
        self.preview_button=QtWidgets.QPushButton('Preview trends');self.preview_button.clicked.connect(lambda:self.start('preview'));row.addWidget(self.preview_button)
        layout.addLayout(row)
        cohort=QtWidgets.QHBoxLayout()
        self.band=QtWidgets.QComboBox();self.band.addItems(['All entry counts','1','2–5','6–20','21–150','151+']);cohort.addWidget(self.band)
        cohort.addWidget(QtWidgets.QLabel('Successful cohort: minimum complete contests'))
        self.min_contests=QtWidgets.QSpinBox();self.min_contests.setRange(1,1000);self.min_contests.setValue(3);cohort.addWidget(self.min_contests)
        cohort.addWidget(QtWidgets.QLabel('minimum contests with top-1% entry'))
        self.min_successes=QtWidgets.QSpinBox();self.min_successes.setRange(1,1000);self.min_successes.setValue(2);cohort.addWidget(self.min_successes);layout.addLayout(cohort)
        self.observed=QtWidgets.QCheckBox('Include unverified field coverage in the successful-user cohort (observed-entry denominator)')
        self.observed.setToolTip('Standard standings may omit total field size. This opt-in uses accepted entry count, which may represent an incomplete field.');layout.addWidget(self.observed)
        self.status=QtWidgets.QLabel('Index mapped contests, then search a username or inspect the whole field.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.report=QtWidgets.QPlainTextEdit();self.report.setReadOnly(True);layout.addWidget(self.report)
        actions=QtWidgets.QHBoxLayout()
        self.save_button=QtWidgets.QPushButton('Save verified profile version');self.save_button.setEnabled(False);self.save_button.clicked.connect(lambda:self.start('save'));actions.addWidget(self.save_button)
        self.cancel_button=QtWidgets.QPushButton('Cancel');self.cancel_button.setEnabled(False);self.cancel_button.clicked.connect(self.cancel);actions.addWidget(self.cancel_button)
        close=QtWidgets.QPushButton('Close');close.clicked.connect(self.reject);actions.addWidget(close);layout.addLayout(actions)
        self.username.textChanged.connect(self.invalidate);self.format.currentIndexChanged.connect(self.invalidate);self.cutoff.dateChanged.connect(self.invalidate)
        self.band.currentIndexChanged.connect(self.invalidate);self.min_contests.valueChanged.connect(self.invalidate);self.min_successes.valueChanged.connect(self.invalidate)
        self.observed.toggled.connect(self.invalidate)

    def invalidate(self):
        self.profile=None;self.save_button.setEnabled(False)

    def start(self, action):
        if self._thread is not None:return
        if action=='save' and self.profile is None:return
        self._pending=None;self._error='';self.action=action
        if action=='sync':self.invalidate()
        for widget in (self.sync_button,self.preview_button,self.username,self.format,self.cutoff,self.band,self.min_contests,self.min_successes,self.observed,self.save_button):widget.setEnabled(False)
        self.cancel_button.setEnabled(True);self.status.setText('Working locally…')
        config=dict(entry_band=self.band.currentText() if self.band.currentIndex() else '',min_contests=self.min_contests.value(),min_successes=self.min_successes.value(),allow_observed_fields=self.observed.isChecked())
        worker=self._worker=HistoryWorker(self.db_path,action,self.cutoff.date().toString('yyyy-MM-dd'),self.format.currentData(),self.username.text(),self.profile,config)
        thread=self._thread=QtCore.QThread(self);worker.moveToThread(thread)
        thread.started.connect(worker.run);worker.progress.connect(self.status.setText)
        worker.result.connect(self.receive);worker.error.connect(self.receive_error)
        worker.done.connect(thread.quit);worker.done.connect(worker.deleteLater);thread.finished.connect(self.retire);thread.start()

    def receive(self,result):
        if self._worker is not None and not self._worker.stop.is_set():self._pending=result

    def receive_error(self,error):self._error=error

    def retire(self):
        cancelled=self._worker.stop.is_set();thread=self._thread
        self._thread=self._worker=None;thread.deleteLater()
        for widget in (self.sync_button,self.preview_button,self.username,self.format,self.cutoff,self.band,self.min_contests,self.min_successes,self.observed):widget.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if cancelled:self.status.setText('Cancelled. Completed indexed contests remain saved; an unfinished contest is rolled back.')
        elif self._pending is None:self.status.setText('Failed: '+self._error)
        elif self.action=='sync':
            r=self._pending
            self.status.setText(f"{len(r['contests'])} contests indexed/unchanged; {len(r['errors'])} errors. Existing build history was not modified.")
            self.report.setPlainText(r['note']+'\n'+'\n'.join(e['name']+': '+e['error'] for e in r['errors']))
        elif self.action=='save':self.status.setText(f"Saved verified profile version #{self._pending['profile_id']}. SIM remains unchanged.")
        else:
            self.profile=self._pending;self.report.setPlainText(render_preview(self.profile));self.status.setText('Preview complete. Historical evidence only; no simulation settings were changed.')
        self._pending=None;self.save_button.setEnabled(self.profile is not None)
        if self._closing:super().reject()

    def cancel(self):
        if self._worker is not None:self._worker.stop.set();self._pending=None;self.cancel_button.setEnabled(False)

    def reject(self):
        if self._thread is not None:self._closing=True;self.cancel()
        else:super().reject()

    def closeEvent(self,event):
        if self._thread is not None:self._closing=True;self.cancel();event.ignore()
        else:super().closeEvent(event)
