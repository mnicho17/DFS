"""All-field comparisons must not depend on personal participation or pool games."""
import copy
import itertools
import unittest
from historical_contest_comparison import field_comparison,field_report_lines
from test_historical_contest_comparison import contest

FEATURE='Kicker/defense slots: 2'


class FieldCohortComparisonTests(unittest.TestCase):
    def test_no_personal_entries_still_compare_field_and_top_cohort(self):
        c=contest(field=20,top1=60);c['cohorts'].pop('yours')
        result=field_comparison([c],FEATURE,'top1')
        self.assertEqual((result['field'],result['comparator'],result['gap']),(20,60,40))
        self.assertEqual((result['contests'],result['slates']), (1,1))

    def test_large_and_repeated_contests_do_not_dominate_slate_weight(self):
        records=[contest('large',field=100,top1=0,n=1000000),
                 contest('same',field=0,top1=100,n=10),
                 contest('next',day='2026-09-28',field=0,top1=100,n=10)]
        r=field_comparison(records,FEATURE,'top1')
        self.assertEqual((r['field'],r['comparator'],r['gap']),(25,75,50))
        self.assertEqual((r['field_rows'],r['comparator_rows']),(1000020,1000020))
        repeated=field_comparison(records+[copy.deepcopy(records[0])],FEATURE,'top1')
        self.assertEqual(repeated['gap'],r['gap']);self.assertEqual(repeated['contests'],3)
        self.assertEqual(repeated['repeated_source_copies'],1)

    def test_cohorts_share_contests_and_keep_separate_coverage(self):
        a=contest('a',field=20,top1=60);b=contest('b',field=100,top1=0)
        b['cohorts'].pop('top1');b['cohorts']['top5']=copy.deepcopy(b['cohorts']['field'])
        first=field_comparison([a,b],FEATURE,'top1');second=field_comparison([a,b],FEATURE,'top5')
        self.assertEqual(first['gap'],40);self.assertEqual(first['contests'],1)
        self.assertEqual(second['gap'],0);self.assertEqual(second['contests'],1)
        self.assertEqual(first['excluded_cohort'],1)
        self.assertIsNone(field_comparison([a,b],FEATURE,'rank1')['gap'])

    def test_empty_mapped_cohort_is_unknown_but_absent_feature_is_true_zero(self):
        a=contest();a['cohorts']['top1']['features']={}
        self.assertEqual(field_comparison([a],FEATURE,'top1')['comparator'],0)
        a['cohorts']['top1']['counts']['supported_rows']=0
        self.assertIsNone(field_comparison([a],FEATURE,'top1')['comparator'])
        self.assertEqual(field_comparison([a],FEATURE,'top1')['excluded_cohort'],1)

    def test_legacy_missing_source_date_and_game_stay_outside_denominators(self):
        records=[contest('good'),contest('legacy'),contest('date',day=None),contest('game',games=()),contest('')]
        records[1]['version']=1
        r=field_comparison(records,FEATURE,'top1')
        self.assertEqual((r['contests'],r['slates'],r['excluded_legacy'],r['excluded_identity']),(1,1,1,3))
        self.assertEqual(r['field_rows'],100)

    def test_conflicting_same_source_receipts_are_withheld_in_both_orders(self):
        a=contest('conflict',field=20);b=contest('conflict',field=90);c=contest('safe')
        first=field_comparison([a,b,c],FEATURE,'top1')
        self.assertEqual(first,field_comparison([c,b,a],FEATURE,'top1'))
        self.assertEqual(first['excluded_conflicting_sources'],1);self.assertEqual(first['contests'],1)

    def test_overlapping_games_merge_transitively_without_input_mutation(self):
        records=[contest('a',games=('A@B','C@D')),contest('b',games=('E@F',)),contest('bridge',games=('C@D','E@F'))]
        before=copy.deepcopy(records)
        expected=field_comparison(records,FEATURE,'top1')
        for sequence in itertools.permutations(records):
            self.assertEqual(field_comparison(sequence,FEATURE,'top1'),expected)
        self.assertEqual(expected['slates'],1);self.assertEqual(records,before)

    def test_report_has_no_personal_dependency_and_discloses_scope_counts_and_ties(self):
        a=contest();a['cohorts'].pop('yours')
        text='\n'.join(field_report_lines(dict(format='showdown',contests=[a])))
        for phrase in ('contests you did not enter','same contests','Rank cutoff 1%','Rank cutoff 5%',
                       'Rank 1','include ties','duplicate EntryIds','not a claim of statistical independence',
                       'Excluded identity / cohort / legacy / conflicting source','No generation limits'):
            self.assertIn(phrase,text)
        self.assertIn('Captain with opposing FLEX defense: yes',text)
        self.assertNotIn('user-assumed lock',text)

    def test_existing_report_includes_all_field_comparison_without_personal_context(self):
        from historical_contest_comparison import report_lines
        a=contest();a['cohorts'].pop('yours');before=copy.deepcopy(a)
        text='\n'.join(report_lines(dict(format='showdown',contests=[a])))
        self.assertIn('Field versus top-finisher construction',text)
        self.assertNotIn('- Context:',text)
        self.assertEqual(a,before)
        with self.assertRaises(ValueError):field_comparison([a],FEATURE,'yours')

    def test_classic_has_own_features_and_empty_input_stays_unavailable(self):
        text='\n'.join(field_report_lines(dict(format='classic',contests=[contest()])))
        self.assertIn('QB same-team WR/TE:',text);self.assertNotIn('| Captain position:',text)
        self.assertEqual(field_report_lines(dict(format='showdown',contests=[])),[])
        r=field_comparison([],FEATURE,'top1')
        self.assertIsNone(r['gap']);self.assertEqual(r['field_rows'],0)


if __name__=='__main__':unittest.main()
