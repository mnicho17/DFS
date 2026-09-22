import copy
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import test_projection_sensitivity as fixtures
from portfolio_comparison import compare_portfolios,format_report
from portfolio_rules import select_portfolio
from repeatability import candidates
from ownership_sensitivity import load_sensitivity_bank


class PortfolioComparisonTests(unittest.TestCase):
    def test_both_real_simulators_and_unchanged_bank(self):
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as folder:
                path,_,_=fixtures.ProjectionSensitivityTests().bank(folder,kind)
                before=path.read_bytes()
                report=compare_portfolios(path,requested=2,scenarios=2000)
                self.assertEqual(report['status'],'completed')
                self.assertEqual(len(report['rows']),2)
                self.assertNotEqual(*report['seeds'])
                for row in report['rows']:
                    self.assertEqual(row['selected'],2)
                    self.assertTrue(0<=row['evaluation_coverage']<=100)
                self.assertIn('not historical accuracy',format_report(report))
                self.assertEqual(path.read_bytes(),before)

    def test_incomplete_holdout_rejected_and_selection_precedes_holdout(self):
        with tempfile.TemporaryDirectory() as folder:
            path,_,_=fixtures.ProjectionSensitivityTests().bank(folder,'classic')
            calls=[]
            def sim(rows,players,**kw):
                calls.append(kw['seed'])
                for lu in rows:
                    lu.sim_metrics.update(sim_scenarios=2000,sim_top_one_pct=1)
                    lu.sim_top_hits={1,2}
                return {'lineups':rows,'report':{'scenarios':2000 if len(calls)==1 else 1999}}
            with patch('portfolio_comparison.select_portfolio',wraps=select_portfolio) as selector:
                with self.assertRaisesRegex(ValueError,'Incomplete simulation'):
                    compare_portfolios(path,2,simulate=sim)
                self.assertEqual(selector.call_count,2)
            with self.assertRaisesRegex(ValueError,'Cancelled'):
                compare_portfolios(path,2,cancelled=lambda:True)

    def test_default_and_individual_unchanged_constraints_preserved(self):
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as folder:
                path,_,_=fixtures.ProjectionSensitivityTests().bank(folder,kind)
                rows=candidates(load_sensitivity_bank(path)['payload']);before=copy.deepcopy(rows)
                a=select_portfolio(rows,2,kind=kind)
                b=select_portfolio(rows,2,kind=kind,core_penalty=0)
                self.assertEqual(a,b)
                a=select_portfolio(rows,2,kind=kind,individual_ranking=True)
                b=select_portfolio(rows,2,kind=kind,individual_ranking=True,core_penalty=6)
                self.assertEqual(a,b)
                r=select_portfolio(rows,2,kind=kind,core_penalty=6,retained_lineups=[rows[0]],refinement_passes=2)
                self.assertTrue(any(lu is rows[0] for lu in r['lineups']))
                self.assertEqual(rows,before)
                with self.assertRaises(ValueError):select_portfolio(rows,2,core_penalty=float('nan'))

    def test_dialog_and_worker_report_files(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5 import QtWidgets
        from portfolio_comparison_ui import PortfolioComparisonDialog,ComparisonWorker
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            history=str(Path(folder)/'build-diagnostics.json')
            with patch('portfolio_comparison_ui.build_history_path',return_value=history),patch('repeatability_ui.build_history_path',return_value=history):
                d=PortfolioComparisonDialog()
                self.assertEqual(d.batches.value(),150)
                self.assertEqual(d.scenarios.currentData(),2000)
                self.assertFalse(d.start.isEnabled())
                d.close()
                path,_,_=fixtures.ProjectionSensitivityTests().bank(folder,'classic')
                r=compare_portfolios(path,2)
                with patch('portfolio_comparison_ui.compare_portfolios',return_value=r):
                    w=ComparisonWorker(path,2,2000);texts=[];w.finished.connect(texts.append);w.run()
                self.assertIn('DFS Portfolio Comparison',texts[0])
                self.assertEqual(len(list((Path(folder)/'portfolio-checks').glob('*.json'))),1)


if __name__=='__main__':unittest.main()
