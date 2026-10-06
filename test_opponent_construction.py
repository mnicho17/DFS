from test_environment import install
install()
import copy
import unittest

import analysis_imports as ai
from opponent_analysis import analyze_standings, render_report
from opponent_construction import analyze_saved_contest, construction_summary, saved_contests
from test_hindsight_evidence import SourceFixture
from test_portfolio_risk_evidence import logical_db, source_bytes


class SavedConstructionTests(unittest.TestCase):
    def setUp(self):
        self.f=SourceFixture(); self.addCleanup(self.f.close)

    def test_saved_mapping_has_team_role_salary_metrics_and_is_readonly(self):
        before=logical_db(self.f.db); files=source_bytes(self.f.root)
        report=analyze_saved_contest(self.f.db,self.f.source['hash'])
        self.assertEqual(before,logical_db(self.f.db)); self.assertEqual(files,source_bytes(self.f.root))
        self.assertEqual(report['salary_evidence']['sha256'],self.f.salary['hash'])
        c=report['portfolios'][0]['constructions']
        self.assertEqual(c['known_entries'],1); self.assertEqual(c['unknown_entries'],0)
        self.assertIn('Team split',c['tables']); self.assertIn('Captain team split',c['tables'])
        self.assertIn('Historical salary:',render_report(report,'Synthetic_User'))
        self.assertEqual(saved_contests(self.f.db)[0]['result_hash'],self.f.source['hash'])

    def test_changed_salary_source_fails_without_replacing_saved_mapping(self):
        before=logical_db(self.f.db)
        from pathlib import Path
        path=Path(self.f.salary['snapshot']); path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(ValueError):analyze_saved_contest(self.f.db,self.f.source['hash'])
        self.assertEqual(before,logical_db(self.f.db))

    def test_missing_mapping_and_cancel_do_not_write(self):
        before=logical_db(self.f.db)
        with self.assertRaises(ValueError):analyze_saved_contest(self.f.db,'absent')
        with self.assertRaises(ai.ImportCancelled):analyze_saved_contest(self.f.db,self.f.source['hash'],lambda:True)
        self.assertEqual(before,logical_db(self.f.db))

    def test_missing_ambiguous_or_conflicting_identity_never_inferred(self):
        players=self.f.salary['manifest']['players']
        lineup=[(p['name'],p['role']) for p in players if p['role']=='FLEX'][:6]
        lineup[0]=(lineup[0][0],'CPT')
        duplicate=next(p for p in players if p['name']==lineup[0][0] and p['role']=='CPT')
        for bad in ([],players+[copy.deepcopy(duplicate)]):
            summary=construction_summary([(lineup,2)],bad)
            self.assertEqual(summary['known_entries'],0)
            self.assertEqual(summary['unknown_entries'],2)
        lineup[0]=(lineup[0][0]+' (999999)', 'CPT')
        self.assertEqual(construction_summary([(lineup,1)],players)['known_entries'],0)

    def test_classic_uses_historical_eligibility_and_qb_stack(self):
        f=SourceFixture('classic');self.addCleanup(f.close)
        report=analyze_saved_contest(f.db,f.source['hash'])
        c=report['field_constructions'];self.assertEqual(c['known_entries'],1)
        self.assertIn('QB same-team WR/TE',c['tables']);self.assertNotIn('Captain position',c['tables'])

    def test_unmapped_file_retains_original_analysis_without_guessed_metadata(self):
        report=analyze_standings(self.f.source['snapshot'])
        self.assertIsNone(report['field_constructions'])
        self.assertIsNone(report['portfolios'][0]['constructions'])

    def test_uniform_captain_context_is_an_explicit_assumption_only_for_owner(self):
        from opponent_construction import apply_user_context
        report=analyze_saved_contest(self.f.db,self.f.source['hash'])
        apply_user_context(report,'someone_else')
        self.assertNotIn('build_context',report['portfolios'][0])
        apply_user_context(report,'Synthetic_User')
        context=report['portfolios'][0]['build_context']
        self.assertTrue(context['captain_locked']);self.assertFalse(context['typical_sim_workflow'])
        self.assertIn('assumption',context['basis'])

    def test_real_saved_analysis_worker_delivers_metadata_and_suppresses_cancel(self):
        from opponent_analysis_ui import OpponentAnalysisWorker
        worker=OpponentAnalysisWorker('unused','classic',self.f.db,self.f.source['hash'],'Synthetic_User',True)
        delivered=[];errors=[]
        worker.result.connect(delivered.append);worker.error.connect(errors.append)
        worker.run()
        self.assertEqual(len(delivered),1);self.assertFalse(errors)
        self.assertEqual(delivered[0]['format'],'showdown')
        self.assertTrue(delivered[0]['portfolios'][0]['build_context']['captain_locked'])
        worker.stop.set();worker.run()
        self.assertEqual(len(delivered),1);self.assertFalse(errors)
