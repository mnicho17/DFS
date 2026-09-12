import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from projection_sensitivity import OutcomeStress,PROFILES,prepare_targets,run_projection,save_projection,format_projection
from repeatability import save_bank,identity,candidates
from ownership_sensitivity import load_sensitivity_bank
from ownership_review import load_review,PROJECTION_COLUMNS
from test_ownership_sensitivity import fixture
from nfl_simulation import player_key

class ProjectionSensitivityTests(unittest.TestCase):
    def bank(self,folder,kind):
        from nfl_simulation import generate_nfl_field_lineups,SimLineup
        from showdown_simulation import generate_showdown_field
        p=fixture(kind)
        for v in p:v['NFLUsageGames']=2
        raw=generate_nfl_field_lineups(p,3,seed=7)[0] if kind=='classic' else generate_showdown_field(p,3,seed=7)
        if kind=='classic':raw=[SimLineup(lu) for lu in raw]
        for i,lu in enumerate(raw):lu.sim_metrics=dict(sim_scenarios=1000,sim_top_one_pct=10-i,sim_mean=100)
        info=save_bank(raw,p,kind=kind,salary_cap=50000,field_count=20,folder=folder,input_id='test-input')
        return Path(folder)/(info['bank_id']+'.dfsbank'),p,raw

    def test_explicit_stresses_shared_player_outcomes_and_mean_preserving_noise(self):
        source={'a':20.,'b':10.,'dst':-2.}; targets=[{'key':'a'}]
        lower=OutcomeStress(PROFILES[1],targets,1)
        self.assertEqual(lower(source),{'a':17.,'b':10.,'dst':-2.});self.assertEqual(source['a'],20.)
        wide=OutcomeStress(PROFILES[2],targets,1); values=[wide(source)['a'] for _ in range(10000)]
        self.assertEqual(set(values),{15.,25.});self.assertAlmostEqual(sum(values)/len(values),20,delta=.15)
        repeat=OutcomeStress(PROFILES[2],targets,1)
        self.assertEqual(values[:20],[repeat(source)['a'] for _ in range(20)])
        from showdown_simulation import showdown_score
        from optimizers import ShowdownLineup
        a={'FlexID':'a'};b={'FlexID':'b'};d={'FlexID':'dst'}
        self.assertEqual(showdown_score(ShowdownLineup(a,[b,d]),lower(source)),1.5*17+10-2)

    def test_missing_history_is_not_rookie_and_specialists_not_targeted(self):
        with tempfile.TemporaryDirectory() as f:
            path,p,_=self.bank(f,'classic');payload=load_sensitivity_bank(path)['payload']
            for v in payload['players']:v.update(NFLUsageGames=16,NFLUsageSource='prior_season')
            skills=[v for v in payload['players'] if v['Position'] in ('QB','RB','WR','TE')]
            skills[0]['NFLUsageGames']=None;skills[1]['NFLUsageGames']=0;skills[2]['Rookie']=True
            targets=prepare_targets(payload)
            wider={v['key']:v['reason'] for v in targets[PROFILES[2]]}
            self.assertIn('missing',wider[player_key(skills[0])]);self.assertIn('Fewer',wider[player_key(skills[1])])
            self.assertEqual(wider[player_key(skills[2])],'Explicit rookie flag')
            self.assertEqual(len(targets[PROFILES[1]]),5)
            self.assertFalse({player_key(v) for v in payload['players'] if v['Position']=='DST'} & set(wider))

    def test_real_classic_showdown_fixed_fields_and_outcomes_and_saved_review(self):
        from nfl_simulation import simulate_nfl_contest
        from showdown_simulation import simulate_showdown
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as f:
                path,p,raw=self.bank(f,kind);original=path.read_bytes();before=copy.deepcopy(p)
                sim=simulate_nfl_contest if kind=='classic' else simulate_showdown
                ordinary=sim(raw,p,scenarios=1000,field_lineup_count=20,seed=1200007)
                report=run_projection(path,batches=1,scenarios=1000)
                self.assertEqual(report['status'],'completed');self.assertEqual(path.read_bytes(),original);self.assertEqual(p,before)
                proof=report['evidence'][0]
                self.assertEqual(len({v['base_outcome_id'] for v in proof.values()}),1)
                self.assertEqual(len({v['opponent_field_id'] for v in proof.values()}),1)
                normal={identity(lu,kind):lu.sim_metrics for lu in ordinary['lineups']}
                reference=candidates(load_sensitivity_bank(path)['payload'])
                for row in (x for x in report['rows'] if x['profile']==PROFILES[0]):
                    self.assertEqual(row['mean_points'],normal[identity(reference[row['saved_rank']-1],kind)]['sim_mean'])
                for v in proof[PROFILES[1]]['player_means'].values():self.assertAlmostEqual(v['stressed'],.85*v['baseline'])
                dest=Path(f)/'report.json';save_projection(dest,report)
                review=load_review(dest,f,comparison='projection');self.assertEqual(len(review['rows']),3)
                with self.assertRaisesRegex(ValueError,'type'):load_review(dest,f)
                self.assertIn('Missing history does not establish rookie',format_projection(report))
                self.assertTrue(dest.with_suffix('.csv').exists())

    def test_pair_guards_and_incomplete_batches(self):
        with tempfile.TemporaryDirectory() as f:
            path,_,_=self.bank(f,'classic')
            for changed in ('field','outcomes','identities','partial'):
                calls=[]
                def sim(rows,players,**kw):
                    calls.append(1);n=len(calls)
                    transform=kw['outcome_transform']
                    for _ in range(1000):transform({'a':float(n if changed=='outcomes' else 1)})
                    if changed=='identities' and n==2:rows=rows[:-1]
                    return dict(lineups=rows,report=dict(scenarios=999 if changed=='partial' and n==5 else 1000,
                        sensitivity_field_id=str(n if changed=='field' else 1)))
                if changed=='partial':
                    r=run_projection(path,batches=2,scenarios=1000,simulate=sim)
                    self.assertEqual(r['completed_batches'],1);self.assertEqual(r['status'],'incomplete')
                else:
                    with self.assertRaises(ValueError):run_projection(path,batches=1,scenarios=1000,simulate=sim)
            r=run_projection(path,batches=1,scenarios=1000,cancelled=lambda:True)
            self.assertEqual(r['completed_batches'],0)

    def test_projection_and_ownership_columns_coexist_and_sort_both_formats(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5 import QtCore,QtWidgets
        from main_window import MainWindow
        from test_ownership_review import OwnershipReviewTests
        from ownership_review import COLUMNS
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as f:
            settings=QtCore.QSettings(f+'/test.ini',QtCore.QSettings.IniFormat)
            with patch('main_window.QtCore.QSettings',return_value=settings):w=MainWindow()
            try:
                for kind in ('classic','showdown'):
                    path,lineups,r=OwnershipReviewTests().fixture(f,kind,160)
                    own=load_review(path,f);own['lookup']={v['key']:v for v in own['rows']}
                    setattr(w,'_'+kind+'_ownership_review',own)
                    old_profiles=list(dict.fromkeys(v['profile'] for v in r['rows']))
                    for v in r['rows']:v['profile']=PROFILES[old_profiles.index(v['profile'])]
                    r['comparison_type']='projection';path.write_text(json.dumps(r))
                    projection=load_review(path,f,comparison='projection');projection['lookup']={v['key']:v for v in projection['rows']}
                    setattr(w,'_'+kind+'_projection_review',projection)
                    if kind=='classic':w._populate_classic_lineups(lineups,'NFL')
                    else:w._populate_showdown_lineups(lineups)
                    table=w.tbl_cl if kind=='classic' else w.tbl_sd
                    count=table.columnCount();w._finish_result_page(kind,lineups[:150]);self.assertEqual(table.columnCount(),count)
                    labels=[table.horizontalHeaderItem(i).text() for i in range(count)]
                    self.assertTrue(all(v in labels for v in COLUMNS+PROJECTION_COLUMNS))
                    col=labels.index('Proj lowest %');w._sort_result_column(kind,col);w._sort_result_column(kind,col)
                    current=w.last_classic if kind=='classic' else w.last_showdown;self.assertIs(current[0],lineups[-1])
                    table.cellWidget(0,0).setChecked(True)
                    saved=w.saved_classic if kind=='classic' else w.saved_showdown;self.assertIs(saved[0],lineups[-1])
                    getattr(w,'_'+kind+'_pages')[0].setCurrentIndex(1);self.assertEqual(table.rowCount(),10)
                    w._reset_result_sort(kind);self.assertIs((w.last_classic if kind=='classic' else w.last_showdown)[0],lineups[0])
            finally:w.close()
