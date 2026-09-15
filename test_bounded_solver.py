import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from bounded_solver import wait_for_process, solve
from portfolio_feasibility import conflict_index
from feasible_shortlist import preserve
from main_window import _deep_shortlist, _lineup_signature
import test_feasible_shortlist as fixtures


class BoundedSolverTests(unittest.TestCase):
    def test_unresponsive_child_is_killed_on_deadline_and_cancel(self):
        for cancelled, allowance in [(lambda: False, .15), (lambda: True, 30)]:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
            with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], **options) as child:
                start=time.perf_counter()
                with self.assertRaises(TimeoutError):
                    wait_for_process(child, start+allowance, cancelled)
                self.assertIsNotNone(child.poll())
                self.assertLess(time.perf_counter()-start, 3)

    def test_real_cbc_solution_remains_usable(self):
        import pulp
        problem=pulp.LpProblem('bounded_test',pulp.LpMaximize)
        a=pulp.LpVariable('a',cat='Binary');b=pulp.LpVariable('b',cat='Binary')
        problem += 2*a+b
        problem += a+b==1
        self.assertTrue(solve(problem,time.perf_counter()+5,lambda:False))
        self.assertEqual((a.value(),b.value()),(1,0))

    def test_preparation_can_be_cancelled_before_solver(self):
        calls=[]
        def stop():
            calls.append(1)
            if len(calls)>4:raise TimeoutError('cancelled')
        with self.assertRaises(TimeoutError):
            conflict_index([set(range(i,i+7)) for i in range(1000)],lambda r:r,2,stop)
        self.assertLess(len(calls),10)

    def test_budget_exhaustion_continues_with_ordinary_shortlist(self):
        rows=fixtures.FeasibleShortlistTests().rows()
        with patch('portfolio_rules.select_portfolio',side_effect=TimeoutError('budget')):
            short,witness,report=preserve(rows,_deep_shortlist,2,2,kind='classic',rules={},retained=[],
                reserved=[],signature=_lineup_signature,individual_ranking=True,
                deadline=time.perf_counter()+20,cancelled=lambda:False)
        self.assertEqual(len(short),2)
        self.assertFalse(witness)
        self.assertEqual(report['status'],'not found within budget')
