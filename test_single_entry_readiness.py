from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
import test_single_entry_review as fixtures
import test_duplication_construction as copies
from single_entry_readiness import render
from single_entry_review import KEYS,summarize


class SingleEntryReadinessTests(unittest.TestCase):
    def contest(self,day='2026-09-01'):
        c=fixtures.SingleEntryTests.synthetic(self,day)
        c['single_entry'].update(eligible=40,accepted=42,unknown_rosters=2,missing_ranks=0,selected_users=0,cohort_eligible=0)
        return c
    def test_same_game_contests_do_not_inflate_support_or_change_evidence(self):
        c=self.contest();report={'contests':[c,copy.deepcopy(c),self.contest('2026-09-02')]};before=copy.deepcopy(report)
        with patch('builtins.open',side_effect=AssertionError('No source access')):text=render(report)
        self.assertIn('3/3 contests; 2 distinct salary games',text)
        self.assertIn('120/126',text);self.assertIn('0 selected',text)
        self.assertIn('No earlier-qualified entrants',text)
        self.assertIn('maximum observed support 2 games',text)
        self.assertEqual(report,before)
    def test_missing_and_legacy_evidence_is_unavailable(self):
        c=self.contest();c.pop('single_entry')
        self.assertIn('Unavailable: no captured',render({'contests':[c]}))
        c['single_entry']={};text=render({'contests':[c]})
        self.assertIn('Entry coverage totals unavailable',text)
        self.assertIn('Earlier-cohort coverage unavailable',text)
        self.assertIn('not captured 1',text)
        self.assertNotIn('No earlier-qualified entrants',text)
    def test_only_matching_single_entry_game_identity_establishes_support(self):
        wrong=self.contest();wrong['preset']='150-Max'
        invalid=self.contest();invalid['slate'][1]='2026-10-01'
        text=render({'contests':[wrong,invalid]})
        self.assertIn('1/1 contests; 0 distinct salary games',text)
        self.assertIn('Game identity unavailable',text)
        self.assertIn('maximum observed support 0 games',text)
    def test_payout_states_are_distinct_and_never_returns(self):
        states=['missing_columns','ambiguous_columns','columns_present_values_unverified','unavailable','future_state',None]
        contests=[]
        for state in states:
            c=self.contest()
            if state is not None:c['single_entry']['cash_evidence']={'state':state}
            contests.append(c)
        text=render({'contests':contests})
        for phrase in ('missing columns 1','ambiguous columns 1','columns present, values unverified 1','unavailable 2','not captured 1','No returns calculated'):self.assertIn(phrase,text)
    def test_selected_but_unresolved_cohort_is_distinct_from_no_selection(self):
        c=self.contest();c['single_entry']['selected_users']=1
        text=render({'contests':[c]})
        self.assertIn('lack ranked/resolved coverage',text)
        self.assertNotIn('No earlier-qualified entrants',text)
    def test_existing_metric_support_thresholds_and_scope_are_preserved(self):
        report={'contests':[]}
        for i in range(1,6):
            c=self.contest('2026-09-0'+str(i))
            d=copies.DuplicationConstructionTests.contest(copies.DuplicationConstructionTests(),c['date'],counts=tuple([(0,1)]*10+[(1,3)]*10))
            c['single_entry']['duplication']=d['single_entry']['duplication'];report['contests'].append(c)
        rows=summarize(report);text=render(report)
        self.assertIn(f'{len(rows)}/{len(rows)} comparisons reach 5 supported games',text)
        self.assertIn('Duplication (10 distinct rosters per side): 1/1',text)
        self.assertIn('not a significance test or a rule recommendation',text)
        report['contests'].pop()
        self.assertIn('Duplication (10 distinct rosters per side): 0/1',render(report))
