import copy
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from build_snapshots import fingerprint
from scoring_distributions import DistributionCapture,save_distribution,load_distribution
from distribution_validation import compare_distributions,validation_report
from nfl_simulation import simulate_nfl_contest,player_key
from showdown_simulation import simulate_showdown
from learning_db import _normalize_roster_token,init_historical_import_tables
from optimizers import ShowdownOptimizer,MultiSportClassicOptimizer
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


class ScoringDistributionTests(unittest.TestCase):
    def player(self):
        return dict(Name='Example Player',FlexID='p',Team='BUF',Position='RB',NFLDepthOrder=1,
                    GameInfo='BUF@MIA 09/10/2099 08:00PM ET')

    def capture(self,folder,kind='classic',input_id='a'*64):
        c=DistributionCapture([self.player()],kind,10,7)
        for i in range(10):c.record({'p':float(i)})
        report={'player_distributions':c.finish(10)}
        status=save_distribution(report,input_id,folder)
        return next((Path(folder)/'scoring-distributions').glob(input_id+'-*.json')),status

    def test_exact_quantiles_ties_missing_and_partial(self):
        c=DistributionCapture([self.player()],'classic',10,7)
        for _ in range(10):c.record({'p':0.})
        r=c.finish(10)['players'][0]
        self.assertEqual([r[k] for k in ('p10','p50','p90','mean','expected_below_p10','expected_above_p90')],[0.]*6)
        self.assertEqual(c.finish(9)['status'],'not captured')
        c.record({});self.assertEqual(c.finish(10)['status'],'not captured')

    def test_both_simulators_preserve_rankings_and_capture_shared_player_scores(self):
        for kind in ('classic','showdown'):
            players=_fixture_players() if kind=='classic' else _showdown_players()
            candidates=MultiSportClassicOptimizer(players,sport='NFL').build_lineups(2) if kind=='classic' else ShowdownOptimizer(players).build_lineups(2)
            simulate=simulate_nfl_contest if kind=='classic' else simulate_showdown
            baseline=simulate(candidates,players,scenarios=10,field_lineup_count=20)
            captured=simulate(candidates,players,scenarios=10,field_lineup_count=20,capture_distributions=True)
            self.assertEqual([r.sim_metrics for r in baseline['lineups']],[r.sim_metrics for r in captured['lineups']])
            raw=captured['report']['player_distributions'];self.assertEqual(raw['scenarios'],10)
            lookup={r['name']:r['mean'] for r in raw['players']}
            for lu in captured['lineups']:
                roster=list(lu) if kind=='classic' else [lu['Captain']]+lu['Flex']
                total=sum(lookup[_normalize_roster_token(p['Name'])]*(1.5 if kind=='showdown' and i==0 else 1) for i,p in enumerate(roster))
                self.assertAlmostEqual(total,lu.sim_metrics['sim_mean'],places=4)
            self.assertEqual(len(lookup),len(raw['players']))

    def test_persistence_integrity_and_nonfatal_disk_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path,status=self.capture(folder)
            self.assertEqual(status['status'],'saved');self.assertEqual(load_distribution(path)['payload']['scenarios'],10)
            value=json.loads(path.read_text());value['payload']['players'][0]['mean']=99
            path.write_text(json.dumps(value));self.assertRaises(ValueError,load_distribution,path)
            with patch('repeatability.atomic_json',side_effect=OSError('Disk full')):
                c=DistributionCapture([self.player()],'classic',1,1);c.record({'p':0})
                report={'player_distributions':c.finish(1)}
                self.assertEqual(save_distribution(report,'a'*64,folder)['status'],'not saved')
                self.assertNotIn('player_distributions',report)

    def test_game_outcomes_deduplicate_and_conflicts_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder,closing(sqlite3.connect(':memory:')) as conn:
            init_historical_import_tables(conn)
            for key in ('one','two'):
                conn.execute('INSERT INTO historical_imports(import_id,created_at,file_name) VALUES (?,?,?)',(key,'2099',key+'.csv'))
            self.capture(folder)
            for key in ('one','two'):compare_distributions(conn,key,'a'*64,'classic',{'example player':10},folder)
            report='\n'.join(validation_report(conn))
            self.assertIn('1 scheduled games; 1 unique player-game outcomes',report)
            self.assertIn('1 repeated or conflicting comparison rows excluded',report)
            self.assertIn('above 100.0%',report)
            compare_distributions(conn,'two','a'*64,'classic',{'example player':11},folder)
            self.assertIn('1 conflicting player-game outcomes excluded','\n'.join(validation_report(conn)))

    def test_no_historical_fabrication_wrong_snapshot_and_postkickoff_capture(self):
        with tempfile.TemporaryDirectory() as folder,closing(sqlite3.connect(':memory:')) as conn:
            init_historical_import_tables(conn)
            path,_=self.capture(folder)
            self.assertEqual(compare_distributions(conn,'a','b'*64,'classic',{'example player':3},folder)['status'],'unavailable')
            self.assertEqual(compare_distributions(conn,'a','a'*64,'showdown',{'example player':3},folder)['status'],'unavailable')
            value=json.loads(path.read_text());value['payload']['finished_at']='2100-01-01T00:00:00+00:00'
            value['capture_id']=fingerprint(value['payload']);path.write_text(json.dumps(value))
            self.assertEqual(compare_distributions(conn,'a','a'*64,'classic',{'example player':3},folder)['status'],'unavailable')

    def test_normal_workers_save_automatically(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5.QtWidgets import QApplication
        from main_window import LineupBuildWorker
        app=QApplication.instance() or QApplication([])
        for kind,mode in [('classic','Fast'),('classic','Deep'),('showdown','Deep')]:
            players=_fixture_players() if kind=='classic' else _showdown_players()
            raw=simulate_nfl_contest if kind=='classic' else simulate_showdown
            calls=[]
            def small(*args,**kwargs):
                calls.append(kwargs.get('capture_distributions',False));kwargs.update(scenarios=5,field_lineup_count=10)
                return raw(*args,**kwargs)
            target='main_window.simulate_nfl_contest' if kind=='classic' else 'showdown_simulation.simulate_showdown'
            with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'DFS_OPTIMIZER_DATA_DIR':folder}),patch(target,side_effect=small):
                worker=LineupBuildWorker(players,kind=kind,num_lineups=2,salary_cap=50000,sim_enabled=True,sim_scenarios=10,
                    compute_mode=mode,deep_time_limit_seconds=20,deep_options={'candidates':30,'shortlist':8,'field':10,'screening':250},portfolio_rules={'balance_ownership':False,'min_unique':1})
                worker.build_input_id='a'*64;results=[];errors=[]
                worker.finished.connect(results.append);worker.error.connect(errors.append);worker.run()
                self.assertFalse(errors,errors);self.assertTrue(any(calls))
                self.assertEqual(results[0]['sim_report']['distribution_capture']['status'],'saved')
                self.assertEqual(len(list((Path(folder)/'history/scoring-distributions').glob('*.json'))),1)

    def test_results_import_uses_recorded_ranges_both_formats(self):
        from test_results_snapshot_learning import ResultsSnapshotLearningTests
        from learning_db import import_historical_result_csvs,generate_learning_report
        from performance_review import analyze_saved_results
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as folder:
                source,snap,_=ResultsSnapshotLearningTests().fixture(folder,kind)
                players=snap['inputs']['players']
                capture=DistributionCapture(players,kind,10,1)
                for i in range(10):capture.record({player_key(p):float(i) for p in players})
                raw=capture.finish(10)
                # Synthetic fixture times, before this fixture's scheduled kickoff.
                raw.update(started_at='2026-09-10T23:00:00+00:00',finished_at='2026-09-10T23:01:00+00:00')
                save_distribution({'player_distributions':raw},snap['input_id'],folder)
                db=str(Path(folder)/'history.sqlite')
                import_historical_result_csvs([str(source)],username='Example_User',db_path=db,archive_files=False)
                analyze_saved_results(db_path=db,username='Example_User')
                with closing(sqlite3.connect(db)) as conn:
                    payload=json.loads(conn.execute('SELECT payload FROM distribution_validations').fetchone()[0])
                self.assertEqual(payload['status'],'matched')
                self.assertEqual(len(payload['rows']),len(players))
                self.assertTrue(all(row['actual']==10 for row in payload['rows']))
                text=generate_learning_report(db_path=db,username='Example_User')['text']
                self.assertIn('Recorded scoring-distribution validation',text)
                self.assertIn('above 100.0%',text)

    def test_late_capture_does_not_replace_pregame_and_different_games_stay_separate(self):
        with tempfile.TemporaryDirectory() as folder,closing(sqlite3.connect(':memory:')) as conn:
            init_historical_import_tables(conn)
            for key in ('one','two'):
                conn.execute('INSERT INTO historical_imports(import_id,created_at,file_name) VALUES (?,?,?)',(key,'2099',key+'.csv'))
            path,_=self.capture(folder)
            original=load_distribution(path)
            late=copy.deepcopy(original);late['payload']['finished_at']='2100-01-01T00:00:00+00:00'
            late['capture_id']=fingerprint(late['payload'])
            path.with_name('a'*64+'-late.json').write_text(json.dumps(late))
            result=compare_distributions(conn,'one','a'*64,'classic',{'example player':10},folder)
            self.assertEqual(result['rows'][0]['capture_id'],original['capture_id'])
            second=copy.deepcopy(original);second['payload']['input_id']='b'*64
            second['payload']['players'][0]['game']=second['payload']['players'][0]['game'].replace('2099-09-11','2099-09-18')
            second['capture_id']=fingerprint(second['payload'])
            path.with_name('b'*64+'-second.json').write_text(json.dumps(second))
            compare_distributions(conn,'two','b'*64,'classic',{'example player':11},folder)
            text='\n'.join(validation_report(conn))
            self.assertIn('2 scheduled games; 2 unique player-game outcomes',text)
            self.assertIn('0 conflicting player-game outcomes excluded',text)


if __name__=='__main__':unittest.main()
