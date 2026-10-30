from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
from receiver_specialist_review import features,review,render,METRICS
from test_hindsight_solver import athletes
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from opponent_history import index_contest
from test_portfolio_risk_evidence import logical_db,source_bytes
import analysis_imports as ai
from portfolio_pattern_audit import PatternAuditLimit


class ReceiverSpecialistTests(unittest.TestCase):
    def fixture(self):
        pool=athletes()
        for i,name,pos in ((8,'I','WR'),(9,'J','K')):
            item=copy.deepcopy(pool[6]);item.update(key=str(100+i),name=name,position=pos)
            for role,data in item['roles'].items():data['id']=str((1100 if role=='CPT' else 100)+i)
            pool.append(item)
        def edit(rows):
            results()(rows);head=rows[0]
            patterns=('C A B D G I','C H B D G I','C A B D E I','C A B D E F','C A B E F J')
            for i,row in enumerate(rows[1:101]):
                names=patterns[i%5].split()
                row[head.index('Lineup')]='CPT '+names[0]+' '+' '.join('FLEX '+n for n in names[1:])
                row[head.index('Rank')]='1' if i%5==1 else '51'
        f=SourceFixture(pool=pool,edit_results=edit);self.addCleanup(f.close)
        index_contest(f.db,f.source['hash']);return f

    def test_role_team_and_exact_count_baselines(self):
        def slot(role,pos,team):return ('x',role,dict(position=pos,team=team))
        slots=[slot('CPT','WR','A'),slot('FLEX','QB','A'),slot('FLEX','QB','B'),slot('FLEX','RB','A'),slot('FLEX','WR','B'),slot('FLEX','TE','B')]
        self.assertEqual(features(slots),(1,0,0,0))
        slots[1]=slot('FLEX','K','A')
        self.assertEqual(features(slots),(0,1,None,None))
        slots[0]=slot('CPT','K','A')
        self.assertEqual(features(slots),(None,None,1,None))
        slots[2]=slot('FLEX','DST','B')
        self.assertEqual(features(slots),(None,None,None,1))

    def test_verified_matching_counts_and_sources_are_unchanged(self):
        f=self.fixture();before=logical_db(f.db);sources=source_bytes(f.root)
        r=review(f.db);self.assertEqual(len(r['contests']),1)
        c=r['contests'][0];self.assertEqual((c['unknown'],c['missing_rank']),(0,0))
        self.assertEqual(c['observed'][METRICS[0]],[ [20,20,20,20],[80,0,0,0]])
        self.assertAlmostEqual(c['matched'][METRICS[0]]['difference_pp']['rank1'],-100)
        for metric in METRICS[1:]:
            self.assertEqual(c['observed'][metric],[[40,20,20,20],[20,0,0,0]])
            self.assertEqual(c['matched'][metric]['entries'],60)
            self.assertAlmostEqual(c['matched'][metric]['difference_pp']['rank1'],-50)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
        self.assertIn('not measured',render(r))

    def test_unresolved_identity_is_excluded_not_a_negative_outcome(self):
        f=self.fixture();f.change("UPDATE opponent_entry_slots SET original_label='Wrong (999)' WHERE entry_id='1000' AND role='CPT'")
        c=review(f.db)['contests'][0]
        self.assertEqual(c['unknown'],1)
        self.assertEqual(c['observed'][METRICS[0]][1][0],79)

    def test_cancel_limits_and_changed_index_reject_whole_report(self):
        f=self.fixture()
        with self.assertRaises(ai.ImportCancelled):review(f.db,cancelled=lambda:True)
        with patch('construction_outcomes.MAX_RESOLUTIONS',0):
            with self.assertRaises(PatternAuditLimit):review(f.db)
        with patch('historical_series_review._inventory',side_effect=['before','after']):
            with self.assertRaisesRegex(ValueError,'index changed'):review(f.db)

    def test_support_thresholds_and_receiver_denominator_are_independent(self):
        f=self.fixture();c=review(f.db)['contests'][0]
        self.assertEqual(c['support_checks']['10']['matched'][METRICS[0]]['strata'],1)
        f.change("UPDATE opponent_entries SET rank=NULL WHERE entry_id='1001'")
        c=review(f.db)['contests'][0]
        self.assertEqual(c['missing_rank'],1)
        self.assertEqual(c['observed'][METRICS[0]][0][0],19)
