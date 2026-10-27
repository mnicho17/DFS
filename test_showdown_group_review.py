from test_environment import install
install()
import copy,json,unittest
from pathlib import Path
import analysis_imports as ai
from opponent_history import index_contest
from test_hindsight_evidence import SourceFixture
from test_opponent_history import results
from test_portfolio_risk_evidence import logical_db,source_bytes
from showdown_group_review import review_history,flags,summarize,render


def edited(duplicate=False):
    def edit(rows):
        results(duplicate=duplicate)(rows)
        head=rows[0]
        for row in rows[1:101]:
            indexes=[1,2,3,4,5,6] if int(row[head.index('Rank')])<=5 else [0,1,2,3,5,7]
            row[head.index('Lineup')]=' '.join(role+' '+list('ABCDEFGH')[i] for role,i in zip(['CPT']+['FLEX']*5,indexes))
        if duplicate:rows[101]=list(rows[1])
    return edit

class GroupReviewTests(unittest.TestCase):
    def fixture(self,duplicate=False):
        f=SourceFixture(edit_results=edited(duplicate));self.addCleanup(f.close)
        index_contest(f.db,f.source['hash']);return f
    def test_verified_entry_weighted_cohorts_and_joint_rules(self):
        f=self.fixture(duplicate=True);before=logical_db(f.db);sources=source_bytes(f.root)
        report=review_history(f.db,username='Winner');row=report['summary']['quarterbacks']
        self.assertEqual(row['field'],{'count':95,'denominator':100,'pct':95})
        self.assertEqual(row['top1']['count'],0);self.assertEqual(row['top5']['denominator'],5)
        self.assertEqual(report['summary']['all_four']['top5']['count'],5)
        self.assertEqual(row['rank1']['denominator'],1)
        self.assertEqual(report['summary']['all_four']['user']['count'],5)
        self.assertEqual(before,logical_db(f.db));self.assertEqual(sources,source_bytes(f.root))
        self.assertIn('not ROI or causal',render(report))
    def test_missing_metadata_does_not_borrow_a_readable_group(self):
        f=self.fixture();f.change('UPDATE opponent_entries SET metadata_complete=0 WHERE entry_id=?',('1000',))
        report=review_history(f.db)
        self.assertEqual(report['contests'][0]['unknown'],1)
        self.assertEqual(report['summary']['all_four']['top1']['denominator'],0)
        self.assertEqual(report['summary']['all_four']['field']['denominator'],99)
    def test_stale_sources_and_unresolved_pairs_are_excluded(self):
        f=self.fixture();Path(f.salary['snapshot']).write_bytes(Path(f.salary['snapshot']).read_bytes()+b'\n')
        report=review_history(f.db)
        self.assertEqual(report['contests'],[]);self.assertEqual(len(report['excluded']),1)
    def test_cancellation_is_not_a_partial_report(self):
        f=self.fixture()
        with self.assertRaises(ai.ImportCancelled):review_history(f.db,cancelled=lambda:True)
    def test_flags_include_captain_and_preserve_same_team_qb_dst(self):
        def slots(kinds):return [(p[0],'CPT' if i==0 else 'FLEX',{'position':p[0],'team':p[1]}) for i,p in enumerate(kinds)]
        value=flags(slots([('QB','A'),('DST','A'),('K','A'),('WR','B'),('RB','B'),('TE','A')]))
        self.assertFalse(value['qb_dst']);self.assertTrue(value['all_four'])
        value=flags(slots([('QB','A'),('DST','B'),('K','A'),('K','B'),('QB','B'),('DST','A')]))
        self.assertTrue(all(value[k] for k in ('qb_dst','kickers','quarterbacks','defenses')))
        self.assertFalse(value['all_four'])
    def test_slate_weights_do_not_follow_contest_size(self):
        def contest(game,n,top):
            cohorts={k:{'known':n,'features':{'kickers':0}} for k in ('field','top1','top5','rank1','user')}
            cohorts['top1']={'known':n,'features':{'kickers':top}}
            return {'game':[game,'A@B'],'cohorts':cohorts}
        report=summarize([contest('one',10000,10000),contest('two',10,0)])
        self.assertEqual(report['kickers']['equal_slate_gap'],50)

if __name__=='__main__':unittest.main()
