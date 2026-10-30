from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
import test_receiver_specialist_review as receiver_tests
from receiver_specialist_review import review
from winner_pairing_review import summarize,render
from portfolio_pattern_audit import PatternAuditLimit
from test_portfolio_risk_evidence import logical_db,source_bytes


class WinnerPairingTests(unittest.TestCase):
    fixture=receiver_tests.ReceiverSpecialistTests.fixture

    def test_tied_users_all_entries_and_winning_entries_are_separate(self):
        f=self.fixture();before=logical_db(f.db);sources=source_bytes(f.root)
        r=review(f.db);detail=r['contests'][0]['winner_portfolios']
        self.assertEqual((detail['field_entries'],detail['field_known']),(100,100))
        self.assertEqual(len(detail['users']),2)
        for user in detail['users']:
            self.assertEqual((user['entries'],user['winning_entries']),(50,10))
            self.assertTrue(user['complete'])
            self.assertEqual(user['rates'],[80,40,20,20,20])
            self.assertEqual(user['winning_rates'],[0,100,0,0,0])
            self.assertEqual(user['other_rates'],[100,25,25,25,25])
        summary=r['winner_portfolios_by_preset']['Unclassified']
        self.assertEqual(summary['qualified_entries'],100)
        self.assertEqual(summary['qualified_user_contests'],2)
        self.assertEqual(summary['winning_minus_other'][0]['difference_pp'],-100)
        self.assertEqual(summary['winning_minus_other'][1]['difference_pp'],75)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))

    def test_one_unresolved_nonwinning_entry_withholds_whole_portfolio(self):
        f=self.fixture();f.change("UPDATE opponent_entry_slots SET original_label='Wrong (999)' WHERE entry_id='1000' AND role='CPT'")
        r=review(f.db);users=r['contests'][0]['winner_portfolios']['users']
        bad=next(u for u in users if u['unknown'])
        self.assertEqual(bad['entries'],50)
        self.assertFalse(bad['complete']);self.assertEqual(bad['rates'],[None]*5)
        self.assertIsNone(bad['counts'])
        self.assertEqual(r['winner_portfolios_by_preset']['Unclassified']['excluded_user_contests'],1)

    def test_missing_rank_is_retained_for_usage_not_inferred_as_loss(self):
        f=self.fixture();f.change("UPDATE opponent_entries SET rank=NULL WHERE entry_id='1000'")
        r=review(f.db);user=next(u for u in r['contests'][0]['winner_portfolios']['users'] if u['missing_rank'])
        self.assertTrue(user['complete']);self.assertEqual(user['entries'],50)
        self.assertEqual(user['rates'],[80,40,20,20,20])
        self.assertEqual(user['missing_rank'],1)

    def test_no_rank_one_and_bounds_never_invent_or_truncate_winners(self):
        f=self.fixture()
        with patch('winner_pairing_review.MAX_ENTRIES',1):
            with self.assertRaises(PatternAuditLimit):review(f.db)
        f.change('UPDATE opponent_entries SET rank=rank+1')
        r=review(f.db)
        self.assertEqual(r['contests'][0]['winner_portfolios']['users'],[])
        self.assertEqual(r['winner_portfolios_by_preset']['Unclassified']['qualified_entries'],0)
        self.assertTrue(all(m['difference_pp'] is None for m in r['winner_portfolios_by_preset']['Unclassified']['metrics']))

    def test_username_identity_conflict_withholds_profile_despite_known_rosters(self):
        f=self.fixture();f.change("UPDATE opponent_users SET username='Different User' WHERE user_key='dk:winner'")
        r=review(f.db)
        user=next(u for u in r['contests'][0]['winner_portfolios']['users'] if u['user_key']=='dk:winner')
        self.assertEqual(user['unknown'],0)
        self.assertFalse(user['complete']);self.assertEqual(user['rates'],[None]*5)

    def test_global_winner_entry_budget_is_not_restarted_for_each_contest(self):
        from contextlib import closing
        from opponent_history import _connect
        f=self.fixture();key=f.query('SELECT contest_key FROM opponent_contests')[0][0]
        def twice(db_path,cancelled,progress,**kwargs):
            with closing(_connect(f.db,True)) as conn:
                for _ in range(2):
                    kwargs['_collector'](conn,key,f.salary['manifest']['players'],100,100,'',cancelled)
            self.fail('Expected the whole-review entry bound.')
        with patch('winner_pairing_review.MAX_ENTRIES',150),patch('showdown_group_review.review_history',side_effect=twice):
            with self.assertRaisesRegex(PatternAuditLimit,'Whole-review'):review(f.db)

    def test_old_report_is_unknown_and_equal_user_game_weighting(self):
        self.assertIn('unavailable',render({'contests':[{}]}))
        def contest(game,rate):
            return dict(game=['date',game],winner_portfolios=dict(field_rates=[0]*5,users=[dict(complete=True,entries=1,missing_rank=0,rates=[rate]*5)]))
        result=summarize([contest('A',100),contest('A',100),contest('B',0)])
        self.assertEqual(result['metrics'][0]['difference_pp'],50)
        self.assertEqual(result['metrics'][0]['games'],2)
