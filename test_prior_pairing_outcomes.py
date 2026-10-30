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
