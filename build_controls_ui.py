"""Compact build controls, backed by the existing recipe and worker settings."""
import json
from PyQt5 import QtCore, QtWidgets
from compute_settings import DEEP_PROFILES, matching_deep_profile, normalize_deep_settings


def setup_build_controls(window, panel, grid):
    w=window
    w.lbl_compute_profile=QtWidgets.QLabel('Compute profile')
    w.combo_compute_profile=QtWidgets.QComboBox(panel)
    w.combo_compute_profile.setObjectName('computeProfile')
    w.combo_compute_profile.addItems(['Fast']+list(DEEP_PROFILES)+['Custom'])
    w.combo_compute_profile.setMinimumWidth(190)
    w.combo_compute_profile.setToolTip('Deep profiles set the time cap and simulation counts together. Custom keeps your current values for editing.')
    w.lbl_build_style=QtWidgets.QLabel('Build style')
    w.lbl_compute_summary=QtWidgets.QLabel(panel)
    w.lbl_compute_summary.setObjectName('computeSummary')
    w.lbl_compute_summary.setWordWrap(True)
    grid.addWidget(w.lbl_compute_profile,0,0)
    grid.addWidget(w.combo_compute_profile,0,1)
    grid.addWidget(w.btn_deep_compute,0,2,1,2)
    grid.addWidget(w.lbl_build_style,1,0);grid.addWidget(w.combo_build_style,1,1)
    grid.addWidget(QtWidgets.QLabel('Salary use'),1,2);grid.addWidget(w.combo_salary_strategy,1,3)
    grid.addWidget(QtWidgets.QLabel('Ownership preference'),2,0);grid.addWidget(w.combo_build_own_mode,2,1)
    grid.addWidget(QtWidgets.QLabel('Influence'),2,2);grid.addWidget(w.spin_build_own_weight,2,3)
    w.lbl_mlb_stack_pref=QtWidgets.QLabel('MLB stack')
    grid.addWidget(w.lbl_mlb_stack_pref,3,0);grid.addWidget(w.combo_mlb_stack_pref,3,1)
    grid.addWidget(w.lbl_field_preset,3,0);grid.addWidget(w.combo_field_preset,3,1)
    grid.addWidget(w.chk_nfl_contest_sim,3,2)
    w.lbl_nfl_scenarios=QtWidgets.QLabel('Scenarios')
    grid.addWidget(w.lbl_nfl_scenarios,4,2);grid.addWidget(w.spin_nfl_sim_scenarios,4,3)
    grid.addWidget(w.lbl_compute_summary,5,0,1,4)
    for col in (1,3):grid.setColumnStretch(col,1)
    # Existing controls remain the recipe source of truth but no longer duplicate the profile picker.
    w.combo_nfl_compute_mode.setParent(panel);w.lbl_nfl_compute_mode.setParent(panel)
    w.combo_nfl_compute_mode.hide();w.lbl_nfl_compute_mode.hide()
    def apply(name):
        if name=='Fast':
            w.combo_nfl_compute_mode.setCurrentText('Fast (default)')
        else:
            if name in DEEP_PROFILES:
                profile=DEEP_PROFILES[name]
                w.deep_compute_settings=normalize_deep_settings(dict(w.deep_compute_settings,**{k:v for k,v in profile.items() if k!='scenarios'}))
                w.spin_nfl_sim_scenarios.setValue(profile['scenarios'])
            w.chk_nfl_contest_sim.setChecked(True)
            w.combo_nfl_compute_mode.setCurrentText('Deep (custom budget)')
            w.app_settings.setValue('build/deep_compute_json',json.dumps(w.deep_compute_settings))
        w._update_deep_compute_button()
        if name == 'Custom':
            w._edit_deep_compute_settings(custom=True)
        w._update_workspace_summary()
    w.combo_compute_profile.activated[str].connect(apply)
    w.chk_nfl_contest_sim.toggled.connect(lambda *_:sync_build_controls(w))
    w.spin_nfl_sim_scenarios.valueChanged.connect(lambda *_:sync_build_controls(w))


def sync_build_controls(w):
    if not hasattr(w,'combo_compute_profile'):return
    nfl=w.combo_sport.currentText().upper()=='NFL'
    deep=w.chk_nfl_contest_sim.isChecked() and w.combo_nfl_compute_mode.currentText().startswith('Deep')
    profile=matching_deep_profile(w.deep_compute_settings,w.spin_nfl_sim_scenarios.value()) if deep else 'Fast'
    blocker=QtCore.QSignalBlocker(w.combo_compute_profile)
    w.combo_compute_profile.setCurrentText(profile)
    del blocker
    w.lbl_compute_profile.setVisible(nfl);w.combo_compute_profile.setVisible(nfl)
    w.combo_nfl_compute_mode.hide();w.lbl_nfl_compute_mode.hide()
    w.chk_nfl_contest_sim.setVisible(nfl and not deep and w._contest_mode()!='showdown')
    w.lbl_nfl_scenarios.setVisible(nfl and not deep and w._contest_mode()!='showdown' and w.chk_nfl_contest_sim.isChecked())
    w.spin_nfl_sim_scenarios.setVisible(not w.lbl_nfl_scenarios.isHidden())
    all_styles=nfl and deep and w.deep_compute_settings['all_styles']
    w.combo_build_style.setEnabled(not all_styles)
    w.lbl_build_style.setText('Build style (all searched)' if all_styles else 'Build style')
    w.combo_build_style.setToolTip('All five styles are searched. Change search scope in Search & output.' if all_styles else 'Style used for candidate generation.')
    w.btn_deep_compute.setText('Search && output…')
    w.lbl_compute_summary.setVisible(nfl)
    if deep:
        scope='All five styles' if all_styles else 'Selected style'
        w.lbl_compute_summary.setText(f"{scope} · {w.deep_compute_settings['selection_mode']} · {w.deep_compute_settings['minutes']} min maximum · {w.spin_nfl_sim_scenarios.value():,} validation scenarios")
    else:
        w.lbl_compute_summary.setText('Fast build. Choose a Deep profile for a timed search and independent validation.')

    from entry_target import sync_entry_target
    sync_entry_target(w)
