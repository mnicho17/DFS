from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
from prior_pairing_outcomes import WithinUser,summarize,render
from portfolio_pattern_audit import PatternAuditLimit


def slots(qb=True,captain='1',salary=7000):
    positions=['WR','QB' if qb else 'RB','WR','RB','TE','WR']
    return [(str(i),'CPT' if i==0 else 'FLEX',dict(id=captain if i==0 else str(i+10),position=p,team='A' if i<3 else 'B',salary=salary)) for i,p in enumerate(positions)]


class PriorPairingOutcomeTests(unittest.TestCase):
    def test_contest_denominator_support_and_rank_ties(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),11)
        v=a.finish(True)['support_checks']['10']['receiver_captain_same_qb']
        self.assertEqual(v['strata'],1);self.assertEqual(v['difference_pp'],dict(top1=100,top5=0,rank1=100))
        self.assertEqual(v['entries'],20)

    def test_exact_captain_salary_and_fixed_support_are_required(self):
        for changed in (slots(False,captain='2'),slots(False,salary=8000)):
            a=WithinUser(1000)
            for _ in range(10):a.add(slots(),1);a.add(changed,20)
            self.assertEqual(a.finish(True)['support_checks']['5']['receiver_captain_same_qb']['strata'],0)
        a=WithinUser(1000)
        for _ in range(5):a.add(slots(),1);a.add(slots(False),20)
        self.assertEqual(a.finish(True)['support_checks']['5']['receiver_captain_same_qb']['strata'],1)
        self.assertEqual(a.finish(True)['support_checks']['10']['receiver_captain_same_qb']['strata'],0)

    def test_unknown_portfolio_and_missing_ranks_do_not_become_losses(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),None)
        self.assertEqual(a.finish(True)['support_checks']['5']['receiver_captain_same_qb']['strata'],0)
        for _ in range(10):a.add(slots(False),20)
        self.assertFalse(a.finish(False)['complete'])
        self.assertEqual(a.finish(False)['support_checks']['5']['receiver_captain_same_qb']['strata'],0)

    def test_cell_bound_aborts_and_invalid_denominators_fail(self):
        with patch('prior_pairing_outcomes.MAX_CELLS',0):
            with self.assertRaises(PatternAuditLimit):WithinUser(100).add(slots(),1)
        for n in (0,True,None):
            with self.assertRaises(ValueError):WithinUser(n)

    def test_legacy_reports_are_unavailable_and_equal_slate_weighting(self):
        r=dict(review_mode='prior_qualified',contests=[])
        self.assertEqual(summarize(r),{});self.assertIn('Unavailable',render(r))
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),20)
        p=dict(pairing_outcomes=a.finish(True))
        c=dict(format='showdown',preset='150-Max',date='2026-10-01',slate=['showdown','2026-10-01',['A@B']],winners=[p]*10)
        other=copy.deepcopy(c);other.update(date='2026-10-02',slate=['showdown','2026-10-02',['C@D']])
        for v in other['winners'][0]['pairing_outcomes']['support_checks']['10'].values():
            if v['strata']:v['difference_pp']={e:0 for e in v['difference_pp']}
        r['contests']=[c,c,other]
        v=summarize(r)['showdown / 150-Max']['receiver_captain_same_qb']
        self.assertEqual(v['slates'],2);self.assertEqual(v['endpoints']['top1']['difference_pp'],50)
        r['review_mode']='winners'
        with self.assertRaises(ValueError):summarize(r)

    def test_fixed_flags_preserve_supported_primary_contrast(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),20)
        v=a.finish(True)
        self.assertEqual(v['support_checks'],v['other_groups_fixed'])

    def test_each_changed_group_flag_cannot_borrow_primary_support(self):
        from showdown_group_review import flags
        for flag in ('kickers','defenses','quarterbacks','qb_dst'):
            with self.subTest(flag=flag):
                row=slots()
                if flag in ('kickers','defenses'):
                    for i in (3,4):row[i][2]['position']='K' if flag=='kickers' else 'DST'
                    if flag=='defenses':row[3][2]['team']=row[4][2]['team']='A'
                elif flag=='quarterbacks':row[3][2]['position']='QB'
                else:row[3][2]['position']='DST'
                self.assertTrue(flags(row)[flag]);self.assertFalse(flags(slots(False))[flag])
                a=WithinUser(1000)
                for _ in range(10):a.add(row,1);a.add(slots(False),20)
                v=a.finish(True)
                self.assertEqual(v['support_checks']['10']['receiver_captain_same_qb']['strata'],1)
                self.assertEqual(v['other_groups_fixed']['10']['receiver_captain_same_qb']['strata'],0)

    def test_conditional_strata_do_not_pool_toward_minimum_support(self):
        a=WithinUser(1000)
        for q in (True,False):
            for double in (True,False):
                row=slots(q)
                if double:
                    for i in (3,4):row[i][2]['position']='K'
                for _ in range(5):a.add(row,1 if q else 20)
        v=a.finish(True)
        self.assertEqual(v['support_checks']['10']['receiver_captain_same_qb']['strata'],1)
        self.assertEqual(v['other_groups_fixed']['10']['receiver_captain_same_qb']['strata'],0)
        self.assertEqual(v['other_groups_fixed']['5']['receiver_captain_same_qb']['strata'],2)
        self.assertTrue(all(not v['strata'] for v in a.finish(False)['other_groups_fixed']['5'].values()))

    def test_old_outcome_reports_cannot_claim_fixed_group_evidence(self):
        a=WithinUser(1000)
        for _ in range(10):a.add(slots(),1);a.add(slots(False),20)
        d=a.finish(True);d.pop('other_groups_fixed')
        r=dict(review_mode='prior_qualified',contests=[dict(format='showdown',preset='150-Max',date='2026-10-01',slate=['showdown','2026-10-01',['A@B']],winners=[dict(pairing_outcomes=d)])])
        self.assertTrue(summarize(r));self.assertEqual(summarize(r,other_groups_fixed=True),{})
        self.assertIn('Unavailable',render(r))

    def test_three_specialists_cannot_match_zero_under_all_fixed_flags(self):
        a=WithinUser(1000);row=slots()
        for i,pos in ((3,'K'),(4,'K'),(5,'DST')):row[i][2]['position']=pos
        for _ in range(10):a.add(row,1);a.add(slots(),20)
        d=a.finish(True)
        self.assertEqual(d['support_checks']['10']['kd_three_plus_vs_zero']['strata'],1)
        self.assertEqual(d['other_groups_fixed']['10']['kd_three_plus_vs_zero']['strata'],0)
