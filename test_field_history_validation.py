from test_environment import install
install()
import copy
import time
import unittest
from unittest.mock import patch
from PyQt5 import QtCore, QtWidgets
import analysis_imports as ai
from historical_field import sample_history_field, distributions, CATEGORIES
from field_history_validation import evaluate_history, render_evaluation
from opponent_history import index_contest
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db, source_bytes
from test_showdown_performance import _showdown_players
from showdown_simulation import showdown_signature
from optimizers import _salary, _cpt_salary, _pkey


def priors():
    return {'Team split':{'3–3':.5,'4–2':.4,'5–1':.1},'Captain position':{'QB':.4,'WR':.4,'RB':.2},
            'Quarterbacks':{'0':.2,'1':.6,'2':.2},'Kicker/defense slots':{'0':1},
            'Salary left':{'$0–200':.1,'$201–700':.2,'$701–1,200':.2,'over $1,200':.5}}


class HistoricalFieldTests(unittest.TestCase):
    def test_sampler_is_deterministic_legal_and_does_not_mutate_inputs(self):
        pool = _showdown_players();before=copy.deepcopy(pool);target=priors()
        a=sample_history_field(pool,150,target,seed=29)
        b=sample_history_field(pool,150,target,seed=29)
        self.assertEqual(len(a),150)
        self.assertEqual([showdown_signature(lu) for lu in a],[showdown_signature(lu) for lu in b])
        self.assertEqual(pool,before)
        self.assertEqual(target,priors())
        for lu in a:
            players=[lu['Captain']]+lu['Flex']
            self.assertEqual(len({_pkey(p) for p in players}),6)
            self.assertEqual(len({p['Team'] for p in players}),2)
            self.assertLessEqual(_cpt_salary(lu['Captain'])+sum(_salary(p) for p in lu['Flex']),50000)

    def test_priors_move_construction_without_imposing_hard_limits(self):
        pool=_showdown_players()
        for p in pool:
            if 'Player 15' in p['Name']:p['Position']='K'
            if 'Player 17' in p['Name']:p['Position']='DST'
        zero=priors();two=priors();two['Kicker/defense slots']={'2':1}
        a=sample_history_field(pool,500,zero,seed=17)
        b=sample_history_field(pool,500,two,seed=17)
        self.assertGreater(distributions(b)['Kicker/defense slots'].get('2',0),distributions(a)['Kicker/defense slots'].get('2',0)+.3)

    def test_unavailable_targets_and_cancellation_are_disclosed(self):
        target=priors();target['Captain position']={'K':1}
        field=sample_history_field(_showdown_players(),50,target)
        self.assertEqual(field.diagnostic['unavailable_targets']['Captain position'],{'K':1})
        self.assertEqual(sample_history_field(_showdown_players(),50,target,cancelled=lambda:True),[])
        pool=[dict(Name=str(i),FlexID=str(i),Team='A' if i<3 else 'B',Position='WR',FlexSalary=20000,CptSalary=30000) for i in range(6)]
        self.assertEqual(sample_history_field(pool,50,target),[])

    def test_bad_identity_and_targets_fail_instead_of_guessing(self):
        pool=_showdown_players()
        with self.assertRaisesRegex(ValueError,'duplicate athlete'):sample_history_field(pool+[pool[0]],50,priors())
        target=priors();target['Quarterbacks']={'1':float('nan')}
        with self.assertRaisesRegex(ValueError,'invalid historical'):sample_history_field(pool,50,target)


