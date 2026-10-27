from test_environment import install
install()
import unittest
from unittest.mock import patch
from contextlib import closing
from construction_outcomes import contrast,contest_outcomes,review_outcomes,render,summarize_outcomes
from portfolio_pattern_audit import METRICS,PatternAuditLimit
import test_portfolio_pattern_audit as patterns
from test_portfolio_risk_evidence import logical_db,source_bytes
from opponent_history import _connect
import analysis_imports as ai

class OutcomeTests(unittest.TestCase):
    fixture=patterns.PortfolioPatternAuditTests.fixture
    def calculate(self,f,cancelled=lambda:False):
        key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        with closing(_connect(f.db,True)) as c:
            return contest_outcomes(c,key,f.salary['manifest']['players'],100,100,'',cancelled)
    def test_no_common_support_is_unknown(self):
        f=self.fixture();r=self.calculate(f)
        self.assertTrue(all(v['strata']==0 for v in r['matched'].values()))
        self.assertTrue(all(v['difference_pp']['top1'] is None for v in r['matched'].values()))
    def test_matching_removes_captain_composition_confound(self):
        cells={('captainA',0):[[90,9,18,9],[10,1,2,1]],('captainB',0):[[10,9,10,9],[90,81,90,81]]}
        self.assertGreater((82/100-18/100),0)
        self.assertAlmostEqual(contrast(cells)[METRICS[0]]['difference_pp']['top1'],0)
    def test_overlap_weights_and_minimum_support(self):
        cells={('a',0):[[5,0,1,0],[5,5,5,5]],('b',0):[[4,4,4,4],[100,0,0,0]]}
        r=contrast(cells)[METRICS[0]]
        self.assertEqual(r['entries'],10);self.assertEqual(r['strata'],1)
        self.assertEqual(r['difference_pp']['rank1'],100)
    def test_missing_ranks_and_conflicting_ids_are_unknown(self):
        f=self.fixture();f.change("UPDATE opponent_entries SET rank=NULL WHERE entry_id='1050'")
        f.change("UPDATE opponent_entry_slots SET original_label='A (1101)' WHERE entry_id='1099' AND role='CPT'")
        r=self.calculate(f);self.assertEqual(r['missing_rank'],1);self.assertEqual(r['unknown'],1)
    def test_readonly_sources_and_database(self):
        f=self.fixture();before=logical_db(f.db);src=source_bytes(f.root)
        r=review_outcomes(f.db)
        self.assertEqual(len(r['contests']),1);self.assertIn('no common support',render(r))
        self.assertEqual(before,logical_db(f.db));self.assertEqual(src,source_bytes(f.root))
    def test_cancel_and_budget_publish_no_partial_result(self):
        f=self.fixture()
        with self.assertRaises(ai.ImportCancelled):self.calculate(f,lambda:True)
        with patch('construction_outcomes.MAX_RESOLUTIONS',0):
            with self.assertRaises(PatternAuditLimit):review_outcomes(f.db)
    def test_changed_index_is_rejected(self):
        f=self.fixture()
        with patch('historical_series_review._inventory',side_effect=['before','after']):
            with self.assertRaisesRegex(ValueError,'index changed'):review_outcomes(f.db)
    def test_equal_game_weights_and_ties_endpoint(self):
        def contest(game,value):
            return dict(game=game,matched={m:dict(strata=1,difference_pp={e:value for e in ('top1','top5','rank1')}) for m in METRICS})
        r=summarize_outcomes([contest(('d','A'),10),contest(('d','A'),10),contest(('e','B'),-10)])
        self.assertEqual(r[METRICS[0]]['endpoints']['rank1']['difference_pp'],0)

    def test_real_matching_counts_all_rank_one_ties_and_duplicate_entries(self):
        from test_hindsight_solver import athletes
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest
        pool=athletes()
        for i,p in enumerate(pool):p['team']='SEA' if i<5 else 'NE'
        def edit(rows):
            results(duplicate=True)(rows);h=rows[0]
            for n,row in enumerate(rows[1:101]):
                row[h.index('Lineup')]='CPT A FLEX B FLEX C FLEX D FLEX E FLEX F' if n<50 else 'CPT A FLEX B FLEX C FLEX F FLEX G FLEX H'
                if n<2:row[h.index('Rank')]='1'
            rows[101]=list(rows[1])
        f=SourceFixture(pool=pool,edit_results=edit);self.addCleanup(f.close)
        index_contest(f.db,f.source['hash'])
        r=self.calculate(f)['matched']
        self.assertEqual(r[METRICS[0]]['entries'],100)
        self.assertAlmostEqual(r[METRICS[0]]['difference_pp']['rank1'],4)
        self.assertAlmostEqual(r[METRICS[2]]['difference_pp']['rank1'],-4)
    def test_explicit_identity_conflicts_are_order_independent(self):
        answers=[]
        for eid in ('1000','1099'):
            f=self.fixture()
            f.change("UPDATE opponent_entry_slots SET original_label='A (1101)' WHERE entry_id=? AND role='CPT'",(eid,))
            answers.append(self.calculate(f))
        self.assertEqual(answers[0],answers[1]);self.assertEqual(answers[0]['unknown'],1)
    def test_original_source_change_never_produces_outcomes(self):
        from pathlib import Path
        f=self.fixture();p=Path(f.salary['snapshot']);p.write_bytes(p.read_bytes()+b'\n')
        r=review_outcomes(f.db);self.assertEqual(r['contests'],[]);self.assertEqual(len(r['excluded']),1)

if __name__=='__main__':unittest.main()
