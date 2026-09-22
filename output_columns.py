"""Readable, user-resizable lineup columns with per-session width retention."""
import re
from PyQt5 import QtCore, QtWidgets


def column_keys(table):
    counts={};keys=[]
    for col in range(table.columnCount()):
        item=table.horizontalHeaderItem(col)
        label=item.text() if item else ''
        label=re.sub(r'^(FLEX|RB|WR|P)([1-9])$',r'\1',label)
        counts[label]=counts.get(label,0)+1
        keys.append((label,counts[label]))
    return tuple(keys)


def fit_output_columns(table):
    header=table.horizontalHeader()
    if not hasattr(table,'_lineup_user_widths'):
        table._lineup_user_widths={}
        def remember(col,old,new):
            keys=column_keys(table)
            if not getattr(table,'_lineup_fitting',False) and keys==getattr(table,'_lineup_width_schema',None):
                table._lineup_user_widths[keys[col]]=new
        header.sectionResized.connect(remember)
    table._lineup_fitting=True
    try:
        keys=column_keys(table)
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(48)
        table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        table.setWordWrap(False)
        summary=False
        for col,key in enumerate(keys):
            label=key[0];item=table.horizontalHeaderItem(col)
            if label in ('TotalSal','Grade','SIM Edge'):summary=True
            player=col>0 and not summary
            width=54 if label=='Save' else 126 if label=='SIM Edge' else 92
            if player:
                widest=max((table.fontMetrics().horizontalAdvance(table.item(row,col).text())
                    for row in range(table.rowCount()) if table.item(row,col)),default=0)
                width=max(180,min(260,widest+20))
            elif item and label!='Save':
                width=max(width,table.fontMetrics().horizontalAdvance(item.text())+28)
            header.resizeSection(col,table._lineup_user_widths.get(key,width))
            if item:
                alignment=QtCore.Qt.AlignLeft if player else QtCore.Qt.AlignRight if label=='TotalSal' else QtCore.Qt.AlignCenter
                item.setTextAlignment(int(QtCore.Qt.AlignVCenter | alignment))
                item.setToolTip('Drag the column divider to resize; double-click it to fit contents. Scroll horizontally to see later columns.')
            for row in range(table.rowCount()):
                cell=table.item(row,col)
                if cell and player:
                    if not cell.toolTip():cell.setToolTip(cell.text())
                elif cell and col>0:
                    cell.setTextAlignment(int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
        table._lineup_width_schema=keys
    finally:table._lineup_fitting=False
