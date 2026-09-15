"""Sortable ownership comparison for the latest simulated build."""
from PyQt5 import QtCore, QtWidgets
from ownership_strategy import format_leverage


class OwnershipDialog(QtWidgets.QDialog):
    def __init__(self,parent,report):
        super().__init__(parent);self.setWindowTitle('Ownership & Leverage');self.resize(1100,650)
        layout=QtWidgets.QVBoxLayout(self)
        label=QtWidgets.QLabel(report.get('note') or 'Run an NFL SIM build to compare projected field ownership with simulated contenders and your selected outputs.')
        label.setWordWrap(True);layout.addWidget(label)
        rows=report.get('rows',[])
        table=QtWidgets.QTableWidget(len(rows),9)
        table.setHorizontalHeaderLabels(['Player','Slot','Field %','Sampled %','Contender %','Difference pp','Your %','Confidence','Source'])
        for i,row in enumerate(rows):
            for j,key in enumerate(['player','slot','field_pct','sampled_pct','contender_pct','gap_pp','your_pct','confidence','source']):
                value=row.get(key);item=QtWidgets.QTableWidgetItem()
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if isinstance(value,(float,int)):item.setData(QtCore.Qt.DisplayRole,round(value,2))
                else:item.setText('Unknown' if value is None else str(value))
                table.setItem(i,j,item)
        table.setSortingEnabled(True);table.sortItems(5,QtCore.Qt.DescendingOrder);table.resizeColumnsToContents()
        layout.addWidget(table)
        button=QtWidgets.QPushButton('Copy Report');layout.addWidget(button)
        button.clicked.connect(lambda:QtWidgets.QApplication.clipboard().setText('\n'.join(format_leverage(report))))
