"""Ownership provenance, explicit troubleshooting and recorded build comparisons."""
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



def ownership_status(owner):
    """Read displayed provenance and worker state; never validate reuse or estimate."""
    from ownership_refresh_notice import describe
    run=getattr(owner,'_ownership_run',None)
    loading=bool(getattr(owner,'csv_load',None) and owner.csv_load.busy)
    closing=bool(getattr(owner,'_ownership_closing',False))
    if closing:state='Closing'
    elif run is not None:state='Stopping safely' if getattr(run,'cancelled',False) or getattr(run,'closing',False) else 'Ownership estimation underway'
    elif loading:state='Waiting for CSV loading'
    elif owner._snapshot_busy() or getattr(owner,'_build_flow_active',False):state='Waiting for current work'
    elif not owner.players:state='Load a CSV'
    else:state='Displayed ownership estimates'
    source=describe(owner.players,getattr(owner,'last_live_check_summary',{}))
    route=('Snapshot replay: viewing these controls does not start an ownership estimate.' if getattr(owner,'_snapshot_replay',False)
        else 'Normal CSV loading automatically estimates ownership or checks compatible prepared estimates.')
    detail=state+'.\n'+route
    if source['sources']:detail+='\nDisplayed sources: '+source['sources']
    if source['notice']:detail+='\n'+source['notice']
    detail+='\nDisplayed provenance is not current-input validation. Manual recalculation uses the sampling settings below.'
    enabled=bool(owner.players) and run is None and not loading and not closing and not owner._snapshot_busy() and not getattr(owner,'_build_flow_active',False)
    return dict(state=state,detail=detail,can_retry=enabled)


def retry_ownership(owner):
    if ownership_status(owner)['can_retry']:owner.on_recalc_ownership_sim()
    update_ownership_controls(owner)


def update_ownership_controls(owner):
    if not hasattr(owner,'ownership_status_label'):return
    view=ownership_status(owner)
    if owner.ownership_status_label.text()!=view['detail']:owner.ownership_status_label.setText(view['detail'])
    owner.ownership_retry_button.setEnabled(view['can_retry'])


def setup_ownership_controls(owner,button):
    """Keep the original widgets and numerical settings as the source of truth."""
    group=QtWidgets.QGroupBox('Ownership estimates')
    layout=QtWidgets.QVBoxLayout(group)
    owner.ownership_status_label=QtWidgets.QLabel();owner.ownership_status_label.setWordWrap(True)
    owner.ownership_status_label.setTextFormat(QtCore.Qt.PlainText)
    owner.ownership_status_label.setObjectName('ownershipAutomaticStatus');layout.addWidget(owner.ownership_status_label)
    toggle=owner.ownership_troubleshooting=QtWidgets.QToolButton()
    toggle.setText('Manual recalculation / troubleshooting');toggle.setCheckable(True)
    toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon);toggle.setArrowType(QtCore.Qt.RightArrow)
    body=owner.ownership_manual_panel=QtWidgets.QWidget();form=QtWidgets.QFormLayout(body)
    form.addRow('Sampling count',owner.spin_own_sims);form.addRow(owner.chk_sd_template_sim)
    owner.ownership_retry_button=button;button.setObjectName('ownershipRetryButton');button.setText('Recalculate / Retry ownership')
    button.setToolTip('Explicitly run the existing ownership estimator with the sampling count, format, cap and settings shown here. Existing estimates remain until a matching completed result is applied.')
    form.addRow(button);body.hide()
    def expand(value):
        body.setVisible(value);toggle.setArrowType(QtCore.Qt.DownArrow if value else QtCore.Qt.RightArrow)
    toggle.toggled.connect(expand);layout.addWidget(toggle);layout.addWidget(body)
    # Refresh once more after the current signal stack retires worker references.
    # This is event-driven UI work, never a polling or estimation timer.
    timer=QtCore.QTimer(group);timer.setSingleShot(True)
    timer.timeout.connect(lambda:update_ownership_controls(owner))
    def changed():
        update_ownership_controls(owner);timer.start(0)
    owner.tasks.changed.connect(changed)
    update_ownership_controls(owner)
    return group



def show_ownership_controls(owner):
    """Navigate to existing diagnostics without expanding or running recalculation."""
    target=owner.ownership_status_label
    owner.action_show_build_controls.setChecked(True)
    tabs=owner.tabs_workspace_controls
    for index in range(tabs.count()):
        page=tabs.widget(index)
        if page.isAncestorOf(target):
            tabs.setCurrentIndex(index)
            if isinstance(page,QtWidgets.QScrollArea):page.ensureWidgetVisible(target)
            break
    update_ownership_controls(owner)
    owner.ownership_troubleshooting.setFocus()
