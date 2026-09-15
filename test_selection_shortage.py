import os
import unittest
from unittest.mock import Mock, patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from main_window import LineupBuildWorker, MainWindow
from portfolio_rules import select_portfolio
from selection_shortage import PortfolioSelectionShortage, TITLE


class SelectionShortageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_explicit_cap_is_explained_without_exposing_partial_output(self):
        a = dict(Name='Example', FlexID='a', Team='A', MaxPct=50)
        b = dict(Name='Other', FlexID='b', Team='B')
        c = dict(Name='Third', FlexID='c', Team='C')
        with self.assertRaises(PortfolioSelectionShortage) as caught:
            select_portfolio([[a,b], [a,c]], 2, allow_relaxation=False)
        self.assertIn('Example: total cap 1/2 (explicit)', str(caught.exception))
        self.assertIn('no partial portfolio was released', str(caught.exception))

    def test_worker_expected_stop_has_no_traceback_but_unexpected_errors_do(self):
        for error, trace in [(PortfolioSelectionShortage(TITLE+'\nDetails'), False), (RuntimeError('unexpected'), True)]:
            worker = LineupBuildWorker([], kind='showdown', num_lineups=150,
                salary_cap=50000, sim_enabled=True, compute_mode='Deep')
            messages=[]; finished=[]
            worker.error.connect(messages.append);worker.finished.connect(finished.append)
            with patch('showdown_simulation.run_deep_showdown', side_effect=error):
                worker.run()
            self.assertEqual(len(messages),1)
            self.assertEqual('Traceback' in messages[0],trace)
            self.assertFalse(finished)

    def test_expected_stop_uses_warning_and_cleans_up_build_ui(self):
        window=Mock()
        with patch('main_window.show_build_error') as warning:
            MainWindow._on_lineup_build_error(window, TITLE+'\nDetails')
        warning.assert_called_once_with(window, 'Lineup limits', TITLE+'\nDetails', warning=True)
        window._finish_lineup_build_ui.assert_called_once()
        self.assertEqual(window._active_build_context,{})


if __name__=='__main__':unittest.main()
