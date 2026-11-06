from test_environment import install
install()
import copy,unittest
from single_entry_review import Duplication,KEYS
from duplication_construction import summarize,render


class DuplicationConstructionTests(unittest.TestCase):
    def pattern(self,value,key=0):
        p=[None]*len(KEYS);p[key]=value;return tuple(p)
    def contest(self,date='2026-09-01',size=10000,counts=((0,1),(1,3),(1,1))):
        d=Duplication()
        for i,(value,n) in enumerate(counts):
            for j in range(n):d.observe(str(i)+'-'+str(j),None,(('CPT',str(i)),),self.pattern(value))
        detail=d.finish()
        return dict(name='Synthetic contest',format='showdown',preset='Single Entry',date=date,slate=['showdown',date,['A@B']],field_size=size,accepted=detail['resolved_entries'],single_entry=dict(accepted=detail['resolved_entries'],duplication=detail))
    def test_roster_and_entry_weighted_means_have_different_denominators(self):
        c=self.contest();d=c['single_entry']['duplication'];absent,present=d['by_construction'][KEYS[0]]
        self.assertEqual(present['rosters'],2);self.assertEqual(present['entries'],4);self.assertEqual(present['shared_entries'],3)
        self.assertEqual(present['shared_entry_pct'],75);self.assertEqual(present['mean_copies_per_roster'],2)
        self.assertEqual(present['mean_copies_per_entry'],2.5);self.assertEqual(present['maximum_copies'],3)
        self.assertEqual(absent['mean_copies_per_entry'],1)
        self.assertEqual(d['resolved_entries'],5);self.assertEqual(d['extra_copies'],2)
    def test_same_signature_conflicting_patterns_abort_in_both_orders(self):
        for a,b in ((0,1),(1,0)):
            d=Duplication();d.observe('one',1,('roster',),self.pattern(a))
            with self.assertRaisesRegex(ValueError,'conflicting'):d.observe('two',1,('roster',),self.pattern(b))
        for first,last in ((None,self.pattern(1)),(self.pattern(1),None)):
            d=Duplication();d.observe('one',None,('roster',),first)
            with self.assertRaises(ValueError):d.observe('two',None,('roster',),last)
        for bad in ((1,),tuple([True]*len(KEYS)),tuple([2]*len(KEYS))):
            with self.assertRaises(ValueError):Duplication().observe('bad',1,('roster',),bad)
    def test_repeated_copies_cannot_supply_distinct_roster_support(self):
        c=self.contest(counts=((0,100),(1,100)))
        self.assertEqual(summarize({'contests':[c]},5),[])
        self.assertIn('no comparisons with this support',render({'contests':[c]}))
    def test_five_and_ten_roster_checks_are_separate_and_metric_specific(self):
        counts=tuple([(0,1)]*5+[(1,3)]*5);c=self.contest(counts=counts)
        rows=summarize({'contests':[c]},5);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['metric'],KEYS[0]);self.assertEqual(rows[0]['endpoints']['shared_entry_pct']['difference'],100)
        self.assertEqual(summarize({'contests':[c]},10),[])
        # Missing/not-applicable feature sides never borrow support from another metric.
        self.assertEqual(c['single_entry']['duplication']['by_construction'][KEYS[1]][0]['rosters'],0)
        with self.assertRaises(ValueError):summarize({'contests':[c]},True)
    def test_equal_game_weighting_field_scopes_and_existing_evidence_are_preserved(self):
        counts=tuple([(0,1)]*10+[(1,3)]*10)
        a=self.contest(counts=counts);b=self.contest(counts=counts);b['single_entry']['duplication']['by_construction'][KEYS[0]][1]['shared_entry_pct']=0
        c=self.contest('2026-09-02',counts=counts);unknown=self.contest('2026-09-03',None,counts)
        wrong=self.contest('2026-09-04',counts=counts);wrong['preset']='150-Max'
        report={'contests':[a,b,c,unknown,wrong]};before=copy.deepcopy(report)
        rows=summarize(report);known=next(r for r in rows if r['scope']=='10,000 or more')
        self.assertEqual(known['games'],2);self.assertEqual(known['endpoints']['shared_entry_pct']['difference'],75)
        self.assertEqual({r['scope'] for r in rows},{'10,000 or more','Unknown field size'})
        self.assertEqual(report,before)
    def test_thin_samples_hide_summary_differences_and_same_game_contests_do_not_add_games(self):
        counts=tuple([(0,1)]*10+[(1,3)]*10);c=self.contest(counts=counts)
        report={'contests':[copy.deepcopy(c) for _ in range(5)]}
        self.assertEqual(summarize(report)[0]['games'],1)
        text=render(report);self.assertIn('insufficient independent games',text);self.assertNotIn('present-minus-absent',text)
        for i,c in enumerate(report['contests'],1):c['date']='2026-09-0'+str(i);c['slate'][1]=c['date']
        row=summarize(report)[0];self.assertEqual(row['games'],5);self.assertEqual(row['status'],'supported descriptive sample')
        self.assertEqual(row['endpoints']['shared_entry_pct']['omit_one_range'],[100,100])
        self.assertIn('Shared-entry rate present-minus-absent +100.000 pp',render(report))
    def test_legacy_aggregate_copy_counts_do_not_create_construction_evidence(self):
        c=self.contest();c['single_entry']['duplication'].pop('by_construction')
        self.assertEqual(summarize({'contests':[c]}),[])
        self.assertIn('Aggregate copy counts cannot reconstruct',render({'contests':[c]}))
        d=Duplication();d.observe('one',None,('legacy',));self.assertIsNone(d.finish()['by_construction'])
