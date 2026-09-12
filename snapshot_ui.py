"""Snapshot actions shared by NFL Classic and Showdown."""
import copy
import os
from PyQt5 import QtCore, QtWidgets
from build_snapshots import create_snapshot, load_snapshot, save_snapshot, freshness_text, validate_snapshot
from build_diagnostics import build_history_path
from learning_db import load_nfl_field_calibration


class SnapshotActions:
    def on_ownership_sensitivity(self):
        if self._snapshot_busy():
            self.status.showMessage('Wait for the current build to finish.',5000)
            return
        from ownership_sensitivity_ui import SensitivityDialog
        SensitivityDialog(self).exec_()

    def on_ownership_leverage(self):
        from ownership_ui import OwnershipDialog
        OwnershipDialog(self,getattr(self,'last_sim_report',{}).get('ownership_leverage') or {}).exec_()

    def on_ranking_repeatability(self):
        if self._snapshot_busy():
            self.status.showMessage('Wait for the current build to finish.',5000)
            return
        from repeatability_ui import RepeatabilityDialog
        RepeatabilityDialog(self).exec_()

    def on_long_search(self):
        if self._snapshot_busy():
            self.status.showMessage('Wait for the current build to finish.',5000)
            return
        from long_search_ui import LongSearchDialog
        LongSearchDialog(self).exec_()

    def on_load_candidate_library(self):
        if self._snapshot_busy():
            return
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Load Candidate Library','','Candidate library (*.dfslib)')
        if not path:return
        try:
            from candidate_library import load_candidates
            recipe=self._current_build_recipe()
            _,report=load_candidates(path,self.players,kind=self._contest_mode(),
                salary_cap=float(recipe.get('salary_cap') or 50000),salary_strategy=recipe.get('salary_strategy','Near Cap'),rules=self._portfolio_rules())
            self._candidate_library=path
            self.status.showMessage(f"Library loaded: {report['accepted']:,} eligible candidates. Choose Deep with SIM enabled, then Build. Current inputs will be used.",15000)
            self.lbl_snapshot_data.setText(f"Candidate library loaded: {report['accepted']:,} candidates — Deep required")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self,'Library Not Loaded',str(exc))

    def on_clear_candidate_library(self):
        self._candidate_library=''
        self._refresh_snapshot_label()
        self.status.showMessage('Candidate library cleared. Next build generates fresh candidates.',6000)

    def _snapshot_busy(self):
        for name in ('_build_thread', '_own_thread'):
            thread = getattr(self, name, None)
            try:
                if thread is not None and thread.isRunning():
                    return True
            except RuntimeError:
                pass
        return False

    def _snapshot_calibration(self):
        if getattr(self, '_snapshot_replay', False):
            return copy.deepcopy(self._snapshot_calibration_data)
        return load_nfl_field_calibration(self.combo_field_preset.currentText()) if self._contest_mode() == 'classic' else {}

    def _capture_snapshot(self, calibration=None, contest=None):
        if self._current_sport() != 'NFL':
            raise ValueError('Snapshots currently support NFL Classic and Showdown.')
        return create_snapshot(self.players, self._current_build_recipe(), self._portfolio_rules(),
                               self._snapshot_calibration() if calibration is None else calibration,
                               self._active_contest_profile() if contest is None else contest,
                               self.last_live_check_summary)

    def on_save_snapshot(self):
        if self._snapshot_busy():
            self.status.showMessage('Wait for the current build or ownership calculation to finish.', 5000)
            return
        try:
            snapshot = self._capture_snapshot()
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save Build Snapshot',
                os.path.join(os.path.dirname(build_history_path()), 'snapshots', snapshot['input_id'][:12] + '.json'),
                'Build snapshot (*.json)')
            if path:
                save_snapshot(path, snapshot)
                self.status.showMessage('Build snapshot saved. It includes player inputs and settings.', 6000)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, 'Snapshot Not Saved', str(exc))

    def _restore_snapshot(self, snapshot):
        value = validate_snapshot(snapshot)
        if self._snapshot_busy():
            raise ValueError('Wait for the current build or ownership calculation to finish.')
        inputs = value['inputs']
        # Block control callbacks while restoring settings. Player data is assigned
        # afterward, so no ownership recalculation or enrichment can change it.
        blockers = [QtCore.QSignalBlocker(widget) for widget in self.findChildren(QtWidgets.QWidget)]
        try:
            self._apply_build_recipe('Snapshot', inputs['recipe'])
        finally:
            del blockers
        self.players = copy.deepcopy(inputs['players'])
        self.portfolio_groups = copy.deepcopy(inputs['rules'].get('groups') or [])
        self.lbl_portfolio_groups.setText(f'Groups: {len(self.portfolio_groups)}')
        self.last_live_check_summary = copy.deepcopy(value.get('freshness') or {})
        self._snapshot_replay = True
        self._snapshot_calibration_data = copy.deepcopy(inputs['calibration'])
        self._snapshot_contest_data = copy.deepcopy(inputs['contest'])
        self.last_showdown = []
        self.last_classic = []
        self.tbl_sd.setRowCount(0)
        self.tbl_cl.setRowCount(0)
        self.last_build_diagnostic = {}
        self.last_portfolio_report = {}
        self.last_sim_report = {}
        self.last_readiness_report = {}
        self.last_final_lock_report = {}
        self.last_build_timing_report = {}
        self.action_copy_build_report.setEnabled(False)
        self._active_build_context = {}
        self._refresh_players_table()
        self._update_workspace_summary()
        self._update_lineup_space_dashboard()
        self._refresh_snapshot_label()

    def on_load_snapshot(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Load Build Snapshot', '', 'Build snapshot (*.json)')
        if not path:
            return
        try:
            self._restore_snapshot(load_snapshot(path))
            self.status.showMessage('Snapshot restored for replay. Automatic refresh is paused.', 8000)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, 'Snapshot Not Loaded', str(exc))

    def _refresh_snapshot_label(self):
        if hasattr(self, 'lbl_snapshot_data'):
            replay = getattr(self, '_snapshot_replay', False)
            self.lbl_snapshot_data.setText(('Snapshot replay' if replay else 'Live inputs') + (' | Candidate library loaded — Deep required' if getattr(self, '_candidate_library', '') else ''))
            self.lbl_snapshot_data.setToolTip(freshness_text(self.last_live_check_summary, replay))

    def on_data_freshness(self):
        QtWidgets.QMessageBox.information(self, 'Data Freshness',
            freshness_text(self.last_live_check_summary, getattr(self, '_snapshot_replay', False))
            + '\nUnknown means the source did not record that information. A recorded check is not proof every source succeeded.'
            + '\nSnapshot replay is for comparison; refresh live data before making current contest decisions.')