class FieldHistoryValidationTests(unittest.TestCase):
    def fixture(self,name):
        f=SourceFixture(edit_results=results(name));self.addCleanup(f.close);return f

    def history(self, duplicate_game=False):
        f=self.fixture('first');index_contest(f.db,f.source['hash'])
        for name,day in [('second','22'),('later','24')]+([('same-later-game','24')] if duplicate_game else []):
            other=self.fixture(name)
            other.raw_salary.write_text(other.raw_salary.read_text(encoding='utf-8-sig').replace('09/21/2026',f'09/{day}/2026'),encoding='utf-8-sig')
            other.raw_result.write_text(other.raw_result.read_text(encoding='utf-8-sig').replace('2026-09-21',f'2026-09-{day}'),encoding='utf-8-sig')
            imported=ai.import_folders(other.results,other.salaries,db_path=f.db)
            self.assertFalse(imported['errors'])
        from opponent_history import sync_saved
        self.assertFalse(sync_saved(f.db)['errors'])
        return f

    def test_readonly_whole_game_cutoff_determinism_and_no_rank_training(self):
        f=self.history(True);before=logical_db(f.db);sources=source_bytes(f.root)
        a=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,101))
        b=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,101))
        self.assertEqual(a,b)
        self.assertEqual(len(a['training']),2)
        self.assertEqual(len(a['games']),1)
        self.assertTrue(all(r['date']<'2026-09-24' for r in a['training']))
        self.assertTrue(all(r['date']>='2026-09-24' for r in a['games']))
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
        f.change('UPDATE opponent_user_contest_stats SET best_rank=999,top_one_pct_entries=0')
        self.assertEqual(a,evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,101)))
        self.assertIn('hypothetical sampling weights',render_evaluation(a))
        uniform=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,),draw_mode='uniform')
        self.assertEqual(uniform['priors'],a['priors'])
        self.assertIn('uniform athlete draw weights',render_evaluation(uniform))

    def test_future_constructions_do_not_change_training_profile(self):
        f=self.history();a=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,))
        f.change("UPDATE opponent_user_contest_stats SET stats_json=json_set(stats_json,'$.constructions.tables.Quarterbacks',json('[{\"label\":\"2\",\"entries\":50}]')) WHERE contest_key IN (SELECT contest_key FROM opponent_contests WHERE end_date>=?)",('2026-09-24',))
        b=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,))
        self.assertEqual(a['priors'],b['priors']);self.assertEqual(a['profile_digest'],b['profile_digest'])
        self.assertNotEqual(a['games'][0]['observed'],b['games'][0]['observed'])

    def test_insufficient_dates_and_cancel_do_not_publish(self):
        f=self.history()
        with self.assertRaisesRegex(ValueError,'at least two earlier'):evaluate_history(f.db,'2026-09-21',count=50)
        with self.assertRaisesRegex(ValueError,'at least two earlier'):evaluate_history(f.db,'2026-10-01',count=50)
        with self.assertRaises(ai.ImportCancelled):evaluate_history(f.db,'2026-09-24',lambda:True,count=50)

    def test_changed_source_is_excluded_and_cannot_supply_training(self):
        f=self.history()
        from pathlib import Path
        Path(f.source['snapshot']).write_text('changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'at least two earlier'):evaluate_history(f.db,'2026-09-24',count=50)

    def test_reassociation_during_sampling_prevents_publication(self):
        f=self.history();original=sample_history_field
        def changed(*args,**kwargs):
            field=original(*args,**kwargs)
            f.change('DELETE FROM analysis_salary_pairs WHERE result_hash=?',(f.source['hash'],))
            return field
        with patch('field_history_validation.sample_history_field',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'association changed'):evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,))

    def test_incomplete_sampler_fields_are_not_scored_as_complete(self):
        f=self.history()
        with patch('field_history_validation.sample_field',return_value=[]):
            report=evaluate_history(f.db,'2026-09-24',count=50,seeds=(17,))
        self.assertEqual(report['scores'],{})
        self.assertFalse(report['games'][0]['comparable'])
        self.assertIn('excluded from aggregate scores',render_evaluation(report))

    def test_dialog_runs_optin_benchmark_without_saving_profile(self):
        from opponent_history_ui import OpponentHistoryDialog
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        f=self.history()
        dialog=OpponentHistoryDialog(f.db)
        dialog.cutoff.setDate(QtCore.QDate(2026,9,24))
        before=logical_db(f.db)
        dialog.start('evaluate')
        deadline=time.monotonic()+15
        while dialog._thread is not None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        self.assertIsNone(dialog._thread)
        self.assertIn('Whole-game Showdown field experiment',dialog.report.toPlainText())
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(before,logical_db(f.db))
        dialog.reject();dialog.deleteLater();app.processEvents()
