"""Joint Captain/DST evidence cannot be inferred from marginal frequencies."""
import copy
import unittest
from historical_field_report import FieldRows
from historical_contest_comparison import captain_defense_rate,field_comparison,captain_defense_report
from test_historical_contest_comparison import contest


def source(name='a',day='2026-09-21'):
    c=contest(name,day=day,field=42,top1=28);c['version']=3
    for key,n,qb,rb,qwith,rwith in [('field',100,80,20,40,2),('top1',100,20,80,12,16)]:
        c['cohorts'][key]=dict(counts=dict(supported_rows=n,entry_rows=n),
            features={'Captain position: QB':qb,'Captain position: RB':rb,'Captain with opposing FLEX defense: yes':qwith+rwith},
            captain_defense={'QB with':qwith,'QB without':qb-qwith,'RB with':rwith,'RB without':rb-rwith})
    return c


def salaries():
    players=[]
    for i,(name,position,team) in enumerate([('Alpha','QB','AAA'),('Bravo','RB','AAA'),('Charlie','WR','BBB'),
            ('Delta','TE','BBB'),('Echo','K','AAA'),('Foxtrot','DST','BBB')]):
        for role in ('CPT','FLEX'):
            players.append(dict(name=name,position=position,team=team,role=role,id=str(i+(1000 if role=='CPT' else 100)),salary=6000 if role=='CPT' else 4000))
    return dict(format='showdown',players=players)


class CaptainDefenseTests(unittest.TestCase):
    def test_resolves_all_captain_positions_and_excludes_captain_itself_from_flex_defense(self):
        rows=FieldRows(salaries(),rank_size=100)
        names=['Alpha','Bravo','Charlie','Delta','Echo','Foxtrot']
        for name in names:rows.add('CPT '+name+' FLEX '+' FLEX '.join(n for n in names if n!=name),rank=1)
        p=rows.payload(True);self.assertEqual(p['version'],3)
        expected={'QB with':1,'RB with':1,'WR without':1,'TE without':1,'K with':1,'DST without':1}
        self.assertEqual(p['cohorts']['field']['captain_defense'],expected)
        self.assertEqual(p['cohorts']['top1']['captain_defense'],expected)

    def test_identical_source_rows_count_as_rows_and_invalid_identity_stays_outside_joint_counts(self):
        rows=FieldRows(salaries(),rank_size=100)
        text='CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'
        rows.add(text,rank=1);rows.add(text,rank=1)
        rows.add(text.replace('Alpha','Alpha (100)'),rank=1)
        p=rows.payload(True)
        self.assertEqual(p['cohorts']['field']['captain_defense'],{'QB with':2})
        self.assertEqual(p['counts']['unknown_identity_rows'],1)
        self.assertEqual(rows.payload(False)['cohorts'],{})

    def test_marginal_gap_can_reverse_within_both_captain_positions(self):
        c=source()
        self.assertEqual(field_comparison([c],'Captain with opposing FLEX defense: yes','top1')['gap'],-14)
        for pos in ('QB','RB'):
            r=field_comparison([c],'','top1',captain_position=pos)
            self.assertEqual(r['gap'],10)
        self.assertEqual(field_comparison([c],'','top1',captain_position='QB')['field_rows'],80)
        self.assertEqual(field_comparison([c],'','top1',captain_position='QB')['comparator_rows'],20)

    def test_legacy_marginals_never_supply_joint_counts_but_broad_rates_remain_valid(self):
        for version in (1,2):
            c=source();c['version']=version
            self.assertIsNone(captain_defense_rate(c,'field','QB'))
            r=field_comparison([c],'','top1',captain_position='QB')
            self.assertIsNone(r['gap']);self.assertEqual(r['excluded_legacy'],1)
        c['version']=2
        text='\n'.join(captain_defense_report(dict(format='showdown',contests=[c])))
        self.assertIn('marginal counts only',text);self.assertNotIn('| QB |',text)
        self.assertEqual(field_comparison([c],'Captain with opposing FLEX defense: yes','top1')['gap'],-14)

    def test_missing_inconsistent_or_boolean_counts_and_empty_positions_stay_unknown(self):
        for change in ('missing','partial','negative','boolean','excess','empty','fraction'):
            c=source();g=c['cohorts']['field']
            if change=='missing':g.pop('captain_defense')
            elif change=='partial':g['captain_defense'].pop('QB without')
            elif change=='negative':g['captain_defense']['QB with']=-1
            elif change=='boolean':g['captain_defense']['QB with']=True
            elif change=='excess':g['counts']['supported_rows']=10
            elif change=='empty':g['features']['Captain position: QB']=0
            else:g['captain_defense']['QB with']=40.5
            with self.subTest(change=change):self.assertIsNone(captain_defense_rate(c,'field','QB'))

    def test_real_zero_with_complete_position_coverage_is_zero(self):
        c=source();g=c['cohorts']['field'];g['captain_defense'].update({'QB with':0,'QB without':80})
        self.assertEqual(captain_defense_rate(c,'field','QB'),0)
        self.assertIsNone(captain_defense_rate(c,'field','TE'))

    def test_equal_slate_weight_deduplication_and_conflicts_are_preserved(self):
        a=source();b=source('b',day='2026-09-28');b['cohorts']['top1']['captain_defense'].update({'QB with':0,'QB without':20})
        records=[a,b];before=copy.deepcopy(records)
        r=field_comparison(records,'','top1',captain_position='QB')
        self.assertEqual(r['gap'],-20);self.assertEqual(r['slates'],2)
        repeated=field_comparison(records+[copy.deepcopy(a)],'','top1',captain_position='QB')
        self.assertEqual(repeated['gap'],r['gap']);self.assertEqual(repeated['contests'],2)
        other=copy.deepcopy(a);other['cohorts']['field']['captain_defense']['QB with']=20
        first=field_comparison([a,other,b],'','top1',captain_position='QB')
        self.assertEqual(first,field_comparison([b,other,a],'','top1',captain_position='QB'))
        self.assertEqual(first['excluded_conflicting_sources'],1);self.assertEqual(records,before)

    def test_comparator_without_position_coverage_or_game_identity_is_excluded(self):
        a=source();b=source('b');b['cohorts']['top1']['features'].pop('Captain position: QB')
        c=source('c');c['games']=()
        r=field_comparison([a,b,c],'','top1',captain_position='QB')
        self.assertEqual((r['contests'],r['excluded_cohort'],r['excluded_identity']),(1,1,1))

    def test_report_separates_positions_cohorts_unknown_legacy_and_classic(self):
        text='\n'.join(captain_defense_report(dict(format='showdown',contests=[source()])))
        for phrase in ('within Captain position','Rank cutoff 1%','Rank cutoff 5%','Rank 1','not a controlled experiment',
                'Reconcile Historical Coverage once','duplicate EntryIds','No Captain locks','| DST |','| TE |'):
            self.assertIn(phrase,text)
        self.assertEqual(captain_defense_report(dict(format='classic',contests=[source()])),[])
        with self.assertRaises(ValueError):field_comparison([source()],'','top1',captain_position='UNKNOWN')


