"""Cancellable read-only browser for winners and their full observed portfolios."""
import threading
from PyQt5 import QtCore,QtWidgets
from winner_portfolio_review import review_winners,render_overview,render_winner


class WinnerWorker(QtCore.QObject):
    progress=QtCore.pyqtSignal(str)
    result=QtCore.pyqtSignal(object)
    error=QtCore.pyqtSignal(str)
    done=QtCore.pyqtSignal()
    def __init__(self,path,through,kind,since,review_mode='winners',followup_from=''):
        super().__init__();self.args=(path,through,kind,since,review_mode,followup_from);self.stop=threading.Event()
    @QtCore.pyqtSlot()
    def run(self):
        try:
            path,through,kind,since,review_mode,followup_from=self.args
            if review_mode=='prior_series':
                from historical_series_review import review_series
                result=review_series(path,through,self.stop.is_set,self.progress.emit,contest_format=kind,since=since,followup_from=followup_from)
            else:result=review_winners(path,through,self.stop.is_set,self.progress.emit,contest_format=kind,since=since,review_mode=review_mode,followup_from=followup_from)
            if not self.stop.is_set():self.result.emit(result)
        except Exception as exc:
            if not self.stop.is_set():self.error.emit(str(exc))
        finally:self.done.emit()


class WinnerPortfoliosDialog(QtWidgets.QDialog):
    def __init__(self,db_path,parent=None,contest_format='showdown'):
        super().__init__(parent);self.db_path=db_path;self.data=None;self._thread=self._worker=None;self._closing=False
        self.setWindowTitle('Historical Portfolios — Winners and Earlier Repeat Finishes');self.resize(1200,800)
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Review all entries of contest winners, or later portfolios of users with repeated earlier strong finishes. Winning once is not proof of repeatable skill. Peer comparisons use exactly the same observed entry count; nothing changes your rules or models.')
        note.setWordWrap(True);layout.addWidget(note)
        mode_row=QtWidgets.QHBoxLayout();mode_row.addWidget(QtWidgets.QLabel('Review'))
        self.mode=QtWidgets.QComboBox();self.mode.addItem('Contest winners — all entries','winners');self.mode.addItem('Earlier repeat finishes — later portfolios','prior_qualified');self.mode.addItem('Across more games — bounded batches','prior_series');mode_row.addWidget(self.mode)
        self.followup_label=QtWidgets.QLabel('Evaluate from');mode_row.addWidget(self.followup_label)
        self.followup_from=QtWidgets.QDateEdit(QtCore.QDate.currentDate().addDays(-7));self.followup_from.setCalendarPopup(True);self.followup_from.setDisplayFormat('yyyy-MM-dd');mode_row.addWidget(self.followup_from);mode_row.addStretch();layout.addLayout(mode_row)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        row=QtWidgets.QHBoxLayout();row.addWidget(QtWidgets.QLabel('History from'))
        self.since=QtWidgets.QDateEdit(QtCore.QDate.currentDate().addYears(-1));self.since.setCalendarPopup(True);self.since.setDisplayFormat('yyyy-MM-dd');row.addWidget(self.since)
        row.addWidget(QtWidgets.QLabel('Through (inclusive)'))
        self.through=QtWidgets.QDateEdit(QtCore.QDate.currentDate());self.through.setCalendarPopup(True);self.through.setDisplayFormat('yyyy-MM-dd');row.addWidget(self.through)
        self.kind=QtWidgets.QComboBox()
        for label,key in [('Showdown','showdown'),('Classic','classic'),('Both formats','')]:self.kind.addItem(label,key)
        self.kind.setCurrentIndex(max(0,self.kind.findData(contest_format)));row.addWidget(self.kind)
        self.start_button=QtWidgets.QPushButton('Review indexed portfolios');self.start_button.clicked.connect(self.start);row.addWidget(self.start_button)
        self.stop_button=QtWidgets.QPushButton('Stop');self.stop_button.setEnabled(False);self.stop_button.clicked.connect(self.cancel);row.addWidget(self.stop_button);layout.addLayout(row)
        self.find=QtWidgets.QLineEdit();self.find.setPlaceholderText('Find a username or contest in completed results');self.find.setMaxLength(100);self.find.textChanged.connect(self.filter_rows);layout.addWidget(self.find)
        self.status=QtWidgets.QLabel('Uses freshly verified indexed history. Unindexed files must finish normal import/indexing first. No review has run.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.tree=QtWidgets.QTreeWidget();self.tree.setObjectName('winnerPortfolioTree');self.tree.setHeaderLabels(['Contest / entrant','Observed entries','Resolved','Rank-1 entries','CPT pool','Prior rank-1 slates','Exact-entry peers']);self.tree.setUniformRowHeights(True);self.tree.currentItemChanged.connect(self.show_selected)
        self.report=QtWidgets.QPlainTextEdit();self.report.setReadOnly(True);self.report.setObjectName('winnerPortfolioDetail')
        split=QtWidgets.QSplitter(QtCore.Qt.Vertical);split.setChildrenCollapsible(False);split.setHandleWidth(8)
        from pane_styles import labelled_pane
        split.addWidget(labelled_pane('Contests and entrants — select a user',self.tree,'winnerUsersPane'));split.addWidget(labelled_pane('Entire observed portfolio, peer comparison and source limits',self.report,'winnerDetailsPane'));split.setSizes([260,420]);layout.addWidget(split,1)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close);buttons.rejected.connect(self.reject)
        copy=buttons.addButton('Copy displayed review',QtWidgets.QDialogButtonBox.ActionRole);copy.clicked.connect(lambda:QtWidgets.QApplication.clipboard().setText(self.report.toPlainText()));layout.addWidget(buttons);self.mode_changed()
    def mode_changed(self):
        followup=self.mode.currentData()!='winners'
        self.followup_label.setVisible(followup);self.followup_from.setVisible(followup)
    def start(self):
        if self._thread is not None:return
        self._pending=None;self._error=''
        for widget in (self.since,self.through,self.kind,self.start_button,self.mode,self.followup_from):widget.setEnabled(False)
        self.stop_button.setEnabled(True);self.status.setText('Verifying sources… Previous completed results remain visible until the new review succeeds.')
        worker=self._worker=WinnerWorker(self.db_path,self.through.date().toString('yyyy-MM-dd'),self.kind.currentData(),self.since.date().toString('yyyy-MM-dd'),self.mode.currentData(),self.followup_from.date().toString('yyyy-MM-dd'))
        thread=self._thread=QtCore.QThread(self);worker.moveToThread(thread)
        thread.started.connect(worker.run);worker.progress.connect(self.status.setText);worker.result.connect(self.receive);worker.error.connect(self.receive_error)
        worker.done.connect(thread.quit);worker.done.connect(worker.deleteLater);thread.finished.connect(self.retire);thread.start()
    def receive(self,result):
        if self._worker is not None and not self._worker.stop.is_set():self._pending=result
    def receive_error(self,error):self._error=error
    def retire(self):
        stopped=self._worker.stop.is_set();thread=self._thread;self._thread=self._worker=None;thread.deleteLater()
        for widget in (self.since,self.through,self.kind,self.start_button,self.mode,self.followup_from):widget.setEnabled(True)
        self.stop_button.setEnabled(False)
        if stopped:self.status.setText('Stopped. No partial review, rules or model changes were applied.')
        elif self._pending is None:self.status.setText('Review unavailable: '+self._error)
        else:self.load_result(self._pending)
        if self._closing:super().reject()
    def load_result(self,result):
        self.data=result;self.tree.clear()
        followup=result.get('review_mode')=='prior_qualified'
        self.tree.headerItem().setText(5,'Prior top-cutoff slates' if followup else 'Prior rank-1 slates')
        for ci,c in enumerate(result['contests']):
            parent=QtWidgets.QTreeWidgetItem([c['date']+' '+c['name'],str(c['accepted']),'',str(sum(p['winning_entries'] for p in c['winners']))]);parent.setData(0,QtCore.Qt.UserRole,(ci,None));self.tree.addTopLevelItem(parent)
            for pi,p in enumerate(c['winners']):
                item=QtWidgets.QTreeWidgetItem([p['username'],str(p['entries']),str(p['known']),str(p['winning_entries']),str(p['metrics']['captain_pool']) if p['metrics']['captain_pool'] is not None else '—',str(p['prior_record']['top_cutoff_slates'] if followup else p['prior_record']['rank1_slates']),str(p['peer_users'])]);item.setData(0,QtCore.Qt.UserRole,(ci,pi));parent.addChild(item)
        self.tree.resizeColumnToContents(0);self.report.setPlainText(render_overview(result));self.filter_rows()
        self.status.setText(f"Completed: {len(result['contests'])} verified contests; {sum(len(c['winners']) for c in result['contests'])} selected entrant profiles; {len(result['excluded'])} excluded contests. Select an entrant to inspect all their observed entries.")
    def show_selected(self,item,previous=None):
        if item is None or self.data is None:return
        ci,pi=item.data(0,QtCore.Qt.UserRole);c=self.data['contests'][ci]
        self.report.setPlainText(render_winner(c,c['winners'][pi]) if pi is not None else render_overview(dict(self.data,contests=[c],excluded=[])))
    def filter_rows(self):
        text=self.find.text().strip().casefold()
        for i in range(self.tree.topLevelItemCount()):
            parent=self.tree.topLevelItem(i);match=text in parent.text(0).casefold();shown=0
            for j in range(parent.childCount()):
                child=parent.child(j);visible=match or text in child.text(0).casefold();child.setHidden(not visible);shown+=visible
            parent.setHidden(not match and not shown)
            if text and shown:parent.setExpanded(True)
    def cancel(self):
        if self._worker is not None:self._worker.stop.set();self._pending=None;self.stop_button.setEnabled(False)
    def accept(self):
        if self._thread is None:super().accept()
    def reject(self):
        if self._thread is not None:self._closing=True;self.cancel()
        else:super().reject()
    def closeEvent(self,event):
        if self._thread is not None:self._closing=True;self.cancel();event.ignore()
        else:super().closeEvent(event)
