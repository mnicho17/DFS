"""Owned background capture/publication UI for the read-only review report."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import threading

from PyQt5 import QtCore, QtWidgets

from review_report import Cancelled, Options, Report, SPORTS, SourceDestination, capture, publish


class ReportJob(QtCore.QObject):
    result = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(str)
    done = QtCore.pyqtSignal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.cancelled = threading.Event()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            self.result.emit(self.operation(self.cancelled.is_set, self.progress.emit))
        except Cancelled:
            self.failed.emit('Cancelled. No new report was published.')
        except SourceDestination:
            self.failed.emit('Choose a report destination outside the source history folder and separate from its diagnostic source file.')
        except Exception:
            # Never echo paths, raw data or credential-bearing exceptions.
            self.failed.emit('Report could not be completed. Check source access, destination permissions and free space, then retry.')
        finally:
            self.done.emit()


class ReportDelivery(QtCore.QObject):
    """Lives on GUI thread; signal delivery retains the originating job identity."""
    def __init__(self, dialog, identity):
        super().__init__(dialog)
        self.dialog, self.identity = dialog, identity

    @QtCore.pyqtSlot(object)
    def result(self, value):
        self.dialog._result(self.identity, value)

    @QtCore.pyqtSlot(str)
    def error(self, message):
        self.dialog._error(self.identity, message)

    @QtCore.pyqtSlot(str)
    def progress(self, message):
        if self.dialog._active(self.identity):
            self.dialog.status.setText(message)

    @QtCore.pyqtSlot()
    def retired(self):
        self.dialog._retired(self.identity)
        self.deleteLater()


class ReviewReportDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, db_path=None, diagnostic_path=None):
        super().__init__(parent)
        self.setWindowTitle('Export Review Report')
        self.resize(960, 780)
        self.db_path, self.diagnostic_path = db_path, diagnostic_path
        self._job = None
        self._sequence = 0
        self._closing = False
        self._report = None
        self._options_at_capture = None
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel('Preview a local snapshot of results, recorded settings and available diagnostics. '
                                'Saving creates a ZIP you can share manually; it does not update history or send data.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        source_bar = QtWidgets.QHBoxLayout()
        self.source_label = QtWidgets.QLabel('Source: current app history')
        self.choose_source = QtWidgets.QPushButton('Choose history folder...')
        self.reset_source = QtWidgets.QPushButton('Use current app history')
        self.choose_source.clicked.connect(self.choose_history)
        self.reset_source.clicked.connect(self.use_current_history)
        for widget in (self.source_label, self.choose_source, self.reset_source):
            source_bar.addWidget(widget)
        layout.addLayout(source_bar)
        filters = QtWidgets.QHBoxLayout()
        self.all_dates = QtWidgets.QCheckBox('All available dates')
        self.all_dates.setChecked(True)
        self.start = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.end = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        for widget in (self.start, self.end):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat('yyyy-MM-dd')
        self.sport = QtWidgets.QComboBox()
        self.sport.addItems(['All sports', *SPORTS])
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(['All formats', 'Classic', 'Showdown'])
        for widget in (self.all_dates, self.start, self.end, self.sport, self.kind):
            filters.addWidget(widget)
        layout.addLayout(filters)
        self.detail = QtWidgets.QCheckBox('Include lineup and build-player details (names, IDs, roster slots and recorded decisions)')
        self.detail.setChecked(False)
        layout.addWidget(self.detail)
        note = QtWidgets.QLabel('Unknown dates/formats are counted; range filters exclude unknown dates. '
                               'Details describe recorded exports, not verified submissions. '
                               'Optional observations may contain personal information: review them before sharing.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.observation = QtWidgets.QPlainTextEdit()
        self.observation.setPlaceholderText('Optional observation: what you did, expected, and observed (2,000 characters maximum).')
        self.observation.setMaximumHeight(85)
        layout.addWidget(self.observation)
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setObjectName('reviewReportPreview')
        self.evidence_preview = QtWidgets.QPlainTextEdit()
        self.evidence_preview.setReadOnly(True)
        self.detail_preview = QtWidgets.QPlainTextEdit()
        self.detail_preview.setReadOnly(True)
        self.previews = QtWidgets.QTabWidget()
        self.previews.addTab(self.preview, 'summary.md')
        self.previews.addTab(self.evidence_preview, 'evidence.json')
        self.previews.addTab(self.detail_preview, 'lineups.csv (not included)')
        self.previews.setTabEnabled(2, False)
        layout.addWidget(self.previews, 1)
        self.status = QtWidgets.QLabel('Choose filters, then Generate Preview.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        bar = QtWidgets.QHBoxLayout()
        self.generate = QtWidgets.QPushButton('Generate Preview')
        self.save = QtWidgets.QPushButton('Save Report ZIP')
        self.save.setEnabled(False)
        self.cancel = QtWidgets.QPushButton('Cancel operation')
        self.cancel.setEnabled(False)
        self.close_button = QtWidgets.QPushButton('Close')
        for widget in (self.generate, self.save, self.cancel, self.close_button):
            bar.addWidget(widget)
        layout.addLayout(bar)
        self.generate.clicked.connect(self.generate_preview)
        self.save.clicked.connect(self.save_report)
        self.cancel.clicked.connect(self.cancel_operation)
        self.close_button.clicked.connect(self.close)
        for signal in (self.all_dates.toggled, self.start.dateChanged, self.end.dateChanged,
                       self.sport.currentIndexChanged, self.kind.currentIndexChanged,
                       self.detail.toggled, self.observation.textChanged):
            signal.connect(self.invalidate)
        self._enable_controls()

    def choose_history(self):
        if self._job:
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Choose the existing history folder')
        if folder:
            self.db_path = Path(folder) / 'exports.sqlite'
            self.diagnostic_path = Path(folder) / 'build-diagnostics.json'
            self.source_label.setText('Source: selected history folder')
            self.source_label.setToolTip(str(Path(folder)))
            self.invalidate()

    def use_current_history(self):
        if self._job:
            return
        self.db_path = self.diagnostic_path = None
        self.source_label.setText('Source: current app history')
        self.source_label.setToolTip('')
        self.invalidate()

    def options(self):
        return Options(start='' if self.all_dates.isChecked() else self.start.date().toString('yyyy-MM-dd'),
                       end='' if self.all_dates.isChecked() else self.end.date().toString('yyyy-MM-dd'),
                       sport='all' if self.sport.currentIndex() == 0 else self.sport.currentText(),
                       kind='all' if self.kind.currentIndex() == 0 else self.kind.currentText().lower(),
                       details=self.detail.isChecked(), observation=self.observation.toPlainText())

    def invalidate(self, *_):
        self._report = None
        self._clear_previews()
        self.save.setEnabled(False)
        if self._job:
            self.cancel_operation()
        else:
            self.status.setText('Options changed. Generate a new preview before saving.')
            self._enable_controls()

    def _clear_previews(self):
        for preview in (self.preview, self.evidence_preview, self.detail_preview):
            preview.clear()
        self.previews.setTabEnabled(2, False)
        self.previews.setTabText(2, 'lineups.csv (not included)')

    def _enable_controls(self):
        busy = self._job is not None
        for widget in (self.all_dates, self.sport, self.kind, self.detail, self.observation, self.generate, self.choose_source, self.reset_source):
            widget.setEnabled(not busy)
        self.start.setEnabled(not busy and not self.all_dates.isChecked())
        self.end.setEnabled(not busy and not self.all_dates.isChecked())
        self.save.setEnabled(not busy and self._report is not None)
        self.cancel.setEnabled(busy)

    def _active(self, identity):
        return self._job is not None and self._job['identity'] == identity

    def _launch(self, operation, kind):
        if self._job is not None or self._closing:
            return
        self._sequence += 1
        thread = QtCore.QThread(self)
        worker = ReportJob(operation)
        delivery = ReportDelivery(self, self._sequence)
        worker.moveToThread(thread)
        self._job = dict(identity=self._sequence, thread=thread, worker=worker, delivery=delivery,
                         kind=kind, result=None, error=None)
        thread.started.connect(worker.run)
        worker.result.connect(delivery.result)
        worker.failed.connect(delivery.error)
        worker.progress.connect(delivery.progress)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(delivery.retired)
        thread.finished.connect(thread.deleteLater)
        self._enable_controls()
        self.status.setText('Capturing report...' if kind == 'capture' else 'Writing report ZIP...')
        thread.start()

    def generate_preview(self):
        if self._job:
            return
        try:
            options = self.options()
        except ValueError as exc:
            self.status.setText(str(exc))  # Static, application-generated validation.
            return
        self._report = None
        self._clear_previews()
        self._options_at_capture = options
        self._launch(lambda cancelled, progress: capture(options, db_path=self.db_path,
                     diagnostic_path=self.diagnostic_path, cancelled=cancelled, progress=progress), 'capture')

    def save_report(self):
        if self._job or self._report is None:
            return
        if self.options() != self._options_at_capture:
            self.invalidate()
            return
        report = self._report
        suggested = f'DFS-Review-{date.today().isoformat()}-{report.data["report_id"][:8]}.zip'
        from data_paths import review_reports_directory
        try:
            destination = review_reports_directory(excluded_roots=report._source_roots) / suggested
        except OSError:
            self.status.setText('The review-report folder is unavailable. Check user-data folder permissions and retry.')
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save Review Report', str(destination), 'ZIP files (*.zip)')
        if not path:
            return
        # The standard Save dialog handles overwrite consent for the exact path.
        def operation(cancelled, progress):
            publish(report, path, cancelled=cancelled)
            return str(Path(path))
        self._launch(operation, 'save')

    def cancel_operation(self):
        if self._job:
            self._job['worker'].cancelled.set()
            self.cancel.setEnabled(False)
            self.status.setText('Cancellation requested; waiting for cleanup...')

    def _result(self, identity, value):
        if self._active(identity):
            self._job['result'] = value

    def _error(self, identity, message):
        if self._active(identity):
            self._job['error'] = message

    def _retired(self, identity):
        if not self._active(identity):
            return
        job, self._job = self._job, None
        value = job['result']
        if job['kind'] == 'save' and isinstance(value, str):
            # Publication is complete, even if Cancel arrived just afterward.
            self.status.setText('Report saved to: ' + value)
        elif job['kind'] == 'capture' and isinstance(value, Report) and not job['worker'].cancelled.is_set():
            self._report = value
            self.preview.setPlainText(value.summary())
            self.evidence_preview.setPlainText(value.evidence.decode('utf-8'))
            details = value.data['options']['details']
            self.previews.setTabEnabled(2, details)
            self.previews.setTabText(2, 'lineups.csv' if details else 'lineups.csv (not included)')
            if details:
                self.detail_preview.setPlainText(value.csv())
            self.status.setText('Preview ready. Review the contents before saving and sharing.')
        else:
            self.status.setText(job['error'] or 'Cancelled. No new report was published.')
        self._enable_controls()
        if self._closing:
            QtCore.QTimer.singleShot(0, self.accept)

    def closeEvent(self, event):
        if self._job:
            self._closing = True
            self.cancel_operation()
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if self._job:
            self._closing = True
            self.cancel_operation()
        else:
            super().reject()
