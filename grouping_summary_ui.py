"""Read-only grouping summary and saved-lineup review for the Build panel."""
from PyQt5 import QtCore, QtWidgets
from portfolio_rules import normalize_rules, _group_ok, _lineup_sets, lineup_captain, player_key
from showdown_common_groups import RULES, PREFIX


def inspect_groups(groups, lineups, kind):
    normalized = normalize_rules({'groups': groups})['groups']
    titles=[]
    for group in normalized:
        label=group['label'] or group['type'].replace('_',' ')
        title=next((title for title in RULES.values()
                    if label==PREFIX+title or label.startswith(PREFIX+title+': ')),None)
        title=title or label
        if title not in titles:titles.append(title)
    affected=[]
    for index,lineup in enumerate(lineups,1):
        keys=_lineup_sets(lineup,kind)[0]
        captain=player_key(lineup_captain(lineup,kind) or {})
        broken=[group['label'] or group['type'].replace('_',' ') for group in normalized
                if not _group_ok(keys,[group],captain)]
        if broken:affected.append((index,broken))
    return titles,affected


def current(w):
    kind='showdown' if w.tabs_lineups.currentIndex()==0 else 'classic'
    saved=getattr(w,'saved_showdown' if kind=='showdown' else 'saved_classic',[]) or []
    titles,affected=inspect_groups(getattr(w,'portfolio_groups',[]),saved,kind)
    return kind,saved,titles,affected


def refresh(w):
    if not hasattr(w,'lbl_active_groups'):return
    kind,saved,titles,affected=current(w)
    visible=w.tabs_lineups.currentIndex() in (0,1)
    w.lbl_active_groups.setVisible(visible)
    w.btn_review_groups.setVisible(visible and bool(affected))
    names='; '.join(titles) if titles else 'None'
    w.lbl_active_groups.setText('Active grouping rules: '+names+
        (f'\nSaved {kind}: {len(affected)} of {len(saved)} lineup(s) conflict with current groups.' if saved and titles else ''))
    w.lbl_active_groups.setToolTip('\n'.join(titles))


def review(w):
    kind,saved,titles,affected=current(w)
    dialog=QtWidgets.QDialog(w)
    dialog.setWindowTitle('Saved lineup grouping review')
    dialog.resize(850,550)
    layout=QtWidgets.QVBoxLayout(dialog)
    note=QtWidgets.QLabel(f'{len(affected)} of {len(saved)} saved {kind} lineup(s) conflict with current grouping rules. '
                         'Lineup numbers follow Saved Portfolio order. This checks grouping rules only. Saved entries and settings are unchanged.')
    note.setWordWrap(True);note.setTextFormat(QtCore.Qt.PlainText);layout.addWidget(note)
    text=QtWidgets.QPlainTextEdit(dialog);text.setObjectName('affectedGroupingReport');text.setReadOnly(True)
    lines=[]
    for index,broken in affected:
        lines.append(f'Saved lineup {index}')
        lines.extend('  '+label for label in broken)
        lines.append('')
    text.setPlainText('\n'.join(lines) or 'No saved lineups conflict with current grouping rules.')
    layout.addWidget(text)
    buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
    dialog.exec_()


def setup(w,grid,panel):
    w.lbl_active_groups=QtWidgets.QLabel(panel)
    w.lbl_active_groups.setObjectName('activeGroupingSummary')
    w.lbl_active_groups.setTextFormat(QtCore.Qt.PlainText)
    w.lbl_active_groups.setWordWrap(True)
    grid.addWidget(w.lbl_active_groups,4,0,1,7)
    w.btn_review_groups=QtWidgets.QPushButton('Review affected lineups…',panel)
    w.btn_review_groups.setObjectName('reviewAffectedGroups')
    w.btn_review_groups.clicked.connect(lambda:review(w))
    grid.addWidget(w.btn_review_groups,4,7,1,2)
    refresh(w)
