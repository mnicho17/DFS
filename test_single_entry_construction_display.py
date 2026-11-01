from test_environment import install
install()
import copy,unittest
from unittest.mock import patch
from single_entry_review import Observer,KEYS
from single_entry_construction_display import render


class SingleEntryConstructionDisplayTests(unittest.TestCase):
    def test_prevalence_uses_finish_group_denominators_and_retains_ties(self):
        o=Observer([],1000)
        for eid,rank,present in (('a',1,1),('b',1,0),('c',5,1),('d',50,0)):
            o.observe(eid,rank,tuple(present for _ in KEYS))
        text=render(o.finish(4))
        row=next(line for line in text.splitlines() if line.startswith('Captain QB:'))
        self.assertEqual(row,'Captain QB: Field 2/4 (50.0%) | Top 1% 2/3 (66.7%) | Top 0.1% 1/2 (50.0%) | Rank one 1/2 (50.0%)')
        self.assertIn('not the chance a construction wins',text)
        self.assertIn('all rank-one ties remain entries',text.lower())
    def test_receiver_only_denominators_and_unresolved_entries_are_preserved(self):
        o=Observer([],1000);pattern=[0]*len(KEYS);pattern[KEYS.index('own_qb')]=None
        o.observe('qb',1,pattern);pattern[KEYS.index('own_qb')]=1
        o.observe('wr',1,pattern);o.observe('unknown',1,None);o.observe('unranked',None,pattern)
        d=o.finish(4);text=render(d)
        row=next(line for line in text.splitlines() if line.startswith('WR/TE Captain with own QB:'))
        self.assertIn('Field 1/1 (100.0%)',row);self.assertIn('Rank one 1/1 (100.0%)',row)
        self.assertEqual(d['unknown_rosters'],1);self.assertEqual(d['missing_ranks'],1)
    def test_zero_eligible_group_is_unavailable_not_zero_percent(self):
        o=Observer([],1000);o.observe('loser',50,tuple(0 for _ in KEYS))
        row=render(o.finish(1)).splitlines()[4]
        self.assertIn('Field 0/1 (0.0%)',row)
        self.assertIn('Rank one unavailable (0 eligible entries)',row)
    def test_missing_or_invalid_legacy_metrics_never_become_zero_counts(self):
        for sides in (None,[],[[1,0,0,0]],[[True,0,0,0],[1,0,0,0]],[[1,2,0,0],[1,0,0,0]],[[1,0,0,-1],[1,0,0,0]]):
            self.assertIn('Captain QB: unavailable in this capture.',render({'field':{'captain_QB':sides}}))
        self.assertIn('Captain QB: unavailable in this capture.',render(None))
    def test_render_is_read_only_and_does_not_reopen_sources(self):
        o=Observer([],100);o.observe('one',1,tuple(1 for _ in KEYS));d=o.finish(1);before=copy.deepcopy(d)
        with patch('builtins.open',side_effect=AssertionError('No source access')):text=render(d)
        self.assertEqual(d,before);self.assertIn('no payout or return is inferred',text)
