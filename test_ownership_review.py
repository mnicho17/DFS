import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtCore,QtWidgets
from ownership_review import load_review,roster_key,COLUMNS
from repeatability import save_bank,model_version,candidates
from ownership_sensitivity import PROFILES,load_sensitivity_bank
from nfl_simulation import SimLineup
from optimizers import ShowdownLineup,MultiSportClassicOptimizer,ShowdownOptimizer
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players

class OwnershipReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def fixture(self,folder,kind,count=3):
        base=MultiSportClassicOptimizer(_fixture_players(),sport='NFL').build_lineups(1)[0] if kind=='classic' else ShowdownOptimizer(_showdown_players()).build_lineups(1)[0]
        lineups=[]
        for i in range(count):
            lu=SimLineup(copy.deepcopy(base)) if kind=='classic' else ShowdownLineup(copy.deepcopy(base['Captain']),copy.deepcopy(base['Flex']))
            (lu[0] if kind=='classic' else lu['Captain'])['FlexID']='review-'+str(i)
            (lu[0] if kind=='classic' else lu['Captain'])['FlexNamePlusID']='Review ('+str(i)+')'
            lu.sim_metrics=dict(sim_scenarios=1000,sim_top_one_pct=count-i,sim_mean=100)
            lineups.append(lu)
        info=save_bank(lineups,[],kind=kind,salary_cap=50000,field_count=20,folder=folder,input_id='frozen-input')
        bank=load_sensitivity_bank(Path(folder)/(info['bank_id']+'.dfsbank'))
        rows=[dict(saved_rank=i,profile=p,mean_top1=(count-i+1)/10-(j*.01),best_rank=i,worst_rank=i) for i in range(1,count+1) for j,p in enumerate(PROFILES)]
        r=dict(bank_id=info['bank_id'],input_id='frozen-input',kind=kind,status='completed',completed_batches=3,requested_batches=3,scenarios=5000,current_model=model_version(),saved_model=bank['payload']['model_version'],candidate_count=count,rows=rows)
        path=Path(folder)/'report.json';path.write_text(json.dumps(r))
        return path,lineups,r

    def test_summary_old_reports_input_matching_and_captain(self):
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as f:
                path,lineups,r=self.fixture(f,kind)
                review=load_review(path,f);v=review['rows'][0]
                self.assertAlmostEqual(v['drop'],.02);self.assertAlmostEqual(v['lowest'],.28)
                self.assertEqual(v['key'],roster_key(lineups[0],kind))
                lu=copy.deepcopy(lineups[0]);p=lu[0] if kind=='classic' else lu['Captain'];p['FlexProjection']=999
                self.assertNotEqual(v['key'],roster_key(lu,kind))
                if kind=='showdown':
                    lu=copy.deepcopy(lineups[0]);lu['Captain'],lu['Flex'][0]=lu['Flex'][0],lu['Captain']
                    self.assertNotEqual(v['key'],roster_key(lu,kind))
                r['current_model']='historical';path.write_text(json.dumps(r))
                self.assertFalse(load_review(path,f)['compatible'])
                r['rows'][0]['mean_top1']=float('nan');path.write_text(json.dumps(r))
                with self.assertRaises(ValueError):load_review(path,f)

    def test_reject_incomplete_wrong_input_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as f:
            path,_,r=self.fixture(f,'classic')
            for key,value in [('input_id','different'),('status','incomplete'),('rows',r['rows'][:-1]+[r['rows'][0]])]:
                bad=copy.deepcopy(r);bad[key]=value;path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):load_review(path,f)

    def test_both_tables_numeric_sort_pages_missing_and_save_identity(self):
        from main_window import MainWindow
        from ownership_review_ui import ReviewDialog
        with tempfile.TemporaryDirectory() as f:
            settings=QtCore.QSettings(f+'/test.ini',QtCore.QSettings.IniFormat)
            with patch('main_window.QtCore.QSettings',return_value=settings):w=MainWindow()
            try:
                for kind in ('classic','showdown'):
                    path,lineups,r=self.fixture(f,kind,160)
                    review=load_review(path,f);review['lookup']={r['key']:r for r in review['rows']}
                    setattr(w,'_'+kind+'_ownership_review',review)
                    if kind=='classic':w._populate_classic_lineups(lineups,'NFL')
                    else:w._populate_showdown_lineups(lineups)
                    table=w.tbl_cl if kind=='classic' else w.tbl_sd
                    labels=[table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
                    col=labels.index('Lowest tested %');count=table.columnCount()
                    w._finish_result_page(kind,lineups[:150]);self.assertEqual(table.columnCount(),count)
                    w._sort_result_column(kind,col);w._sort_result_column(kind,col)
                    current=w.last_classic if kind=='classic' else w.last_showdown
                    self.assertIs(current[0],lineups[-1])
                    table.cellWidget(0,0).setChecked(True)
                    saved=w.saved_classic if kind=='classic' else w.saved_showdown
                    self.assertIs(saved[0],lineups[-1])
                    getattr(w,'_'+kind+'_pages')[0].setCurrentIndex(1);self.assertEqual(table.rowCount(),10)
                    w._reset_result_sort(kind)
                    self.assertIs((w.last_classic if kind=='classic' else w.last_showdown)[0],lineups[0])
                    d=ReviewDialog(w,review);self.assertEqual(d.table.rowCount(),160);d.close()
                    self.assertEqual(lineups[0].sim_metrics['sim_top_one_pct'],160)
                    player=lineups[0][0] if kind=='classic' else lineups[0]['Captain']
                    player['FlexProjection']=999
                    for _ in range(2):
                        w._sort_result_column(kind,col)
                        current=w.last_classic if kind=='classic' else w.last_showdown
                        self.assertIs(current[-1],lineups[0])
            finally:w.close()
