"""Always-visible build essentials; existing widgets remain the source of truth."""
from PyQt5 import QtCore, QtWidgets
from compute_settings import DEEP_PROFILES, matching_deep_profile


def setup_simple_build_panel(w, layout, count_labels, cap_labels):
    panel = w.simple_build_panel = QtWidgets.QGroupBox('Build setup', w)
    panel.setObjectName('simpleBuildPanel')
    panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
    grid = QtWidgets.QGridLayout(panel)
    grid.setContentsMargins(10, 7, 10, 7)
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(5)
    w.lbl_simple_lineups = QtWidgets.QLabel('Lineups')
    grid.addWidget(w.lbl_simple_lineups, 0, 0)
    w.simple_count_stack = QtWidgets.QStackedWidget(panel)
    w.simple_count_stack.setMaximumWidth(110)
    # Move the original controls instead of mirroring values into new inputs.
    for spin, label in zip((w.spin_sd, w.spin_cl), count_labels):
        label.hide()
        w.simple_count_stack.addWidget(spin)
    grid.addWidget(w.simple_count_stack, 0, 1)
    w.lbl_simple_time = QtWidgets.QLabel('Time budget')
    grid.addWidget(w.lbl_simple_time, 0, 2)
    w.combo_simple_time = QtWidgets.QComboBox(panel)
    w.combo_simple_time.setObjectName('simpleTimeBudget')
    w.combo_simple_time.addItem('Fast', 'Fast')
    for name, profile in DEEP_PROFILES.items():
        w.combo_simple_time.addItem(f"Up to {profile['minutes']} minutes", name)
    w.combo_simple_time.addItem('Custom…', 'Custom')
    w.combo_simple_time.setToolTip('Timed choices use the existing Deep profiles. They set a maximum time, not a guarantee of completion. Fast retains the current SIM toggle; Advanced shows every setting.')
    grid.addWidget(w.combo_simple_time, 0, 3)
    grid.setColumnStretch(4, 1)
    w.btn_build_advanced = QtWidgets.QToolButton(panel)
    w.btn_build_advanced.setObjectName('buildAdvancedButton')
    w.btn_build_advanced.setText('Advanced')
    w.btn_build_advanced.setCheckable(True)
    w.btn_build_advanced.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    w.btn_build_advanced.setArrowType(QtCore.Qt.RightArrow)
    w.btn_build_advanced.setToolTip('Build settings, portfolio rules, and data controls. Opening this panel does not change the build.')
    w.btn_build_advanced.toggled.connect(w.action_show_build_controls.setChecked)
    grid.addWidget(w.btn_build_advanced, 0, 5)
    w.btn_review_build = QtWidgets.QPushButton('Review build…', panel)
    w.btn_review_build.setObjectName('reviewBuildButton')
    w.btn_review_build.clicked.connect(w.on_build_history)
    w.btn_review_build.setToolTip('Review recorded results, limits, preparation, SIM, and comparisons for a selected build.')
    grid.addWidget(w.btn_review_build, 0, 6)
    grid.addWidget(w.btn_primary_build, 0, 7)
    w.lbl_simple_build_summary = QtWidgets.QLabel(panel)
    w.lbl_simple_build_summary.setObjectName('simpleBuildSummary')
    w.lbl_simple_build_summary.setWordWrap(True)
    w.lbl_simple_build_summary.setTextFormat(QtCore.Qt.PlainText)
    grid.addWidget(w.lbl_simple_build_summary, 1, 0, 1, 8)
    w.lbl_build_flow = QtWidgets.QLabel(panel)
    w.lbl_build_flow.setObjectName('buildFlowStatus')
    w.lbl_build_flow.setWordWrap(True)
    w.lbl_build_flow.setTextFormat(QtCore.Qt.PlainText)
    grid.addWidget(w.lbl_build_flow, 2, 0, 1, 8)
    # Keep unusual caps editable in Advanced while removing repeated result-toolbar inputs.
    advanced_grid = w.lbl_compute_summary.parentWidget().layout()
    w.lbl_advanced_cap = QtWidgets.QLabel('Salary cap')
    w.advanced_cap_stack = QtWidgets.QStackedWidget()
    w.advanced_cap_stack.setMaximumWidth(180)
    for edit, label in zip((w.edit_sd_cap, w.edit_cl_cap), cap_labels):
        label.hide()
        w.advanced_cap_stack.addWidget(edit)
        edit.textChanged.connect(lambda *_: sync_simple_build_panel(w))
    advanced_grid.addWidget(w.lbl_advanced_cap, 7, 0)
    advanced_grid.addWidget(w.advanced_cap_stack, 7, 1)
    layout.insertWidget(layout.indexOf(w.tabs_workspace_controls), panel)
    w.lbl_workspace_summary.hide()  # The full recipe remains available in Advanced and reports.
    w.lbl_workspace_summary.setToolTip('Current build recipe; open Advanced to change it.')

    def choose(index):
        name = w.combo_simple_time.itemData(index)
        if name == 'Custom':
            # Reveal the current values without enabling SIM or rewriting budgets.
            w.action_show_build_controls.setChecked(True)
            w.tabs_workspace_controls.setCurrentIndex(0)
        else:
            # Reuse the existing profile handler and its persistence/validation.
            w.combo_compute_profile.setCurrentText(name)
            w.combo_compute_profile.activated[str].emit(name)
        sync_simple_build_panel(w)

    w.combo_simple_time.activated[int].connect(choose)
    for spin in (w.spin_sd, w.spin_cl):
        spin.valueChanged.connect(lambda *_: sync_simple_build_panel(w))
    for combo in (w.combo_build_own_mode, w.combo_field_preset, w.combo_build_style,
                  w.combo_salary_strategy, w.combo_mlb_stack_pref, w.combo_nfl_compute_mode):
        combo.currentTextChanged.connect(lambda *_: sync_simple_build_panel(w))
    w.spin_build_own_weight.valueChanged.connect(lambda *_: sync_simple_build_panel(w))
    w.action_show_build_controls.toggled.connect(lambda *_: sync_simple_build_panel(w))
    w.action_auto_library.toggled.connect(lambda *_: sync_simple_build_panel(w))
    sync_simple_build_panel(w)


