from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
from test_hindsight_solver import athletes
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from opponent_history import index_contest
from receiver_specialist_review import review
from winner_size_matching import render
from portfolio_pattern_audit import PatternAuditLimit
from test_portfolio_risk_evidence import logical_db,source_bytes


class WinnerSizeMatchingTests(unittest.TestCase):
    def fixture(self,tie=False,no_receiver_peer=False,peer_count=10):
        pool=athletes();item=copy.deepcopy(pool[6]);item.update(key='108',name='I')
        for role,data in item['roles'].items():data['id']='1108' if role=='CPT' else '108'
        pool.append(item)
        def edit(rows):
            results()(rows);h=rows[0]
            for i,row in enumerate(rows[1:101]):
                peer=5<=i<5+5*peer_count
                name='Winner' if i<5 else 'Peer'+str((i-5)//5) if peer else 'Large'
                lineup='C H B D E F' if peer else 'C A B D G I'
                if no_receiver_peer and 5<=i<10:lineup='B H C D E F'
                names=lineup.split()
                row[h.index('EntryName')]=name
                row[h.index('Rank')]='1' if i==0 or (tie and i==5) else '51'
                row[h.index('Lineup')]='CPT '+names[0]+' '+' '.join('FLEX '+n for n in names[1:])
        f=SourceFixture(pool=pool,edit_results=edit);self.addCleanup(f.close)
        index_contest(f.db,f.source['hash']);return f

    def test_exact_size_peers_audit_all_entries_without_mutation(self):
        f=self.fixture();before=logical_db(f.db);sources=source_bytes(f.root)
        r=review(f.db);d=r['contests'][0]['winner_portfolios']
        self.assertEqual((d['peer_users'],d['peer_entries'],d['peer_excluded']),(10,50,0))
        self.assertEqual(set(d['size_matched_peers']),{'5'})
        peers=d['size_matched_peers']['5']
        self.assertEqual(peers['means'],[0,0,0,100,0])
        for minimum in ('5','10'):
            m=r['size_matched_by_preset']['Unclassified'][minimum]['metrics']
            self.assertEqual(m[0]['difference_pp'],100)
            self.assertEqual(m[3]['difference_pp'],-100)
            self.assertEqual(m[0]['winner_portfolios'],1)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))

    def test_all_tied_winners_are_excluded_from_peer_support(self):
        f=self.fixture(tie=True);r=review(f.db)
        d=r['contests'][0]['winner_portfolios']
        self.assertEqual(len(d['users']),2);self.assertEqual(d['peer_users'],9)
        self.assertEqual(r['size_matched_by_preset']['Unclassified']['10']['metrics'][0]['games'],0)

    def test_partial_peer_withholds_entire_portfolio_and_ten_user_support(self):
        f=self.fixture();f.change("UPDATE opponent_entry_slots SET original_label='Wrong (999)' WHERE entry_id='1005' AND role='CPT'")
        r=review(f.db);d=r['contests'][0]['winner_portfolios']
        self.assertEqual(d['peer_excluded'],1)
        self.assertEqual(d['size_matched_peers']['5']['qualified'],9)
        self.assertIsNone(r['size_matched_by_preset']['Unclassified']['10']['metrics'][0]['difference_pp'])

    def test_receiver_support_does_not_borrow_kdst_support_or_size_bands(self):
        f=self.fixture(no_receiver_peer=True);r=review(f.db)
        metrics=r['size_matched_by_preset']['Unclassified']['10']['metrics']
        self.assertIsNone(metrics[0]['difference_pp'])
        self.assertEqual(metrics[3]['difference_pp'],-100)
        f=self.fixture(peer_count=2);r=review(f.db)
        self.assertTrue(all(m['difference_pp'] is None for m in r['size_matched_by_preset']['Unclassified']['5']['metrics']))

    def test_peer_and_whole_review_bounds_abort_without_truncation(self):
        f=self.fixture()
        for key,value in [('MAX_PEER_USERS',1),('MAX_PEER_ENTRIES',49),('MAX_REVIEW_PEER_ENTRIES',49)]:
            with patch('winner_size_matching.'+key,value):
                with self.assertRaises(PatternAuditLimit):review(f.db)

    def test_peer_username_conflicts_and_cross_contest_budget_are_not_ignored(self):
        f=self.fixture();f.change("UPDATE opponent_users SET username='Different User' WHERE user_key='dk:peer0'")
        r=review(f.db)
        self.assertEqual(r['contests'][0]['winner_portfolios']['peer_excluded'],1)
        self.assertIsNone(r['size_matched_by_preset']['Unclassified']['10']['metrics'][0]['difference_pp'])
        from contextlib import closing
        from opponent_history import _connect
        key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        def twice(db_path,cancelled,progress,**kwargs):
            with closing(_connect(f.db,True)) as conn:
                for _ in range(2):kwargs['_collector'](conn,key,f.salary['manifest']['players'],100,100,'',cancelled)
            self.fail('Expected the whole-review peer bound.')
        with patch('winner_size_matching.MAX_REVIEW_PEER_ENTRIES',99),patch('showdown_group_review.review_history',side_effect=twice):
            with self.assertRaisesRegex(PatternAuditLimit,'Whole-review peer'):review(f.db)

    def test_equal_game_weight_and_omit_one_sensitivity(self):
        from winner_size_matching import summarize
        def contest(game,gap):
            return dict(game=['date',game],winner_portfolios=dict(users=[dict(complete=True,entries=5,rates=[50+gap]*5)],
                size_matched_peers={'5':dict(metric_users=[10]*5,means=[50]*5)}))
        r=summarize([contest('A',2),contest('A',2),contest('B',4),contest('C',-6)],10)
        self.assertEqual(r['metrics'][0]['difference_pp'],0)
        self.assertEqual(r['metrics'][0]['games'],3)
        self.assertEqual(r['metrics'][0]['omit_one_range'],[-2,3])

    def test_old_report_cannot_claim_a_peer_comparison(self):
        self.assertIn('unavailable',render({'contests':[]}))
