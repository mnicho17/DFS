from test_environment import install
install()
import unittest
from unittest.mock import patch
from contextlib import closing
from construction_outcomes import contrast,contest_outcomes,review_outcomes,render,summarize_outcomes,METRICS
from portfolio_pattern_audit import PatternAuditLimit
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

    def grouping_fixture(self):
        import copy
        from test_hindsight_solver import athletes
        from test_hindsight_evidence import SourceFixture
        from test_opponent_history import results
        from opponent_history import index_contest
        pool=athletes()
        for p,pos in zip(pool,['QB','QB','K','K','DST','DST','WR','RB']):p['position']=pos
        for name in ('I','J'):
            n=len(pool);p=copy.deepcopy(pool[6]);p.update(key=str(100+n),name=name)
            p['roles']['FLEX']['id']=str(100+n);p['roles']['CPT']['id']=str(1100+n);pool.append(p)
        # With one QB, both construction groups finish first; with two QBs,
        # neither does. The kicker groups have different QB composition.
        def edit(rows):
            results()(rows);h=rows[0]
            for n,row in enumerate(rows[1:101]):
                lineup=('G A C D E H' if n<45 else 'G A C E H I' if n<50 else 'G A B C D H' if n<55 else 'G A B C H I').split()
                row[h.index('Lineup')]='CPT '+lineup[0]+' '+' '.join('FLEX '+x for x in lineup[1:])
                row[h.index('Rank')]='1' if n<50 else '51'
        f=SourceFixture(pool=pool,edit_results=edit);self.addCleanup(f.close);index_contest(f.db,f.source['hash']);return f
    def test_other_grouping_conditions_remove_kicker_qb_composition_confound(self):
        f=self.grouping_fixture();r=self.calculate(f)
        self.assertAlmostEqual(r['matched']['two_kickers_pct']['difference_pp']['rank1'],80)
        fixed=r['other_rules_fixed']['two_kickers_pct']
        self.assertEqual(fixed['strata'],2);self.assertEqual(fixed['entries'],100)
        self.assertAlmostEqual(fixed['difference_pp']['rank1'],0)
        self.assertEqual(r['other_rules_fixed']['two_quarterbacks_pct']['difference_pp']['rank1'],-100)
    def test_missing_conditional_overlap_cannot_borrow_primary_support(self):
        f=self.grouping_fixture()
        f.change("UPDATE opponent_entry_slots SET original_label='G (1101)' WHERE entry_id='1045' AND role='CPT'")
        r=self.calculate(f)
        self.assertEqual(r['unknown'],1)
        self.assertEqual(r['matched']['two_kickers_pct']['entries'],99)
        self.assertEqual(r['other_rules_fixed']['two_kickers_pct']['entries'],50)
        self.assertEqual(r['other_rules_fixed']['two_kickers_pct']['strata'],1)
        self.assertEqual(r['other_rules_fixed']['two_defenses_pct']['difference_pp']['top1'],None)
    def test_features_include_two_defenses_without_a_quarterback(self):
        from construction_outcomes import outcome_features
        from portfolio_pattern_audit import features,METRICS as PEER_METRICS
        def slot(i,pos,team):return ('x','CPT' if i==0 else 'FLEX',dict(position=pos,team=team))
        slots=[slot(i,pos,team) for i,(pos,team) in enumerate([('WR','A'),('K','A'),('K','B'),('DST','A'),('DST','B'),('RB','B')])]
        value=outcome_features(slots)
        self.assertEqual(value[:5],features(slots));self.assertEqual(value[3],0)
        self.assertEqual(value[5:],(1,1,0));self.assertEqual(len(PEER_METRICS),5)
    def test_conditional_cell_budget_aborts_whole_report(self):
        f=self.grouping_fixture()
        with patch('construction_outcomes.MAX_CELLS',0):
            with self.assertRaisesRegex(PatternAuditLimit,'cell bound'):review_outcomes(f.db)
    def test_older_reports_have_unknown_conditional_support(self):
        row={m:dict(strata=1,difference_pp={e:1 for e in ('top1','top5','rank1')}) for m in METRICS}
        r=summarize_outcomes([dict(game=['date','game'],matched=row)])
        fixed=r['two_kickers_pct']['other_rules_fixed']
        self.assertEqual(fixed['games'],0);self.assertIsNone(fixed['endpoints']['top1']['difference_pp'])
    def test_game_direction_counts_and_rows_preserve_no_overlap(self):
        def contest(game,value,supported=True):
            return dict(game=['date',game],matched={m:dict(strata=int(supported),difference_pp={e:value for e in ('top1','top5','rank1')}) for m in METRICS})
        r=summarize_outcomes([contest('A',2),contest('A',0),contest('B',-2),contest('C',0),contest('D',100,False)])['two_kickers_pct']
        endpoint=r['endpoints']['top1']
        self.assertEqual((endpoint['higher_games'],endpoint['lower_games'],endpoint['equal_games']),(1,1,1))
        self.assertEqual(r['games'],3);self.assertEqual(len(r['game_differences']),3)
        self.assertAlmostEqual(endpoint['difference_pp'],-1/3)
    def test_render_identifies_primary_and_conditional_support(self):
        f=self.grouping_fixture();r=review_outcomes(f.db);text=render(r)
        self.assertIn('Two or more kickers',text);self.assertIn('Other three grouping conditions also fixed',text)
        self.assertIn('higher/lower/equal',text);self.assertIn('Other grouping flags fixed',text)

    def test_stricter_support_can_reverse_primary_association(self):
        cells={('small',0):[[5,0,0,0],[5,5,5,5]],('large',0):[[10,2,2,2],[10,0,0,0]]}
        loose=contrast(cells,5)[METRICS[0]];strict=contrast(cells,10)[METRICS[0]]
        self.assertAlmostEqual(loose['difference_pp']['top1'],20)
        self.assertAlmostEqual(strict['difference_pp']['top1'],-20)
        self.assertEqual((loose['entries'],strict['entries']),(30,20))
    def test_ten_entry_conditional_support_cannot_borrow_primary_overlap(self):
        f=self.grouping_fixture();r=self.calculate(f)
        self.assertEqual(r['matched'],r['support_checks']['5']['matched'])
        self.assertEqual(r['other_rules_fixed'],r['support_checks']['5']['other_rules_fixed'])
        five=r['support_checks']['5']['other_rules_fixed']['two_kickers_pct']
        ten=r['support_checks']['10']['other_rules_fixed']['two_kickers_pct']
        self.assertEqual(five['strata'],2);self.assertEqual(ten['strata'],0)
        self.assertIsNone(ten['difference_pp']['top1'])
        self.assertEqual(r['support_checks']['10']['matched']['two_kickers_pct']['entries'],100)
    def test_support_checks_share_one_verified_slot_scan(self):
        f=self.grouping_fixture();key=f.query('SELECT contest_key FROM opponent_contests')[0][0];queries=[]
        with closing(_connect(f.db,True)) as c:
            c.set_trace_callback(queries.append)
            contest_outcomes(c,key,f.salary['manifest']['players'],100,100,'',lambda:False)
        self.assertEqual(sum('FROM opponent_entry_slots ' in q for q in queries),1)
    def test_missing_rank_does_not_fill_stricter_support(self):
        f=self.grouping_fixture();f.change("UPDATE opponent_entries SET rank=NULL WHERE CAST(entry_id AS INTEGER)<1041")
        r=self.calculate(f)
        self.assertEqual(r['missing_rank'],41);self.assertEqual(r['unknown'],0)
        self.assertEqual(r['support_checks']['5']['matched']['two_kickers_pct']['entries'],59)
        self.assertEqual(r['support_checks']['10']['matched']['two_kickers_pct']['strata'],0)
        self.assertIsNone(r['support_checks']['10']['matched']['two_kickers_pct']['difference_pp']['rank1'])
    def test_missing_older_support_checks_are_unknown_not_inferred(self):
        row={m:dict(strata=1,difference_pp={e:1 for e in ('top1','top5','rank1')}) for m in METRICS}
        report=summarize_outcomes([dict(game=['date','game'],matched=row,other_rules_fixed=row)])
        metric=report['two_kickers_pct']
        self.assertEqual(metric['games'],1)
        for n in ('5','10'):
            for mode,value in metric['support_checks'][n].items():
                self.assertEqual(value['games'],0);self.assertIsNone(value['endpoints']['top1']['difference_pp'])
    def test_fixed_threshold_summaries_keep_metric_and_mode_support_separate(self):
        def rows(strata,value):return {m:dict(strata=strata,difference_pp={e:value for e in ('top1','top5','rank1')}) for m in METRICS}
        contest=dict(game=['d','A'],matched=rows(1,2),support_checks={'5':dict(matched=rows(1,2),other_rules_fixed=rows(1,1)),'10':dict(matched=rows(1,-2),other_rules_fixed=rows(0,99))})
        del contest['support_checks']['10']['matched']['two_defenses_pct']
        r=summarize_outcomes([contest])
        self.assertEqual(r['two_kickers_pct']['support_checks']['10']['matched']['endpoints']['top1']['difference_pp'],-2)
        self.assertEqual(r['two_kickers_pct']['support_checks']['10']['other_rules_fixed']['games'],0)
        self.assertEqual(r['two_defenses_pct']['support_checks']['10']['matched']['games'],0)
        self.assertEqual(r['two_kickers_pct']['support_checks']['5']['other_rules_fixed']['endpoints']['top1']['difference_pp'],1)
    def test_render_explains_entry_support_not_independent_users(self):
        f=self.grouping_fixture();r=review_outcomes(f.db);text=render(r)
        self.assertIn('10 entries per side',text);self.assertIn('not independent users',text)
        self.assertIn('10 per side / other_rules_fixed',text)

if __name__=='__main__':unittest.main()
