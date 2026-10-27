from test_environment import install
install()
import csv,json,unittest
from pathlib import Path
from unittest.mock import patch
import analysis_imports as ai
from opponent_history import index_contest
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db,source_bytes
from winner_portfolio_review import review_winners,render_overview,render_winner,followup_outcomes,ReviewLimitError

class PriorPortfolioFollowupTests(unittest.TestCase):
    def fixture(self):
        f=SourceFixture();self.addCleanup(f.close);return f
    def day(self,target,date,number,*,loses=False,entries=50,preset='150 Max',unknown_user=None,kind='showdown'):
        other=SourceFixture(kind=kind,edit_results=results('day-'+str(number)));self.addCleanup(other.close)
        with other.raw_salary.open(newline='',encoding='utf-8-sig') as h:rows=list(csv.reader(h))
        for row in rows[1:]:row[6]=row[6].replace('09/21/2026',date[5:7]+'/'+date[8:10]+'/'+date[:4])
        with other.raw_salary.open('w',newline='',encoding='utf-8-sig') as h:csv.writer(h).writerows(rows)
        with other.raw_result.open(newline='',encoding='utf-8-sig') as h:rows=list(csv.reader(h))
        head=rows[0]
        for i,row in enumerate(rows[1:101]):
            row[head.index('EntryName')]='Winner' if i<entries else 'Other'
            row[head.index('EntryId')]=str(number*1000+i)
            row[head.index('SlateDate')]=date
            row[head.index('Rank')]=str(51+i if i<entries else i-entries+1) if loses else str(i+1)
        if unknown_user is not None:rows[1 if unknown_user=='Winner' else entries+1][head.index('Rank')]=''
        with other.raw_result.open('w',newline='',encoding='utf-8-sig') as h:csv.writer(h).writerows(rows)
        named=other.raw_result.with_name(f'{date} {preset} day {number}.csv');other.raw_result.rename(named)
        result=ai.import_folders(other.results,other.salaries,db_path=str(target.db),username='Winner');self.assertFalse(result['errors'],result)
        digest=target.query("SELECT hash FROM analysis_sources WHERE kind='results' AND original_name=?",(named.name,))[0][0]
        index_contest(target.db,digest);return digest
    def history(self,*,dates=('2026-09-01','2026-09-02','2026-09-03'),**kwargs):
        f=self.fixture()
        for i,date in enumerate(dates,1):self.day(f,date,i,**kwargs)
        return f
    def review(self,f,through='2026-09-04',start='2026-09-04'):
        return review_winners(f.db,through,review_mode='prior_qualified',followup_from=start)
    def test_earlier_cohort_includes_losing_later_portfolio_and_current_winner_peer(self):
        f=self.history();self.day(f,'2026-09-04',4,loses=True);before=logical_db(f.db);sources=source_bytes(f.root)
        r=self.review(f);self.assertEqual(len(r['contests']),1);p=r['contests'][0]['winners'][0]
        self.assertEqual(p['username'],'Winner');self.assertEqual(p['entries'],50);self.assertEqual(p['winning_entries'],0)
        self.assertFalse(p['current_top_cutoff']);self.assertEqual(p['peer_users'],1);self.assertEqual(p['peer_top_rate'],1)
        self.assertEqual(p['prior_record']['ranked_slates'],3);self.assertEqual(len(r['evidence']),4)
        self.assertIn('Earlier top-rank cutoff',render_winner(r['contests'][0],p));self.assertIn('later observed entry',render_overview(r))
        outcomes=followup_outcomes(r)['showdown / 150-Max'];self.assertEqual(outcomes['cohort_rate'],0);self.assertEqual(outcomes['peer_rate'],1)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
    def test_same_day_and_future_results_do_not_supply_eligibility(self):
        f=self.history(dates=('2026-09-01','2026-09-02','2026-09-04'));self.day(f,'2026-09-04',4);self.day(f,'2026-09-05',5)
        r=self.review(f,'2026-09-05');same_day=[c for c in r['contests'] if c['date']=='2026-09-04']
        self.assertEqual(len(same_day),2);self.assertTrue(all(c['winners']==[] for c in same_day))
    def test_multiple_contests_in_same_salary_slate_count_once(self):
        f=self.history(dates=('2026-09-01','2026-09-01','2026-09-02'));self.day(f,'2026-09-04',4)
        self.assertEqual(self.review(f)['contests'][0]['winners'],[])
    def test_prior_exact_entry_count_must_match(self):
        f=self.history();self.day(f,'2026-09-04',4,entries=49)
        self.assertEqual(self.review(f)['contests'][0]['winners'],[])
    def test_prior_entry_preset_must_match_and_unknown_preset_cannot_qualify(self):
        for preset in ('20 Max','unclassified'):
            with self.subTest(preset=preset):
                f=self.history(preset=preset);self.day(f,'2026-09-04',4)
                self.assertEqual(self.review(f)['contests'][0]['winners'],[])
    def test_unknown_prior_rank_withholds_ranked_slate_credit(self):
        f=self.fixture()
        for i in range(1,4):self.day(f,'2026-09-0'+str(i),i,unknown_user='Winner' if i==1 else None)
        self.day(f,'2026-09-04',4);self.assertEqual(self.review(f)['contests'][0]['winners'],[])
    def test_peer_must_have_same_prior_ranked_slate_count(self):
        f=self.fixture()
        for i in range(1,4):self.day(f,'2026-09-0'+str(i),i,unknown_user='Other' if i==1 else None)
        self.day(f,'2026-09-04',4,loses=True);p=self.review(f)['contests'][0]['winners'][0]
        self.assertEqual(p['peer_users'],0);self.assertIsNone(p['peer_top_rate'])
    def test_changed_prior_salary_source_cannot_supply_cohort_qualification(self):
        f=self.history();self.day(f,'2026-09-04',4)
        source=f.query("SELECT snapshot FROM analysis_sources WHERE hash=(SELECT salary_hash FROM opponent_contests WHERE end_date='2026-09-01')")[0][0]
        path=Path(source);path.write_bytes(path.read_bytes()+b'\n')
        r=self.review(f);self.assertEqual(r['contests'][0]['winners'],[]);self.assertEqual(len(r['excluded']),1)
    def test_prior_mapping_changed_during_review_aborts_publication(self):
        f=self.history();self.day(f,'2026-09-04',4)
        digest=f.query("SELECT result_hash FROM opponent_contests WHERE end_date='2026-09-01'")[0][0]
        def progress(text):
            if 'day 4.csv' in text:f.change('DELETE FROM analysis_salary_pairs WHERE result_hash=?',(digest,))
        with self.assertRaisesRegex(ValueError,'association changed'):
            review_winners(f.db,'2026-09-04',progress=progress,review_mode='prior_qualified',followup_from='2026-09-04')
    def test_partial_current_portfolio_is_retained_without_peer_comparison(self):
        f=self.history();self.day(f,'2026-09-04',4,loses=True)
        f.change("UPDATE opponent_entries SET metadata_complete=0 WHERE contest_key='dk:contest:day-4' AND entry_id='4000'")
        p=self.review(f)['contests'][0]['winners'][0]
        self.assertEqual(p['unknown'],1);self.assertEqual(p['entries'],50);self.assertEqual(p['peer_users'],0);self.assertIsNone(p['peer_top_rate'])
    def test_narrow_evaluation_and_history_bounds_abort_without_partial_promotion(self):
        f=self.history();self.day(f,'2026-09-04',4)
        for constant in ('MAX_FOLLOWUP_USERS','MAX_HISTORY_RECORDS','MAX_WINNER_ENTRIES'):
            with self.subTest(constant=constant),patch('winner_portfolio_review.'+constant,1):
                with self.assertRaises(ReviewLimitError):self.review(f)
    def test_invalid_evaluation_range_and_cancellation(self):
        f=self.fixture()
        with self.assertRaises(ValueError):self.review(f,start='2026-09-05')
        with self.assertRaises(ai.ImportCancelled):review_winners(f.db,'2026-09-04',cancelled=lambda:True,review_mode='prior_qualified',followup_from='2026-09-04')

    def test_classic_history_cannot_qualify_a_showdown_user(self):
        f=self.history(kind='classic');self.day(f,'2026-09-04',4)
        self.assertEqual(self.review(f)['contests'][0]['winners'],[])
    def test_missing_later_rank_remains_unknown_and_withholds_outcome_comparison(self):
        f=self.history();self.day(f,'2026-09-04',4,loses=True,unknown_user='Winner')
        p=self.review(f)['contests'][0]['winners'][0]
        self.assertEqual(p['entries'],50);self.assertIsNone(p['current_top_cutoff']);self.assertIsNone(p['peer_top_rate'])
    def test_later_outcomes_weight_independent_slates_equally(self):
        p=dict(current_top_cutoff=True,peer_top_rate=0)
        a=dict(format='showdown',preset='150-Max',slate=['showdown','2026-09-04',['A@B']],winners=[p]*10)
        b=dict(a,slate=['showdown','2026-09-05',['C@D']],winners=[dict(current_top_cutoff=False,peer_top_rate=1)])
        v=followup_outcomes(dict(contests=[a,a,b]))['showdown / 150-Max']
        self.assertEqual(v['cohort_rate'],.5);self.assertEqual(v['peer_rate'],.5);self.assertEqual(v['slates'],2)

if __name__=='__main__':unittest.main()

