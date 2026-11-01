from test_environment import install
install()
import copy,csv,unittest
from unittest.mock import patch
from PyQt5 import QtWidgets
import analysis_imports as ai
from single_entry_review import Observer,features,KEYS,review,summarize,render,field_bucket
from winner_portfolio_review import render_overview,ReviewLimitError
from winner_portfolio_ui import WinnerWorker,WinnerPortfoliosDialog
from opponent_history import index_contest
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db,source_bytes


class SingleEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def fixture(self):
        f=SourceFixture();self.addCleanup(f.close);return f
    def day(self,target,day,*,preset='Single Entry',loses=False,duplicate_user=False,missing=False):
        def edit(rows):
            results('single-'+str(day))(rows);h=rows[0]
            for i,row in enumerate(rows[1:101]):
                row[h.index('EntryName')]='Winner' if i==0 or (duplicate_user and i==1) else 'Other'+str(i)
                row[h.index('Rank')]=str((51 if i==0 else 1 if i==1 else i+1) if loses else i+1)
                if missing and i==0:row[h.index('Rank')]=''
                row[h.index('SlateDate')]='2026-09-0'+str(day)
        f=SourceFixture(edit_results=edit);self.addCleanup(f.close)
        with f.raw_salary.open(newline='',encoding='utf-8-sig') as h:rows=list(csv.reader(h))
        for row in rows[1:]:row[6]=row[6].replace('09/21/2026','09/0'+str(day)+'/2026')
        with f.raw_salary.open('w',newline='',encoding='utf-8-sig') as h:csv.writer(h).writerows(rows)
        named=f.raw_result.with_name('2026-09-0'+str(day)+' '+preset+'.csv');f.raw_result.rename(named)
        imported=ai.import_folders(f.results,f.salaries,db_path=str(target.db),username='Winner');self.assertFalse(imported['errors'])
        digest=target.query('SELECT hash FROM analysis_sources WHERE original_name=?',(named.name,))[0][0]
        index_contest(target.db,digest);return digest
    def history(self,**kwargs):
        f=self.fixture()
        for day in (1,2,3):self.day(f,day,**kwargs)
        return f
    def run_review(self,f):return review(f.db,'2026-09-04',since='2026-09-01',followup_from='2026-09-04')
    def test_source_backed_earlier_selection_retains_later_loss_and_full_field(self):
        f=self.history();self.day(f,4,loses=True);before=logical_db(f.db);sources=source_bytes(f.root)
        r=self.run_review(f);self.assertEqual(r['excluded'],[]);self.assertEqual(len(r['evidence']),4)
        c=r['contests'][0];self.assertEqual(c['preset'],'Single Entry');self.assertEqual(c['single_entry']['eligible'],100)
        self.assertEqual([p['username'] for p in c['winners']],['Winner']);self.assertFalse(c['winners'][0]['current_top_cutoff'])
        self.assertEqual(c['single_entry']['cohort_eligible'],1);self.assertEqual(c['field_size'],100)
        d=WinnerPortfoliosDialog(f.db);self.addCleanup(d.close);d.load_result(r)
        parent=d.tree.topLevelItem(0);self.assertEqual(parent.text(2),'100');self.assertEqual(parent.text(3),'1')
        self.assertEqual(c['winners'][0]['winning_entries'],0)
        text=render_overview(r);self.assertIn('SINGLE ENTRY SHOWDOWN',text);self.assertIn('field usage',text)
        self.assertIn('Entry-fee matching, payouts/ROI and exact lineup duplication are unavailable',text)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
    def test_other_entry_limits_cannot_supply_history(self):
        f=self.history(preset='150 Max');self.day(f,4)
        r=self.run_review(f);self.assertEqual(len(r['evidence']),1);self.assertEqual(r['contests'][0]['winners'],[])
        self.assertEqual(r['contests'][0]['single_entry']['cohort_eligible'],0)
    def test_same_day_and_missing_rank_cannot_qualify(self):
        f=self.fixture()
        self.day(f,1);self.day(f,2,missing=True);self.day(f,3);self.day(f,4)
        self.assertEqual(self.run_review(f)['contests'][0]['winners'],[])
        # A same-day evaluation cannot be its own third earlier game.
        r=review(f.db,'2026-09-03',since='2026-09-01',followup_from='2026-09-03')
        self.assertEqual(r['contests'][0]['winners'],[])
    def test_single_entry_label_with_multiple_entries_is_excluded(self):
        f=self.history();self.day(f,4,duplicate_user=True)
        r=self.run_review(f);self.assertEqual(r['contests'],[])
        self.assertIn('exactly one indexed entry',r['excluded'][0]['reason'])
    def test_unresolved_and_missing_current_entries_not_losses(self):
        f=self.history();self.day(f,4,missing=True)
        f.change("UPDATE opponent_entries SET metadata_complete=0 WHERE contest_key='dk:contest:single-4' AND entry_id='1001'")
        r=self.run_review(f);d=r['contests'][0]['single_entry']
        self.assertEqual(d['eligible'],98);self.assertEqual(d['missing_ranks'],1);self.assertEqual(d['unknown_rosters'],1)
        self.assertEqual(d['cohort_eligible'],0)
    def test_changed_source_cancellation_and_bounds_do_not_publish(self):
        f=self.history();self.day(f,4)
        with patch('single_entry_review.MAX_REVIEW_ENTRIES',99):
            with self.assertRaises(ReviewLimitError):self.run_review(f)
        with self.assertRaises(ai.ImportCancelled):review(f.db,'2026-09-04',lambda:True,since='2026-09-01',followup_from='2026-09-04')
        source=f.query("SELECT snapshot FROM analysis_sources WHERE hash=(SELECT salary_hash FROM opponent_contests WHERE end_date='2026-09-01')")[0][0]
        from pathlib import Path
        path=Path(source);path.write_bytes(path.read_bytes()+b'\n')
        r=self.run_review(f);self.assertTrue(r['excluded']);self.assertEqual(r['contests'][0]['winners'],[])
    def test_index_change_after_scan_aborts(self):
        with patch('historical_series_review._inventory',side_effect=['before','after']),patch('winner_portfolio_review.review_winners',return_value={'partial':True}):
            with self.assertRaisesRegex(ValueError,'changed'):review('unused','2026-09-04')
    def test_features_cover_captain_pairs_kd_salary_and_team_split(self):
        def p(name,pos,team,salary):return dict(id=name,name=name,position=pos,team=team,salary=salary)
        slots=[('WR','CPT',p('WR','WR','A',10000)),('QB','FLEX',p('QB','QB','A',9000)),('DST','FLEX',p('DST','DST','B',4000)),('K','FLEX',p('K','K','B',4000)),('RB','FLEX',p('RB','RB','A',8000)),('TE','FLEX',p('TE','TE','A',5000))]
        v=dict(zip(KEYS,features(slots)))
        self.assertEqual((v['captain_WR'],v['own_qb'],v['opposing_dst'],v['kd_2'],v['split_4'],v['salary_1']),(1,1,1,1,1,1))
        slots[0][2]['position']='RB';self.assertIsNone(dict(zip(KEYS,features(slots)))['own_qb'])
    def test_observer_uses_contest_denominator_all_ties_and_never_infers_missing_ranks(self):
        o=Observer({'selected'},10000);pattern=(1,)*len(KEYS)
        for rank,user in ((1,'selected'),(1,'other'),(10,'other'),(100,'other'),(101,'other'),(None,'selected')):o.observe(str(rank),rank,pattern,user)
        o.observe('unknown',1,None,'selected');d=o.finish(7)
        self.assertEqual(d['field'][KEYS[0]][1],[5,4,3,2]);self.assertEqual(d['cohort'][KEYS[0]][1],[1,1,1,1])
        self.assertEqual(d['eligible'],5);self.assertEqual(d['missing_ranks'],1);self.assertEqual(d['unknown_rosters'],1)
        with self.assertRaises(ValueError):o.finish(8)
    def synthetic(self,day,size=10000):
        field={k:[[20,0,0,0],[20,4,2,1]] for k in KEYS};cohort={k:[[5,0,0,0],[5,2,1,1]] for k in KEYS}
        return dict(date=day,slate=['showdown',day,['A@B']],format='showdown',preset='Single Entry',field_size=size,single_entry=dict(field=field,cohort=cohort))
    def test_field_size_format_preset_and_game_weighting_stay_separate(self):
        a=self.synthetic('2026-09-01');b=copy.deepcopy(a);b['single_entry']['field'][KEYS[0]][1][1]=0
        report={'contests':[a,b,self.synthetic('2026-09-02'),self.synthetic('2026-09-03',100),self.synthetic('2026-09-04',None)]}
        wrong=self.synthetic('2026-09-05');wrong['preset']='150-Max';report['contests'].append(wrong)
        rows=summarize(report);v=next(r for r in rows if r['bucket']=='10,000 or more' and r['metric']==KEYS[0] and r['endpoint']=='top1')
        self.assertEqual(v['games'],2);self.assertAlmostEqual(v['difference_pp'],15)
        self.assertEqual({r['bucket'] for r in rows},{'10,000 or more','Under 1,000','Unknown field size'})
        self.assertEqual(field_bucket(1000),'1,000-9,999');self.assertEqual(field_bucket(9999),'1,000-9,999')
    def test_support_is_metric_specific_and_five_games_not_contests(self):
        report={'contests':[self.synthetic('2026-09-01') for _ in range(5)]}
        self.assertTrue(all(r['status']=='insufficient independent games' for r in summarize(report)))
        report={'contests':[self.synthetic('2026-09-0'+str(i)) for i in range(1,6)]}
        report['contests'][0]['single_entry']['field'][KEYS[0]][0][0]=9
        rows=summarize(report);v=next(r for r in rows if r['metric']==KEYS[0] and r['endpoint']=='top1')
        self.assertEqual(v['games'],4);self.assertEqual(v['status'],'insufficient independent games')
        self.assertTrue(any(r['games']==5 and r['status']=='supported descriptive sample' for r in rows))
        with self.assertRaises(ValueError):summarize(report,True)
        self.assertEqual(summarize({'contests':[dict(self.synthetic('2026-09-01'),single_entry=None)]}),[])
    def test_ui_mode_is_explicit_showdown_only_and_worker_routes(self):
        d=WinnerPortfoliosDialog('unused');self.addCleanup(d.close);d.kind.setCurrentIndex(d.kind.findData('classic'))
        d.mode.setCurrentIndex(d.mode.findData('single_entry'))
        self.assertEqual(d.kind.currentData(),'showdown');self.assertFalse(d.kind.isEnabled());self.assertIsNone(d._thread)
        worker=WinnerWorker('unused','2026-09-04','showdown','2026-09-01','single_entry','2026-09-04');seen=[];worker.result.connect(seen.append)
        with patch('single_entry_review.review',return_value={'done':True}) as run:worker.run()
        self.assertEqual(run.call_args.kwargs['followup_from'],'2026-09-04');self.assertEqual(seen,[{'done':True}])
        d.mode.setCurrentIndex(d.mode.findData('winners'));self.assertTrue(d.kind.isEnabled())

    def test_saved_modes_cannot_be_mixed_and_legacy_evidence_is_unavailable(self):
        from historical_series_review import combine_followups
        a=dict(review_mode='prior_qualified',since='2026-09-01',format='showdown',followup_from='2026-09-02',through='2026-09-02',contests=[],evidence=[],excluded=[],single_entry_analysis=True)
        b=dict(a,followup_from='2026-09-03',through='2026-09-03');b.pop('single_entry_analysis')
        with self.assertRaisesRegex(ValueError,'scopes'):combine_followups([a,b])
        self.assertIn('no verified Single Entry',render({'contests':[]}))
    def test_thin_samples_hide_means_in_display_and_aggregation_does_not_mutate(self):
        report={'contests':[self.synthetic('2026-09-01')]};before=copy.deepcopy(report)
        rows=summarize(report);self.assertTrue(rows);self.assertTrue(all(r['difference_pp'] is not None for r in rows))
        # Render a legacy record without a captured field audit instead of inventing evidence.
        legacy=dict(report['contests'][0]);legacy.pop('single_entry')
        self.assertIn('no verified Single Entry',render({'contests':[legacy]}))
        # Complete the display receipt for the synthetic audit.
        c=report['contests'][0];c.update(name='Synthetic',complete_field=False)
        c['single_entry'].update(eligible=40,accepted=50,unknown_rosters=5,missing_ranks=5,selected_users=10,cohort_eligible=10,rank_denominator=10000)
        text=render(report);self.assertIn('insufficient independent games',text);self.assertNotIn('+20.000 pp',text)
        self.assertNotIn('supported descriptive sample',text)
        self.assertEqual(summarize(before),rows)
