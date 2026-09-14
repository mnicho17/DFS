import copy
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from captain_coverage import captain_targets,seed_captains,shortlist_reservations,coverage_report,format_coverage
from nfl_simulation import player_key
from showdown_simulation import active_showdown_players,showdown_signature,validate_showdown_lineup,simulate_showdown
from optimizers import ShowdownLineup,ShowdownOptimizer
from test_showdown_performance import _showdown_players


class CaptainCoverageTests(unittest.TestCase):
    def players(self):
        players=_showdown_players()
        players[5].update(Name='Lower mean starter',Position='TE',NFLDepthOrder=1,FlexProjection=5.46,
            CptProjection=8.19,OwnershipUnits='percent_of_entries',ProjCptOwnPct=.8)
        return active_showdown_players(players)

    def test_eligibility_preserves_exclusions_and_unknown_roles(self):
        players=self.players();low=players[5]
        self.assertIn(player_key(low),{player_key(p) for p in captain_targets(players)})
        for field,value in [('FadeCpt',True),('LockFlex',True),('MaxPct',0),('MaxCptPct',0),('NFLAvailability','OUT'),('FlexProjection',0)]:
            changed=copy.deepcopy(players);changed[5][field]=value
            self.assertNotIn(player_key(low),{player_key(p) for p in captain_targets(changed)},field)
        changed=copy.deepcopy(players);changed[5].update(Position='QB',NFLDepthOrder=2)
        self.assertNotIn(player_key(low),{player_key(p) for p in captain_targets(changed)})
        changed=copy.deepcopy(players);changed[5]['NFLDepthOrder']=None
        self.assertNotIn(player_key(low),{player_key(p) for p in captain_targets(changed)})
        changed[5]['ProjectionSource']='Manual override'
        self.assertIn(player_key(low),{player_key(p) for p in captain_targets(changed)})
        players[0]['LockCpt']=True
        self.assertEqual([player_key(p) for p in captain_targets(players)],[player_key(players[0])])

    def test_seeded_rosters_are_legal_and_temporary_locks_do_not_escape(self):
        players=self.players();players[2]['LockFlex']=True;players[3]['FadeFlex']=True
        original=copy.deepcopy(players);bank={};target=players[5]
        count=seed_captains(players,[target],bank,set(),120,salary_cap=50000,own_mode='Balanced',own_weight=.15,
            deadline=time.perf_counter()+5,cancelled=lambda:False)
        self.assertGreater(count,0);self.assertLessEqual(count,12);self.assertEqual(players,original)
        for lu in bank.values():
            validate_showdown_lineup(lu,players,50000)
            self.assertEqual(player_key(lu['Captain']),player_key(target))
            self.assertFalse(lu['Captain'].get('LockCpt'))
            self.assertIn(player_key(players[2]),[player_key(p) for p in lu['Flex']])
            self.assertNotIn(player_key(players[3]),[player_key(p) for p in lu['Flex']])
        count=seed_captains(players,[target],{},set(),120,salary_cap=50000,own_mode='Balanced',own_weight=.15,
            deadline=time.perf_counter()+5,cancelled=lambda:True)
        self.assertEqual(count,0)

    def test_screening_retains_low_ranked_captain_within_capacity_both_modes(self):
        from main_window import _deep_shortlist
        players=self.players();target=players[5]
        regular=ShowdownOptimizer(players).build_lineups(25)
        bank={};seed_captains(players,[target],bank,set(),120,salary_cap=50000,own_mode='Balanced',own_weight=.15,
            deadline=time.perf_counter()+5,cancelled=lambda:False)
        for lu in regular:lu.sim_metrics={'sim_scenarios':1000,'sim_top_one_pct':80.,'sim_win_rate':1.}
        for lu in bank.values():lu.sim_metrics={'sim_scenarios':1000,'sim_top_one_pct':.01,'sim_win_rate':.001}
        all_rows=regular+list(bank.values());retained={showdown_signature(regular[0])}
        reserved,added=shortlist_reservations(all_rows,[target],20,retained)
        self.assertLessEqual(added,4)
        for individual in (False,True):
            short=_deep_shortlist(all_rows,20,reserved_signatures=reserved,individual_ranking=individual)
            self.assertEqual(len(short),20)
            self.assertIn(next(iter(retained)),{showdown_signature(lu) for lu in short})
            self.assertGreaterEqual(sum(player_key(lu['Captain'])==player_key(target) for lu in short),3)
        reserved,added=shortlist_reservations(all_rows,[target],1,retained)
        self.assertEqual(reserved,retained);self.assertEqual(added,0)

    def test_report_distinguishes_incomplete_and_fully_evaluated_not_selected(self):
        players=self.players();target=players[5]
        lu=ShowdownLineup(target,[players[i] for i in [0,1,18,19,20]])
        lu.sim_metrics={'sim_scenarios':10,'sim_top_one_pct':1.,'sim_win_rate':.2}
        args=dict(targets=[target],generated=[lu],shortlisted=[lu],validated=[lu],selected=[],seeded=1,reserved=1,library=False)
        p=coverage_report(**args,validation_complete=False)
        self.assertEqual(p['rows'][0]['validated'],0);self.assertIsNone(p['rows'][0]['best_rank'])
        p=coverage_report(**args,validation_complete=True)
        self.assertEqual(p['rows'][0]['best_rank'],1)
        self.assertIn('Fully evaluated; not selected','\n'.join(format_coverage(p)))
        self.assertEqual(json.loads(json.dumps(p)),p)

    def test_worker_carries_coverage_to_diagnostic_without_forcing_selection(self):
        from PyQt5.QtWidgets import QApplication
        from main_window import LineupBuildWorker
        from build_diagnostics import create_build_diagnostic,format_build_report
        app=QApplication.instance() or QApplication([])
        players=self.players();original=copy.deepcopy(players)
        def small(*args,**kwargs):
            kwargs.update(scenarios=12,field_lineup_count=20)
            result=simulate_showdown(*args,**kwargs)
            # Keep this intentionally partial, so the report cannot claim full validation.
            return result
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}),patch('showdown_simulation.simulate_showdown',side_effect=small):
            worker=LineupBuildWorker(players,kind='showdown',num_lineups=2,salary_cap=50000,sim_enabled=True,
                compute_mode='Deep',sim_scenarios=2500,deep_time_limit_seconds=20,
                deep_options={'candidates':120,'shortlist':30,'field':20,'screening':250},portfolio_rules={'balance_ownership':False,'min_unique':1})
            results=[];errors=[];worker.finished.connect(results.append);worker.error.connect(errors.append);worker.run()
            self.assertFalse(errors,errors);r=results[0];self.assertEqual(players,original)
            record=create_build_diagnostic(context={'sport':'NFL','kind':'showdown','settings':{}},
                timing_report=r['timing_report'],portfolio_report=r['portfolio_report'],sim_report=r['sim_report'],lineups=r['lineups'])
            self.assertIn('Showdown Captain coverage',format_build_report(record))
            self.assertFalse(record['captain_coverage']['validation_complete'])
            self.assertLessEqual(len(r['lineups']),2)
            self.assertTrue(all(not p.get('LockCpt') for lu in r['lineups'] for p in [lu['Captain']]+lu['Flex']))


if __name__=='__main__':unittest.main()
