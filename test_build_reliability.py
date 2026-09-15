import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import requests
from nfl_usage_cache import fetch_cached,cache_path
from portfolio_rules import select_portfolio
from nfl_simulation import SimLineup
from optimizers import ShowdownLineup
from captain_coverage import coverage_report
from ownership_strategy import fit_field
from showdown_field import OpponentField,sample_field
from test_showdown_performance import _showdown_players


class BuildReliabilityTests(unittest.TestCase):
    def test_retry_cached_fallback_season_integrity_and_timestamp(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}):
            rows=[dict(season='2025',week='1',player_name='Example')]
            transient=requests.HTTPError('504',response=Mock(status_code=504))
            get=Mock(side_effect=[transient,rows])
            fresh=fetch_cached(2025,'source',get)
            self.assertEqual(get.call_count,2);self.assertEqual(fresh.state,'fresh')
            failed=Mock(side_effect=requests.Timeout('Timed out'))
            cached=fetch_cached(2025,'source',failed)
            self.assertEqual(cached,rows);self.assertEqual(cached.state,'cached')
            self.assertEqual(cached.fetched_at,fresh.fetched_at)
            self.assertEqual(fetch_cached(2026,'source',failed),[])
            value=json.loads(cache_path(2025).read_text());value['payload']['rows'][0]['week']='9'
            cache_path(2025).write_text(json.dumps(value))
            self.assertEqual(fetch_cached(2025,'source',failed),[])

    def test_cache_note_and_player_provenance_survive_enrichment(self):
        from nfl_auto_data import apply_auto_nfl_context
        from nfl_usage_cache import UsageRows
        from build_snapshots import freshness_text
        rows=UsageRows([dict(season='2025',week='1',player_name='Example',player_id='p',recent_team='KC',position='WR',targets='8')],state='cached',fetched_at='2026-01-01T00:00:00+00:00')
        players=[dict(Name='Example',Team='KC',Position='WR',FlexSalary=5000,HistoricalPPG=8,GameInfo='KC@DEN 09/14/2026 08:00PM ET')]
        result=apply_auto_nfl_context(players,fetch_external=False,sleeper_data={},usage_rows=rows,usage_season=2025,weather_by_game={})
        self.assertEqual(players[0]['NFLUsageFetchState'],'cached')
        self.assertIn('not freshly downloaded',freshness_text(result))
        self.assertEqual(players[0]['NFLUsageFetchedAt'],rows.fetched_at)

    def test_strict_repair_escapes_greedy_dead_end_without_changing_caps(self):
        a=dict(FlexID='a',Team='A',MaxPct=50,FlexProjection=20)
        b=dict(FlexID='b',Team='B',MaxPct=50,FlexProjection=20)
        c=dict(FlexID='c',Team='C',FlexProjection=1)
        d=dict(FlexID='d',Team='D',FlexProjection=1)
        rows=[SimLineup(roster,metrics=dict(sim_scenarios=10,sim_top_one_pct=rate)) for roster,rate in [([a,b],90),([a,c],30),([b,d],20)]]
        result=select_portfolio(rows,2,individual_ranking=True,allow_relaxation=False)
        self.assertTrue(result['report']['feasibility_repaired'])
        self.assertEqual({tuple(p['FlexID'] for p in lu) for lu in result['lineups']},{('a','c'),('b','d')})
        self.assertEqual(result['report']['effective_min_unique'],1)
        with self.assertRaisesRegex(ValueError,'No limits were relaxed'):
            select_portfolio(rows[:2],2,individual_ranking=True,allow_relaxation=False)

    def test_strict_uniqueness_shortage_is_not_silently_relaxed(self):
        players=[dict(FlexID=str(i),Team='T',FlexProjection=10) for i in range(4)]
        with self.assertRaisesRegex(ValueError,'No limits were relaxed'):
            select_portfolio([players[:3],players[:2]+players[3:]],2,rules={'min_unique':2},allow_relaxation=False)

    def test_all_captains_are_reported_including_unreserved_selection(self):
        players=_showdown_players();a=ShowdownLineup(players[0],[players[i] for i in [1,2,18,19,20]])
        b=ShowdownLineup(players[6],[players[i] for i in [1,2,18,19,20]])
        for lu in (a,b):lu.sim_metrics=dict(sim_top_one_pct=1.,sim_win_rate=.1)
        report=coverage_report([players[0]],[a,b],[a,b],[a,b],[b],validation_complete=True,seeded=1,reserved=1,library=False)
        self.assertEqual(report['eligible_count'],1);self.assertEqual(len(report['rows']),2)
        ordinary=next(r for r in report['rows'] if not r['reservation_eligible'])
        self.assertEqual(ordinary['selected'],1)
        self.assertEqual(sum(r['selected'] for r in report['rows']),1)

    def test_salary_targets_are_met_when_feasible(self):
        field=sample_field(_showdown_players(),400,seed=19)
        self.assertEqual(len(field),400)
        self.assertEqual(field.diagnostic['salary_band_counts'],field.diagnostic['salary_band_targets'])
        self.assertEqual(field.diagnostic['fallback_entries'],0)

    def test_ownership_fit_cannot_purchase_accuracy_by_worsening_salary_mix(self):
        players=[dict(FlexID=str(i),Name=str(i),OwnershipUnits='percent_of_entries',ProjCptOwnPct=100 if i==1 else 0,ProjFlexOwnPct=0 if i==1 else 100) for i in range(6)]
        initial=OpponentField([ShowdownLineup(players[0],players[1:])]*2)
        trial=OpponentField([ShowdownLineup(players[1],[players[0]]+players[2:])]*2)
        initial.diagnostic=dict(salary_band_targets={'high':2},salary_band_counts={'high':2})
        trial.diagnostic=dict(salary_band_targets={'high':2},salary_band_counts={'low':2})
        fitted=fit_field(initial,players,lambda *args:trial,showdown=True)
        self.assertEqual(fitted.diagnostic,initial.diagnostic)
        self.assertEqual(fitted.ownership_fit['before_mae_pp'],fitted.ownership_fit['after_mae_pp'])


if __name__=='__main__':unittest.main()
