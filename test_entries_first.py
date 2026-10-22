from test_environment import install
install()
import copy
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from entries_first import (inspect_entries, choose, load_salary, verify_sources,
                           pool_identity, validate_players, capture, money)
from dk_entries import read_entries_template
from data_io import read_players_csv


def fixture(root, *, embedded=True, kind='showdown', count=2, filled=True, contests=None):
    slots = ['CPT']+['FLEX']*5 if kind=='showdown' else ['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']
    header = ['Entry ID','Contest Name','Contest ID','Entry Fee']+slots
    salary_header = ['Position','Name + ID','Name','ID','Roster Position','Salary','Game Info','TeamAbbrev','Projection']
    salaries = []
    for i in range(10):
        for role, pid, mult in ([('CPT',1000+i,1.5),('FLEX',100+i,1)] if kind=='showdown' else [('FLEX',100+i,1)]):
            salaries.append(['QB' if i in (0,5) else 'WR',f'Player {i} ({pid})',f'Player {i}',str(pid),role,5000*mult,'SEA@DEN 10/25/2026 01:00PM ET','SEA' if i<5 else 'DEN',10*mult])
    groups = contests or [('77','Tournament test','$1',count)]
    entries = []
    for cid, name, fee, n in groups:
        for _ in range(n):
            roster = ['1000','101','102','103','104','105'] if kind=='showdown' else [str(100+i) for i in range(9)]
            entries.append([str(10000+len(entries)),name,cid,fee]+(roster if filled else ['']*len(slots)))
    path = Path(root)/'entries.csv'
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f)
        writer.writerow(header+(['']+salary_header if embedded else []))
        for i in range(max(len(entries),len(salaries) if embedded else 0)):
            left=entries[i] if i<len(entries) else ['']*len(header)
            writer.writerow(left+(['']+(salaries[i] if i<len(salaries) else ['']*len(salary_header)) if embedded else []))
    salary=Path(root)/'salary.csv'
    with salary.open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(salary_header);writer.writerows(salaries)
    return path,salary


def profile(name='Cash', fee=1):
    return dict(name=name,objective='DOUBLE_UP',field_size=100,entry_fee=fee,user_entries=1,payouts='1-40=2')


class EntriesFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)

    def selected(self, **kwargs):
        path,salary=fixture(self.tmp.name,**kwargs)
        plan=inspect_entries(path)
        return path,salary,plan,choose(plan,'77','20-Max','default',{},path if kwargs.get('embedded',True) else salary,confirmed=True)

    def test_embedded_both_formats_capture_ids_and_keep_sources_unchanged(self):
        for kind in ('showdown','classic'):
            with self.subTest(kind=kind):
                path,salary,plan,selection=self.selected(kind=kind)
                original=path.read_bytes();players,result=load_salary(path,selection)
                self.assertEqual(plan['kind'],kind);self.assertTrue(plan['embedded_identity'])
                self.assertEqual(result['observed_slots'],2*(6 if kind=='showdown' else 9))
                self.assertEqual(result['pool_identity'],pool_identity(players))
                self.assertEqual(path.read_bytes(),original);verify_sources(result)

    def test_no_inference_from_name_count_or_preexisting_settings(self):
        path,salary=fixture(self.tmp.name,contests=[('77','Double-Up 150-Max','$1',1)])
        plan=inspect_entries(path)
        with self.assertRaisesRegex(ValueError,'Select a contest'):
            choose(plan,'77','','',{},path)
        selection=choose(plan,'77','150-Max','default',{},path)
        self.assertIsNone(selection['profile']);self.assertEqual(selection['contest']['count'],1)
        self.assertEqual(selection['entry_limit'],'150-Max')

    def test_multiple_contests_selected_scope_and_fees(self):
        path,salary=fixture(self.tmp.name,contests=[('77','One','$1',1),('88','Two','$2',3)])
        plan=inspect_entries(path);self.assertEqual([c['count'] for c in plan['contests']],[1,3])
        selection=choose(plan,'88','3-Max','default',{},path)
        _,result=load_salary(path,selection);self.assertEqual(result['observed_slots'],18)
        with self.assertRaisesRegex(ValueError,'exceeds'):
            choose(plan,'88','Single Entry','default',{},path)

    def test_plain_entries_need_explicit_salary_choice_and_confirmation(self):
        path,salary,plan,selection=self.selected(embedded=False,filled=False)
        self.assertFalse(plan['embedded_identity'])
        with self.assertRaisesRegex(ValueError,'Confirm'):
            choose(plan,'77','20-Max','default',{},salary)
        _,result=load_salary(salary,selection)
        self.assertEqual(result['observed_slots'],0)

    def test_wrong_embedded_revision_cannot_be_replaced_by_plausible_ids(self):
        path,salary,plan,selection=self.selected()
        salary.write_text(salary.read_text().replace('5000','5100'),encoding='utf-8')
        selection=choose(plan,'77','20-Max','default',{},salary)
        with self.assertRaisesRegex(ValueError,'differs from the embedded'):
            load_salary(salary,selection)

    def test_changed_entries_or_salary_and_late_changes_are_rejected(self):
        path,salary,plan,selection=self.selected()
        path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'changed'):
            load_salary(path,selection)
        plan=inspect_entries(path);selection=choose(plan,'77','20-Max','default',{},path)
        _,loaded=load_salary(path,selection)
        path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'changed'):
            verify_sources(loaded)

    def test_wrong_role_unknown_id_duplicate_athlete_and_names_without_ids(self):
        path,salary,plan,selection=self.selected(embedded=False)
        players=read_players_csv(salary);template=read_entries_template(path)
        for roster in (['100'],['99999'],['1000','100'],['Player 0']):
            with self.subTest(roster=roster):
                bad=copy.deepcopy(template);row=bad.rows[bad.entry_row_indexes[0]]
                row[4:10]=roster+['']*(6-len(roster))
                with self.assertRaises(ValueError):validate_players(bad,'77',players)
        partial=copy.deepcopy(template);partial.rows[partial.entry_row_indexes[0]][4:10]=['Player 0 (1000)']+['']*5
        self.assertEqual(validate_players(partial,'77',players),7)

    def test_conflicting_contest_metadata_duplicate_entries_and_unsupported_format(self):
        for defect in ('fee','name','duplicate','format'):
            with self.subTest(defect=defect):
                path,_=fixture(self.tmp.name)
                with path.open(newline='',encoding='utf-8') as f:rows=list(csv.reader(f))
                if defect=='fee':rows[2][3]='$2'
                elif defect=='name':rows[2][1]='Different'
                elif defect=='duplicate':rows[2][0]=rows[1][0]
                else:rows[0][4]='MVP'
                with path.open('w',newline='',encoding='utf-8') as f:csv.writer(f).writerows(rows)
                with self.assertRaises(ValueError):inspect_entries(path)

    def test_profiles_are_explicit_fee_matched_and_count_adjusted_without_mutation(self):
        path,salary,plan,_=self.selected()
        profiles={'Cash':profile()};before=copy.deepcopy(profiles)
        result=choose(plan,'77','20-Max','profile:Cash',profiles,path)
        self.assertEqual(result['profile']['objective'],'DOUBLE_UP')
        self.assertEqual(result['profile']['user_entries'],2);self.assertEqual(profiles,before)
        with self.assertRaisesRegex(ValueError,'fee'):
            choose(plan,'77','20-Max','profile:Cash',{'Cash':profile(fee=2)},path)
        with self.assertRaisesRegex(ValueError,'unavailable'):
            choose(plan,'77','20-Max','profile:Missing',{},path)

    def test_limits_cancellation_and_known_zero_fee(self):
        path,salary=fixture(self.tmp.name,contests=[('77','Free','$0',2)])
        plan=inspect_entries(path);self.assertEqual(plan['contests'][0]['fee'],'0.00')
        self.assertIsNone(choose(plan,'77','20-Max','default',{},path)['profile'])
        for value in ('NaN','-1','unknown','1.001','1e100000'):
            with self.subTest(value=value), self.assertRaises(ValueError):money(value)
        with self.assertRaises(InterruptedError):inspect_entries(path,lambda:True)
        with patch('entries_first.MAX_BYTES',1),self.assertRaisesRegex(ValueError,'32 MiB'):
            inspect_entries(path)

    def test_private_copy_survives_changes_and_does_not_write_original(self):
        path,_=fixture(self.tmp.name)
        original=path.read_bytes()
        with capture(path) as (frozen,digest,_):
            path.write_bytes(b'changed')
            self.assertEqual(Path(frozen).read_bytes(),original)
        self.assertEqual(path.read_bytes(),b'changed')


    def test_explicit_salary_conflicts_are_rejected_in_either_order(self):
        from entries_first import salary_players
        path,salary=fixture(self.tmp.name)
        with salary.open(newline='',encoding='utf-8') as f:rows=list(csv.reader(f))
        for conflict in ('id','salary','team'):
            for reverse in (False,True):
                with self.subTest(conflict=conflict,reverse=reverse):
                    duplicate=list(rows[1])
                    if conflict=='id':duplicate[3]='99999'
                    elif conflict=='salary':duplicate[5]='1234'
                    else:duplicate[7]='OTHER'
                    data=rows[:1]+([duplicate]+rows[1:] if reverse else rows[1:]+[duplicate])
                    with salary.open('w',newline='',encoding='utf-8') as f:csv.writer(f).writerows(data)
                    with self.assertRaises(ValueError):salary_players(salary)

    def test_identical_salary_duplicates_remain_usable(self):
        from entries_first import salary_players
        path,salary=fixture(self.tmp.name)
        before=salary_players(salary)
        with salary.open(newline='',encoding='utf-8') as f:rows=list(csv.reader(f))
        with salary.open('a',newline='',encoding='utf-8') as f:csv.writer(f).writerow(rows[1])
        self.assertEqual(salary_players(salary),before)

    def test_invalid_embedded_table_is_not_silently_treated_as_absent(self):
        path,salary=fixture(self.tmp.name)
        with path.open(newline='',encoding='utf-8') as f:rows=list(csv.reader(f))
        rows[2][14]='not-an-id'
        with path.open('w',newline='',encoding='utf-8') as f:csv.writer(f).writerows(rows)
        with self.assertRaises(ValueError):inspect_entries(path)


    def test_large_entries_count_keeps_side_by_side_embedded_pool(self):
        path,salary=fixture(self.tmp.name,count=150,filled=False)
        plan=inspect_entries(path)
        self.assertTrue(plan['embedded_identity'])
        self.assertEqual(plan['contests'][0]['count'],150)
        selection=choose(plan,'77','150-Max','default',{},path)
        players,loaded=load_salary(path,selection)
        self.assertEqual(len(players),10);self.assertEqual(loaded['observed_slots'],0)
