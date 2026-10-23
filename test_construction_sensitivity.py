"""Outlier visibility without changing matched cohorts, rates or model behavior."""
import copy
import unittest
from historical_contest_comparison import (field_comparison,field_report_lines,slate_sensitivity,sensitivity_text,captain_defense_report)
from test_historical_contest_comparison import contest
from test_captain_defense_comparison import source

FEATURE='Kicker/defense slots: 2'


class ConstructionSensitivityTests(unittest.TestCase):
    def test_exact_direction_counts_and_omit_one_range(self):
        s=slate_sensitivity([-10,0,20])
        self.assertEqual((s['blocks'],s['lower'],s['equal'],s['higher']),(3,1,1,1))
        self.assertEqual((s['omit_one_min'],s['omit_one_max']),(-5,10))
        self.assertIn('changes sign',sensitivity_text(dict(slate_sensitivity=s)))

    def test_positive_mean_can_hide_mostly_negative_blocks(self):
        gaps=[-10]*6+[90];s=slate_sensitivity(gaps)
        self.assertGreater(sum(gaps)/len(gaps),0)
        self.assertEqual((s['lower'],s['higher']),(6,1))
        self.assertEqual(s['omit_one_min'],-10)
        self.assertAlmostEqual(s['omit_one_max'],20/3)

    def test_none_one_and_equal_blocks_do_not_invent_omit_one_evidence(self):
        for gaps in ([],[5]):
            s=slate_sensitivity(gaps)
            self.assertIsNone(s['omit_one_min']);self.assertIsNone(s['omit_one_max'])
            self.assertIn('unavailable',sensitivity_text(dict(slate_sensitivity=s)))
        s=slate_sensitivity([0,0,0]);self.assertEqual(s['equal'],3)
        self.assertEqual(s['omit_one_min'],0);self.assertNotIn('changes sign',sensitivity_text(dict(slate_sensitivity=s)))

    def test_whole_overlap_blocks_are_removed_together_not_individual_contests(self):
        records=[contest('a',games=('A@B',),field=50,top1=30),
            contest('b',games=('A@B','C@D'),field=20,top1=60),
            contest('c',day='2026-09-28',field=30,top1=20)]
        before=copy.deepcopy(records);r=field_comparison(records,FEATURE,'top1')
        self.assertEqual(r['slates'],2);self.assertEqual(r['contests'],3)
        self.assertEqual(r['gap'],0)
        self.assertEqual(r['slate_sensitivity'],dict(blocks=2,lower=1,equal=0,higher=1,omit_one_min=-10,omit_one_max=10))
        self.assertEqual(records,before)
        self.assertEqual(r,field_comparison(list(reversed(records)),FEATURE,'top1'))

    def test_duplicate_conflicting_and_missing_cohorts_do_not_add_sensitivity_samples(self):
        a=contest('a');b=contest('b',day='2026-09-28');missing=contest('missing');missing['cohorts'].pop('top1')
        bad=contest('bad',day=None);old=contest('old');old['version']=1
        first=field_comparison([a,b,missing,bad,old],FEATURE,'top1')
        repeated=field_comparison([a,b,missing,bad,old,copy.deepcopy(a)],FEATURE,'top1')
        self.assertEqual(first['slate_sensitivity'],repeated['slate_sensitivity'])
        conflict=copy.deepcopy(a);conflict['cohorts']['field']['features'][FEATURE]=99
        r=field_comparison([a,conflict,b],FEATURE,'top1')
        self.assertEqual(r['slate_sensitivity']['blocks'],1)
        self.assertIsNone(r['slate_sensitivity']['omit_one_min'])

    def test_conditional_positions_use_their_own_matched_blocks(self):
        a=source('a');b=source('b',day='2026-09-28')
        b['cohorts']['top1']['features'].pop('Captain position: RB')
        qb=field_comparison([a,b],'','top1',captain_position='QB')
        rb=field_comparison([a,b],'','top1',captain_position='RB')
        self.assertEqual(qb['slate_sensitivity']['blocks'],2)
        self.assertEqual(rb['slate_sensitivity']['blocks'],1)
        self.assertIsNone(rb['slate_sensitivity']['omit_one_min'])

    def test_broad_and_conditional_reports_label_sensitivity_without_statistical_claims(self):
        for text in ('\n'.join(field_report_lines(dict(format='showdown',contests=[contest()]))),
                     '\n'.join(captain_defense_report(dict(format='showdown',contests=[source()])))):
            self.assertIn('Lower/equal/higher blocks; omit-one gap range (pp)',text)
            self.assertIn('not a confidence interval',text)
            self.assertIn('unavailable',text)
        self.assertEqual(captain_defense_report(dict(format='classic',contests=[source()])),[])

    def test_existing_source_row_rates_and_position_denominators_are_unchanged(self):
        a=source();r=field_comparison([a],'','top1',captain_position='QB')
        self.assertEqual((r['field'],r['comparator'],r['gap']),(50,60,10))
        self.assertEqual((r['field_rows'],r['comparator_rows']),(80,20))
        s=field_comparison([a],'Captain with opposing FLEX defense: yes','top1')
        self.assertEqual(s['gap'],-14)


if __name__=='__main__':unittest.main()
