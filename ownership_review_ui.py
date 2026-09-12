"""Saved comparison review and optional matching to current result tables."""
from pathlib import Path
from PyQt5 import QtCore, QtWidgets
from build_diagnostics import build_history_path
from ownership_review import COLUMNS, PROJECTION_COLUMNS, KEYS, load_review, tooltip

class ReviewDialog(QtWidgets.QDialog):
    def __init__(self, parent, review):
        super().__init__(parent); self.setWindowTitle('Saved '+review.get('comparison','ownership').title()+' Comparison'); self.resize(1150,650)
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel(tooltip(review)); note.setWordWrap(True); layout.addWidget(note)
        if not review['compatible']:
            layout.addWidget(QtWidgets.QLabel('Historical model version: review only; cannot attach to current lineups.'))
        table=QtWidgets.QTableWidget(len(review['rows']), 7); self.table=table
        columns=PROJECTION_COLUMNS if review.get('comparison')=='projection' else COLUMNS
        table.setHorizontalHeaderLabels(['Saved rank','Lineup']+list(columns))
        for i,row in enumerate(review['rows']):
            for j,key in enumerate(('saved_rank','lineup')+KEYS):
                value=row[key]; item=QtWidgets.QTableWidgetItem()
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if isinstance(value,(int,float)): item.setData(QtCore.Qt.DisplayRole, round(value, 3))
                else: item.setText(value)
                item.setToolTip(row['lineup']+'\n\n'+tooltip(review)); table.setItem(i,j,item)
        table.setSortingEnabled(True); table.sortItems(3,QtCore.Qt.DescendingOrder)
        for col,width in enumerate((90,320,140,145,115,105,105)):table.setColumnWidth(col,width)
        layout.addWidget(table)
        close=QtWidgets.QPushButton('Close'); close.clicked.connect(self.accept); layout.addWidget(close)

def open_review(parent, kind=None, comparison='ownership'):
    history=Path(build_history_path()).parent
    path,_=QtWidgets.QFileDialog.getOpenFileName(parent,'Choose saved '+comparison+' comparison',str(history/(comparison+'-checks')),'Comparison reports (*.json)')
    if not path:return
    try:
        review=load_review(path,history/'ranking-banks',comparison=comparison)
        if kind and review['kind'] != kind:raise ValueError('Choose a comparison for this contest format.')
    except Exception as exc:
        QtWidgets.QMessageBox.warning(parent,'Cannot load comparison',str(exc));return
    if kind:
        review['lookup']={r['key']:r for r in review['rows']}
        setattr(parent,'_'+kind+'_'+comparison+'_review',review)
        choice=getattr(parent,'_'+kind+'_sort',None)
        if choice and choice[2] in COLUMNS+PROJECTION_COLUMNS:setattr(parent,'_'+kind+'_sort',None)
        parent._change_result_page(kind,0)
        current=parent.last_classic if kind=='classic' else parent.last_showdown
        from ownership_review import comparison_value
        matched=sum(comparison_value(parent,lu,kind,comparison) is not None for lu in current)
        parent.status.showMessage(f'{comparison.title()} comparison: {matched}/{len(current)} exact player-input matches. Unmatched rows show a dash.',10000)
    ReviewDialog(parent,review).exec_()
