from test_environment import install
install()
import copy
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtWidgets,QtCore
from main_window import MainWindow,LineupBuildWorker,ContestProfileDialog
from qb_coverage_ui import QBCoverageDialog
from qb_coverage import slate_identity
from test_qb_coverage import fixtures
from test_contest_strategy import profile
from test_showdown_performance import _showdown_players
from test_nfl_logic import _fixture_players
from contest_strategy import attach_strategy
from optimizers import ShowdownOptimizer


class ContestCoverageUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_dialog_apply_scoped_targets_and_disable_without_mutating_pool(self):
        rows,players,config = fixtures()
        window = MainWindow()
        try:
            window.players = copy.deepcopy(players)
            window.tabs_lineups.setCurrentIndex(0)
            window.last_showdown = rows
            before = copy.deepcopy(window.players)
            dialog = QBCoverageDialog(window)
            dialog.qbs[0].setCurrentIndex(dialog.qbs[0].findData('A'))
            dialog.qbs[1].setCurrentIndex(dialog.qbs[1].findData('B'))
            dialog.enabled.setChecked(True)
            dialog.targets['fade_a'].setValue(2)
            dialog.save()
            self.assertEqual(window._portfolio_rules()['qb_coverage']['fade_a'],2)
            self.assertEqual(window._qb_coverage['slate_id'],slate_identity(players))
            self.assertEqual(window.players,before)
            window.tabs_lineups.setCurrentIndex(1)
            self.assertNotIn('qb_coverage',window._portfolio_rules())
            window.tabs_lineups.setCurrentIndex(0)
            dialog = QBCoverageDialog(window)
            dialog.enabled.setChecked(False)
            dialog.save()
            self.assertNotIn('qb_coverage',window._portfolio_rules())
        finally:
            window.close()

    def test_real_point_worker_finishes_and_close_cancels_safely(self):
        rows,players,config = fixtures()
        window = MainWindow()
        try:
            window.players = players
            window.last_showdown = rows
            window.tabs_lineups.setCurrentIndex(0)
            window._qb_coverage = config
            dialog = QBCoverageDialog(window)
            dialog.start_review()
            self.assertFalse(dialog.review.isEnabled())
            self.assertFalse(dialog.qbs[0].isEnabled())
            deadline = time.monotonic()+10
            while dialog.thread.isRunning() and time.monotonic()<deadline:
                self.app.processEvents()
                time.sleep(.002)
            self.app.processEvents()
            self.assertFalse(dialog.thread.isRunning())
            self.assertIn('QB A exits',dialog.output.toPlainText())
            self.assertIn('Not a paid-finish',dialog.output.toPlainText())
            dialog.start_review()
            dialog.reject()
            self.assertTrue(dialog.worker.cancel.is_set())
            while dialog.thread.isRunning() and time.monotonic()<deadline:
                self.app.processEvents()
                time.sleep(.002)
            self.app.processEvents()
            self.assertFalse(dialog.thread.isRunning())
            dialog.reject()
        finally:
            window.close()

    def test_paid_profit_output_columns_refresh_sort_and_clear(self):
        window = MainWindow()
        try:
            rows = ShowdownOptimizer(_showdown_players()).build_lineups(3)
            for i,row in enumerate(rows):
                row.sim_metrics = attach_strategy(dict(sim_scenarios=100,sim_cash_rate=90-i*20,sim_expected_profit=i*3,sim_mean=100+i),profile())
            window._populate_showdown_lineups(rows)
            table = window.tbl_sd
            labels = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
            self.assertEqual(labels.count('Paid %'),1)
            window._sort_result_column('showdown',labels.index('Profit $'))
            self.assertIs(window.last_showdown[0],rows[-1])
            window._reset_result_sort('showdown')
            self.assertIs(window.last_showdown[0],rows[0])
            for row in rows: row.sim_metrics = dict(sim_scenarios=100,sim_mean=100)
            window._populate_showdown_lineups(rows)
            labels = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
            self.assertNotIn('Paid %',labels)
            self.assertNotIn('Profit $',labels)
            window._sort_result_column('showdown',labels.index('Top 1%'))
            for i,row in enumerate(rows):
                row.sim_metrics = attach_strategy(dict(sim_scenarios=100,sim_cash_rate=90-i*20,sim_expected_profit=i*3,sim_mean=100),profile())
            window._populate_showdown_lineups(rows)
            labels = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
            window._sort_result_column('showdown',labels.index('Paid %'))
            self.assertIs(window.last_showdown[0],rows[0])
        finally:
            window.close()

    def test_real_classic_and_showdown_cash_workers_record_executed_strategy(self):
        for kind,players,mode in (('classic',_fixture_players(),'Fast'),('showdown',_showdown_players(),'Deep')):
            worker = LineupBuildWorker(players,kind=kind,num_lineups=1,salary_cap=50000,
                contest_profile=profile(),sim_enabled=True,sim_scenarios=250,compute_mode=mode,
                salary_strategy='Balanced Spend',deep_time_limit_seconds=300,
                deep_options=dict(candidates=40,shortlist=20,field=80,screening=250))
            finished,errors = [],[]
            worker.finished.connect(finished.append)
            worker.error.connect(lambda *args: errors.append(args))
            worker.run()
            self.assertFalse(errors,errors)
            self.assertEqual(len(finished[0]['lineups']),1)
            self.assertIn('Double-Up: paid-finish rate',finished[0]['sim_report']['selection_strategy'])
            self.assertEqual(finished[0]['lineups'][0].sim_metrics['sim_selection_objective'],'DOUBLE_UP')

    def test_stale_slate_does_not_apply_coverage(self):
        rows,players,config = fixtures()
        config.update(kind='showdown',slate_id='old-slate')
        worker = LineupBuildWorker(players,kind='showdown',num_lineups=3,salary_cap=50000,portfolio_rules={'qb_coverage':config})
        errors,finished = [],[]
        worker.error.connect(lambda *args:errors.append(args))
        worker.finished.connect(finished.append)
        worker.run()
        self.assertFalse(finished)
        self.assertIn('different slate',str(errors))

    def test_real_deep_showdown_fade_generation_and_selection(self):
        from portfolio_rules import player_key
        players = _showdown_players()
        qbs = [p for p in players if p['Position']=='QB']
        config = dict(enabled=True,qb_a=player_key(qbs[0]),qb_b=player_key(qbs[1]),fade_a=1,fade_b=1,neither=1)
        worker = LineupBuildWorker(players,kind='showdown',num_lineups=4,salary_cap=50000,
                portfolio_rules={'qb_coverage':config},sim_enabled=True,sim_scenarios=250,
                compute_mode='Deep',salary_strategy='Balanced Spend',deep_time_limit_seconds=300,
                deep_options=dict(candidates=150,shortlist=80,field=60,screening=250))
        finished,errors = [],[]
        worker.finished.connect(finished.append)
        worker.error.connect(lambda *args:errors.append(args))
        worker.run()
        self.assertFalse(errors,errors)
        self.assertEqual(len(finished[0]['lineups']),4)
        report = finished[0]['portfolio_report']['qb_coverage']
        self.assertTrue(all(not row['shortage'] for row in report['targets'].values()))
        self.assertGreaterEqual(report['counts']['neither'],1)
        self.assertFalse(any(p.get('FadeCpt') or p.get('FadeFlex') for lu in finished[0]['lineups'] for p in [lu['Captain']]+lu['Flex']))