def sync_simple_build_panel(w):
    if not hasattr(w, 'simple_build_panel') or getattr(w, '_syncing_simple_build', False):
        return
    w._syncing_simple_build = True
    try:
        index = w.tabs_lineups.currentIndex()
        kind = 'showdown' if index == 0 else 'classic'
        lineup = index in (0, 1)
        nfl = w._current_sport() == 'NFL'
        w.simple_count_stack.setCurrentIndex(0 if kind == 'showdown' else 1)
        w.advanced_cap_stack.setCurrentIndex(0 if kind == 'showdown' else 1)
        w.lbl_simple_lineups.setVisible(lineup)
        w.simple_count_stack.setVisible(lineup)
        w.lbl_simple_time.setVisible(nfl and lineup)
        w.combo_simple_time.setVisible(nfl and lineup)
        deep = nfl and w.chk_nfl_contest_sim.isChecked() and w.combo_nfl_compute_mode.currentText().startswith('Deep')
        name = matching_deep_profile(w.deep_compute_settings, w.spin_nfl_sim_scenarios.value()) if deep else 'Fast'
        blocker = QtCore.QSignalBlocker(w.combo_simple_time)
        custom = w.combo_simple_time.findData('Custom')
        w.combo_simple_time.setItemText(custom, f"Custom — up to {w.deep_compute_settings['minutes']} min" if deep and name == 'Custom' else 'Custom…')
        w.combo_simple_time.setCurrentIndex(w.combo_simple_time.findData(name))
        del blocker
        expanded = w.action_show_build_controls.isChecked()
        blocker = QtCore.QSignalBlocker(w.btn_build_advanced)
        w.btn_build_advanced.setChecked(expanded)
        w.btn_build_advanced.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        del blocker
        w.lbl_advanced_cap.setText('Salary cap (' + kind.title() + ')')
        if not lineup:
            summary = w._current_sport() + ' stacks · Choose Refresh Stacks to update the stack report.'
        else:
            parts = [w._current_sport() + ' ' + kind.title()]
            if nfl and hasattr(w, 'chk_auto_entry_target'):
                label = 'Auto entry target' if w.chk_auto_entry_target.isChecked() else 'Manual entry target'
                parts.append(label + ': ' + w.combo_field_preset.currentText())
            parts.extend((w.combo_build_style.currentText(), w.combo_salary_strategy.currentText()))
            cap = w.edit_sd_cap if kind == 'showdown' else w.edit_cl_cap
            parts.append('Cap: ' + cap.text())
            if nfl:
                parts.append('Deep SIM' if deep else 'Fast SIM' if kind == 'classic' and w.chk_nfl_contest_sim.isChecked() else 'SIM off')
                if deep and name == 'Custom':
                    parts.append('Custom compute settings')
                if kind == 'showdown' and w.chk_showdown_history.isChecked():
                    parts.append('Historical field requested' + ('' if deep else ' — Deep required'))
            else:
                parts.append('Ownership: ' + w.combo_build_own_mode.currentText())
                if w._current_sport() == 'MLB':
                    parts.append(w.combo_mlb_stack_pref.currentText())
            if w._active_recipe_name:
                parts.insert(0, 'Recipe: ' + w._active_recipe_name)
            summary = ' · '.join(parts)
        if not getattr(w, '_build_flow_active', False):
            from build_workflow import preparation_plan
            w.lbl_build_flow.setText(preparation_plan(w) if lineup else '')
        w.lbl_simple_build_summary.setText(summary)
        w.lbl_simple_build_summary.setToolTip(
            w.lbl_compute_summary.text() + '\nOwnership: ' + w.combo_build_own_mode.currentText()
            + f' (influence {w.spin_build_own_weight.value():g})'
            + '\nEntry targets are defaults; actual contest size and payouts require a contest profile.'
            + '\nAll current settings remain editable in Advanced.')
    finally:
        w._syncing_simple_build = False


def advanced_scroll_page(panel):
    """Let Advanced grow vertically without clipping controls on smaller screens."""
    scroll = QtWidgets.QScrollArea()
    scroll.setObjectName('advancedBuildScroll')
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setMinimumHeight(220)
    panel.layout().setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
    scroll.setWidget(panel)
    return scroll
