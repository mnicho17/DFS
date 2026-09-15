"""Sortable read-only core-play shortlist from already loaded player inputs."""
from PyQt5 import QtCore,QtWidgets
from core_plays import build_core_plays,format_core_report,focused_core_rows,CATEGORIES,POSITIVE


class CoreNumberItem(QtWidgets.QTableWidgetItem):
    def __init__(self,value,pattern):
        super().__init__('Unknown' if value is None else format(value,pattern))
        self.value=value
    def __lt__(self,other):
        if self.value is None or other.value is None:
            if self.value is None and other.value is None:return False
            descending=self.tableWidget().horizontalHeader().sortIndicatorOrder()==QtCore.Qt.DescendingOrder
            return self.value is None if descending else other.value is None
        return self.value<other.value


class CorePlaysDialog(QtWidgets.QDialog):
    def __init__(self,players,kind,parent=None):
        super().__init__(parent);self.setWindowTitle('Core Plays — '+kind.title());self.resize(1180,760)
        self.setObjectName('corePlaysDialog');self.report=build_core_plays(players,kind)
        layout=QtWidgets.QVBoxLayout(self)
        intro=QtWidgets.QLabel('Find salary and role candidates in your loaded data. Select a player for supporting numbers and review notes. No Deep build is needed.\nOwnership is estimated. Use the position filter for value comparisons. Refresh player data in the main window before relying on current roles; this view does not refresh or change inputs.');intro.setWordWrap(True);layout.addWidget(intro)
        controls=QtWidgets.QHBoxLayout()
        self.category=QtWidgets.QComboBox();self.category.addItems(['Starting shortlist','Core candidates','All eligible','All loaded']+list(CATEGORIES))
        self.slot=QtWidgets.QComboBox();self.slot.addItems(['All slots']+(['Captain','FLEX'] if kind=='showdown' else ['Regular']))
        self.position=QtWidgets.QComboBox();self.position.addItems(['All positions','QB','RB','WR','TE','K','DST'])
        self.search=QtWidgets.QLineEdit();self.search.setPlaceholderText('Search player, team or position')
        for w in (self.category,self.slot,self.position,self.search):controls.addWidget(w)
        layout.addLayout(controls)
        self.count=QtWidgets.QLabel();layout.addWidget(self.count)
        split=QtWidgets.QSplitter(QtCore.Qt.Vertical);layout.addWidget(split,1)
        self.table=QtWidgets.QTableWidget(0,8)
        self.table.setHorizontalHeaderLabels(['Player','Slot','Salary','Proj. pts','Pts/$1,000','Est. own %','Signals','Review'])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSortIndicator(-1,QtCore.Qt.AscendingOrder)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        for j,width in enumerate([200,68,75,88,88,88,230,240]):self.table.setColumnWidth(j,width)
        split.addWidget(self.table)
        self.details=QtWidgets.QPlainTextEdit();self.details.setReadOnly(True);split.addWidget(self.details);split.setSizes([440,200])
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.copy=buttons.addButton('Copy Report',QtWidgets.QDialogButtonBox.ActionRole)
        self.copy.setObjectName('copyCorePlays');self.copy.clicked.connect(self.copy_report)
        buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.category.currentIndexChanged.connect(self.refresh);self.slot.currentIndexChanged.connect(self.refresh)
        self.position.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh);self.table.itemSelectionChanged.connect(self.show_details)
        self.refresh()
    def refresh(self):
        mode=self.category.currentText();slot=self.slot.currentText();query=self.search.text().strip().casefold()
        def include(r):
            if self.position.currentText()!='All positions' and r['position']!=self.position.currentText():return False
            if slot!='All slots' and r['slot']!=slot:return False
            if query and query not in r['player'].casefold():return False
            if mode in ('Starting shortlist','Core candidates'):return bool(POSITIVE.intersection(r['categories']))
            if mode=='All eligible':return r['eligible']
            if mode=='All loaded':return True
            return mode in r['categories']
        source=focused_core_rows(self.report['rows']) if mode=='Starting shortlist' else self.report['rows']
        self.visible=[r for r in source if include(r)]
        self.table.setSortingEnabled(False);self.table.setRowCount(0);self.table.setRowCount(len(self.visible))
        for i,r in enumerate(self.visible):
            text=[r['player'],r['slot'],None,None,None,None,', '.join(r['categories']),
                  '; '.join(r['excluded']+r['concerns']) or 'No threshold warnings; not validated']
            numeric={2:('salary',',.0f'),3:('projection','.2f'),4:('value','.2f'),5:('ownership','.1f')}
            for j in range(8):
                item=CoreNumberItem(r[numeric[j][0]],numeric[j][1]) if j in numeric else QtWidgets.QTableWidgetItem(text[j])
                if j==0:item.setData(QtCore.Qt.UserRole,i)
                item.setToolTip(item.text());self.table.setItem(i,j,item)
        self.table.setSortingEnabled(True)
        flagged=sum(bool(r['concerns']) for r in self.visible)
        self.count.setText(f'{len(self.visible)} player/slot rows shown; {flagged} with review notes. Select a row for reasons and workload. Copy Report includes this filtered view.')
        if mode=='Starting shortlist':
            self.count.setText(self.count.text()+'\nUp to 3 per position/slot: an anchor, a distinct value, and a distinct lower-owned alternative when available; remaining places use projection. Review notes still apply. Core candidates shows the full list.')
        self.count.setWordWrap(True)
        if self.visible:self.table.selectRow(0);self.show_details()
        else:self.details.setPlainText('No matching candidates. Use All loaded to inspect missing forecasts, exclusions or uncertain roles. Refresh player data from the main window when needed.')
    def ordered_rows(self):
        return [self.visible[self.table.item(i,0).data(QtCore.Qt.UserRole)] for i in range(self.table.rowCount())]
    def show_details(self):
        row=self.table.currentRow()
        if row<0 or self.table.item(row,0) is None:return
        idx=self.table.item(row,0).data(QtCore.Qt.UserRole)
        if idx is not None and idx<len(self.visible):self.details.setPlainText('\n'.join(format_core_report(self.report,[self.visible[idx]]).splitlines()[5:]))
    def copy_report(self):
        text=format_core_report(self.report,self.ordered_rows())
        if self.category.currentText()=='Starting shortlist':
            text+='\nStarting shortlist: up to 3 per position/slot; anchor, distinct value, distinct lower-owned alternative where available, then projection. A review starting point, not a SIM ranking or recommended exposure. Full candidates remain available.'
        QtWidgets.QApplication.clipboard().setText(text)


def open_core_plays(w):
    if w._current_sport()!='NFL':
        QtWidgets.QMessageBox.information(w,'Core Plays','Core Plays currently supports NFL Classic and Showdown.');return
    if not w.players:
        QtWidgets.QMessageBox.information(w,'Core Plays','Load a salary file and refresh player data first.');return
    CorePlaysDialog(w.players,'showdown' if w.tabs_lineups.currentIndex()==0 else 'classic',w).exec_()
