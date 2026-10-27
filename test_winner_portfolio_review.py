from test_environment import install
install()
import unittest
from unittest.mock import patch
from pathlib import Path
import analysis_imports as ai
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db,source_bytes
from opponent_history import index_contest
from winner_portfolio_review import review_winners,render_winner,prior_record,ReviewLimitError,comparisons

class WinnerPortfolioTests(unittest.TestCase):
    def fixture(self,kind='showdown',tie=False,winner_entries=50):
        def edit(rows):
            results()(rows);h=rows[0]
            for i,row in enumerate(rows[1:101]):row[h.index('EntryName')]='Winner' if i<winner_entries else 'Other'
            if tie:
                rows[51][h.index('Rank')]='1'
        f=SourceFixture(kind=kind,edit_results=edit);self.addCleanup(f.close);index_contest(f.db,f.source['hash']);return f
    def test_entire_winner_portfolio_including_losing_entries_and_exact_peers(self):
        f=self.fixture();before=logical_db(f.db);sources=source_bytes(f.root)
        result=review_winners(f.db,'2026-09-22');self.assertEqual(len(result['contests']),1)
        c=result['contests'][0];self.assertEqual(len(c['winners']),1);p=c['winners'][0]
        self.assertEqual(p['entries'],50);self.assertEqual(p['winning_entries'],1);self.assertEqual(p['known'],50)
        self.assertEqual(p['other_known'],49);self.assertEqual(p['peer_users'],1)
        self.assertEqual(sum(e['captain'] for e in p['exposures']),50)
        self.assertEqual(sum(e['total'] for e in p['exposures']),300)
        self.assertIn('entire observed portfolio',render_winner(c,p));self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
    def test_tied_first_place_users_are_each_included_once(self):
        f=self.fixture(tie=True);result=review_winners(f.db,'2026-09-22')
        profiles=result['contests'][0]['winners'];self.assertEqual(len(profiles),2)
        self.assertTrue(all(p['entries']==50 and p['peer_users']==0 for p in profiles))
    def test_classic_slots_and_flex_exposure(self):
        f=self.fixture('classic');r=review_winners(f.db,'2026-09-22',contest_format='classic');p=r['contests'][0]['winners'][0]
        self.assertEqual(p['known'],50);self.assertEqual(sum(e['total'] for e in p['exposures']),450)
        self.assertEqual(sum(e['flex'] for e in p['exposures']),50);self.assertEqual(sum(e['captain'] for e in p['exposures']),0)
    def test_missing_metadata_is_unknown_not_an_empty_portfolio(self):
        f=self.fixture();f.change("UPDATE opponent_entries SET metadata_complete=0 WHERE entry_id='1000'")
        p=review_winners(f.db,'2026-09-22')['contests'][0]['winners'][0]
        self.assertEqual(p['entries'],50);self.assertEqual(p['unknown'],1);self.assertEqual(p['known'],49)
        self.assertEqual(p['peer_users'],0);self.assertEqual(p['winning_entries'],1);self.assertEqual(p['winning_lineups'],[])
    def test_changed_source_excluded_and_cancelled_review_not_published(self):
        f=self.fixture()
        with self.assertRaises(ai.ImportCancelled):review_winners(f.db,'2026-09-22',cancelled=lambda:True)
        path=Path(f.salary['snapshot']);path.write_bytes(path.read_bytes()+b'\n')
        r=review_winners(f.db,'2026-09-22');self.assertEqual(r['contests'],[]);self.assertEqual(len(r['excluded']),1)
    def test_no_first_place_present_does_not_invent_a_winner(self):
        f=self.fixture();f.change('UPDATE opponent_entries SET rank=rank+1');f.change('UPDATE opponent_user_contest_stats SET best_rank=best_rank+1,stats_json=json_set(stats_json,\'$.best_rank\',best_rank+1)')
        r=review_winners(f.db,'2026-09-22');self.assertEqual(r['contests'][0]['winners'],[])
    def test_same_date_has_no_prior_success_credit(self):
        f=self.fixture();p=review_winners(f.db,'2026-09-22')['contests'][0]['winners'][0]
        self.assertEqual(p['prior_record']['observed_slates'],0);self.assertFalse(p['prior_record']['repeat_observed_top_cohort'])

    def test_exact_entry_count_has_no_broad_band_fallback(self):
        f=self.fixture(winner_entries=49)
        p=review_winners(f.db,'2026-09-22')['contests'][0]['winners'][0]
        self.assertEqual(p['entries'],49);self.assertEqual(p['peer_users'],0)
        self.assertTrue(all(value is None for value in p['peer_medians'].values()))
    def test_global_bound_aborts_without_publishing_partial_results(self):
        f=self.fixture()
        with patch('winner_portfolio_review.MAX_WINNER_ENTRIES',1):
            with self.assertRaises(ReviewLimitError):review_winners(f.db,'2026-09-22')
    def test_salary_association_changed_during_review_requires_rerun(self):
        f=self.fixture()
        def progress(text):f.change('DELETE FROM analysis_salary_pairs')
        with self.assertRaisesRegex(ValueError,'association changed'):
            review_winners(f.db,'2026-09-22',progress=progress)
    def test_prior_history_excludes_same_day_future_and_other_format_and_deduplicates_slate(self):
        def row(d,rank=1,kind='showdown',game='A@B',n=100):return (d,(kind,d,(game,)),rank,n)
        records=[row('2026-09-01'),row('2026-09-01'),row('2026-09-02',2,n=200),row('2026-09-03',50),row('2026-09-04'),row('2026-09-05'),row('2026-09-02',kind='classic')]
        p=prior_record(records,'2026-09-04','showdown')
        self.assertEqual(p,dict(observed_slates=3,ranked_slates=3,rank1_slates=1,top_cutoff_slates=2,repeat_observed_top_cohort=True))
    def test_unknown_ranks_do_not_count_toward_repeat_observation_minimum(self):
        records=[('2026-09-0'+str(i),('showdown','2026-09-0'+str(i),('A@B',)),rank,100) for i,rank in enumerate((1,1,None),1)]
        p=prior_record(records,'2026-09-04','showdown')
        self.assertEqual(p['observed_slates'],3);self.assertEqual(p['ranked_slates'],2);self.assertFalse(p['repeat_observed_top_cohort'])

    def test_same_user_tied_winning_copies_remain_one_profile(self):
        f=self.fixture();f.change("UPDATE opponent_entries SET rank=1 WHERE entry_id='1001'")
        profiles=review_winners(f.db,'2026-09-22')['contests'][0]['winners']
        self.assertEqual(len(profiles),1);self.assertEqual(profiles[0]['entries'],50)
        self.assertEqual(profiles[0]['winning_entries'],2);self.assertEqual(len(profiles[0]['winning_lineups']),1)
        self.assertEqual(profiles[0]['winning_lineups'][0]['copies'],2)
    def test_aggregate_weights_slates_equally_despite_tie_and_contest_counts(self):
        f=self.fixture();r=review_winners(f.db,'2026-09-22');import copy
        a=r['contests'][0];a['winners']*=10
        for p in a['winners']:p['metrics']['mean_salary']=40000;p['peer_medians']['mean_salary']=41000
        b=copy.deepcopy(a);b['slate']=['showdown','2026-09-23',['C@D']];b['winners']=b['winners'][:1]
        b['winners'][0]['metrics']['mean_salary']=50000;b['winners'][0]['peer_medians']['mean_salary']=49000
        r['contests']=[a,copy.deepcopy(a),b]
        metric=next(iter(comparisons(r).values()))['metrics']['mean_salary']
        self.assertEqual(metric['winner'],45000);self.assertEqual(metric['peer'],45000);self.assertEqual(metric['slates'],2)

if __name__=='__main__':unittest.main()