class CaptainDefenseReconciliationTests(unittest.TestCase):
    from test_historical_field_report import FieldReportTests as _fixture
    setUp=_fixture.setUp
    saved=_fixture.saved

    def test_normal_reconciliation_produces_joint_counts_and_report_stays_readonly(self):
        from historical_field_report import summarize,report_lines
        from learning_db import load_nfl_field_calibration
        saved,pairs=self.saved();payload=saved['contests'][0].data['results_evidence']['field_row_summary']
        self.assertEqual(payload['version'],3)
        self.assertEqual(payload['cohorts']['field']['captain_defense'],{'QB without':30})
        cache=self.fixture.query('SELECT * FROM historical_contest_identities')
        before=load_nfl_field_calibration('150-Max',db_path=self.fixture.db)
        text='\n'.join(report_lines(saved,summarize(saved,pairs)))
        self.assertIn('Opposing FLEX defense within Captain position',text)
        self.assertEqual(cache,self.fixture.query('SELECT * FROM historical_contest_identities'))
        self.assertEqual(before,load_nfl_field_calibration('150-Max',db_path=self.fixture.db))

    def test_partial_scan_never_publishes_joint_counts(self):
        from unittest.mock import patch
        import historical_identity as hi
        with patch.object(hi,'MAX_OUTCOME_ROWS',2):
            outcome=hi._outcomes(self.fixture.source,self.salary,lambda:False)
        self.assertFalse(outcome['field_row_summary']['scan_complete'])
        self.assertEqual(outcome['field_row_summary']['cohorts'],{})


if __name__=='__main__':unittest.main()
