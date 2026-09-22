import unittest
from build_diagnostics import _aggregate_warning
from showdown_simulation import filter_salary_candidates, salary_floor


class SalaryRecoveryTests(unittest.TestCase):
    def test_success_is_not_a_rule_failure(self):
        for text in [
            'Selection used the SIM-scored compliant set preserved before shortlisting; rules were unchanged.',
            'Bounded feasibility repair completed the portfolio without weakening uniqueness or exposure limits.']:
            self.assertEqual(_aggregate_warning(text),'')
        self.assertIn('not met',_aggregate_warning('Total exposure exceeded'))
        self.assertIn('raised',_aggregate_warning('Automatic Showdown exposure guardrails were relaxed 3 times'))

    def test_salary_thresholds_include_boundary_and_captain_cost(self):
        def row(salary):
            return {'Captain':{'CptSalary':salary-25000,'FlexSalary':1},
                    'Flex':[{'FlexSalary':5000} for _ in range(5)]}
        rows=[row(v) for v in [43200,47400,47500,49400,49500,50000,50100]]
        self.assertEqual(filter_salary_candidates(rows,50000,'Near Cap'),rows[2:6])
        self.assertEqual(filter_salary_candidates(rows,50000,'Maximize Salary'),rows[4:6])
        self.assertEqual(filter_salary_candidates(rows,50000,'Salary Leverage'),rows[:6])
        self.assertEqual(filter_salary_candidates(rows,50000,'Balanced Spend'),rows[:6])
        self.assertEqual(salary_floor(40000,'Near Cap'),37500)

    def test_retained_salary_conflict_is_a_clear_stop(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5.QtWidgets import QApplication
        from main_window import LineupBuildWorker
        from optimizers import ShowdownLineup
        from test_showdown_performance import _showdown_players
        app=QApplication.instance() or QApplication([])
        players=_showdown_players()
        chosen=players[:3]+players[18:21]
        for p in chosen:
            p['FlexSalary']=1000;p['CptSalary']=1500
        retained=ShowdownLineup(chosen[0],chosen[1:])
        worker=LineupBuildWorker(players,kind='showdown',num_lineups=1,salary_cap=50000,
            sim_enabled=True,compute_mode='Deep',retained_lineups=[retained])
        errors=[];worker.error.connect(errors.append);worker.run()
        self.assertEqual(len(errors),1)
        self.assertIn('retained lineup is outside',errors[0])
        self.assertNotIn('Traceback',errors[0])
