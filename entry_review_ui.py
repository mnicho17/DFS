"""Read-only entry review using in-memory outputs; no simulation work."""
from PyQt5 import QtCore,QtWidgets
from entry_review import review_entries


class NumberItem(QtWidgets.QTableWidgetItem):
    def __init__(self,value,text):
        super().__init__(text);self.setData(QtCore.Qt.UserRole,value)
    def __lt__(self,other):return self.data(QtCore.Qt.UserRole)<other.data(QtCore.Qt.UserRole)


class EntryReviewDialog(QtWidgets.QDialog):
    def __init__(self,generated,saved,kind,cap,parent=None):
        super().__init__(parent);self.setWindowTitle('Review my entries');self.resize(1050,690)
        self.setObjectName('entryReviewDialog');self.inputs=(list(generated),list(saved));self.kind=kind;self.cap=cap
        layout=QtWidgets.QVBoxLayout(self)
        label=QtWidgets.QLabel('Review shared players, repeated cores and roster constructions. This describes your entries; it does not rescore or change them.');label.setWordWrap(True);layout.addWidget(label)
        self.source=QtWidgets.QComboBox();self.source.addItems([f'Generated outputs ({len(generated)})',f'Saved entries ({len(saved)})']);layout.addWidget(self.source)
        self.tabs=QtWidgets.QTabWidget();layout.addWidget(self.tabs)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        copy=buttons.addButton('Copy Report',QtWidgets.QDialogButtonBox.ActionRole);copy.setObjectName('copyEntryReview');copy.clicked.connect(lambda:QtWidgets.QApplication.clipboard().setText(self.report['text']))
        buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.source.currentIndexChanged.connect(self.refresh);self.refresh()
    def refresh(self):
        self.report=review_entries(self.inputs[self.source.currentIndex()],self.kind,self.cap,self.source.currentText())
        while self.tabs.count():
            widget=self.tabs.widget(0);self.tabs.removeTab(0);widget.deleteLater()
        summary=QtWidgets.QPlainTextEdit();summary.setReadOnly(True);summary.setPlainText(self.report['text']);self.tabs.addTab(summary,'Summary')
        for title,rows in self.report['tables'].items():
            if title=='Captain + FLEX' and self.kind!='showdown':continue
            table=QtWidgets.QTableWidget(len(rows),3);table.setHorizontalHeaderLabels(['Pattern','Entries','% of entries'])
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            for i,row in enumerate(rows):
                table.setItem(i,0,QtWidgets.QTableWidgetItem(row['label']))
                table.setItem(i,1,NumberItem(row['count'],str(row['count'])))
                table.setItem(i,2,NumberItem(row['pct'],f"{row['pct']:.1f}%"))
            table.horizontalHeader().setSectionResizeMode(0,QtWidgets.QHeaderView.Stretch)
            table.setSortingEnabled(True);table.sortItems(1,QtCore.Qt.DescendingOrder);self.tabs.addTab(table,title)


def open_entry_review(w):
    if w._current_sport()!='NFL':
        QtWidgets.QMessageBox.information(w,'Review my entries','Construction review currently supports NFL Classic and Showdown.');return
    kind='showdown' if w.tabs_lineups.currentIndex()==0 else 'classic'
    generated=w.last_showdown if kind=='showdown' else w.last_classic
    saved=w.saved_showdown if kind=='showdown' else w.saved_classic
    cap=w._safe_float(w.edit_sd_cap.text() if kind=='showdown' else w.edit_cl_cap.text(),50000)
    EntryReviewDialog(generated,saved,kind,cap,w).exec_()
