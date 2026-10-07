from test_environment import install
install()
import copy
from contextlib import closing
from itertools import combinations
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

from showdown_library import prepare, status, iter_candidates
from portfolio_rules import player_key


def players():
    return [dict(Name=f'Player {i}', FlexID=str(i+1), CptID=str(i+101),
                 FlexNamePlusID=f'Player {i} ({i+1})', FlexSalary=4000+i*500,
                 CptSalary=(4000+i*500)*1.5, Team='A' if i<4 else 'B',
                 GameInfo='A@B 10/11/2026 08:15PM ET', Position='WR',
                 FlexProjection=10+i, CptProjection=(10+i)*1.5) for i in range(8)]


def signatures(rows):
    return {(player_key(r['Captain']), tuple(sorted(player_key(p) for p in r['Flex']))) for r in rows}


class ShowdownLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'library.sqlite'
        self.players = players()

    def test_complete_library_matches_independent_brute_force(self):
        self.players[0]['FlexSalary'] = 20000
        self.players[0]['CptSalary'] = 30000
        expected = set()
        for captain in self.players:
            for flex in combinations([p for p in self.players if p is not captain], 5):
                if captain['CptSalary']+sum(p['FlexSalary'] for p in flex) <= 50000 and len({p['Team'] for p in [captain,*flex]}) == 2:
                    expected.add((player_key(captain), tuple(sorted(player_key(p) for p in flex))))
        result = prepare(self.path, self.players, batch_size=7)
        self.assertTrue(result['complete']); self.assertEqual(result['checked'], 168)
        actual = list(iter_candidates(self.path, self.players))
        self.assertEqual(signatures(actual), expected)
        self.assertEqual(len(actual), len(expected))

    def test_cancel_resume_is_complete_without_duplicates_or_restarts(self):
        stop = [False]
        first = prepare(self.path, self.players, batch_size=7,
                        progress=lambda value: stop.__setitem__(0, True), cancelled=lambda:stop[0])
        self.assertEqual(first['checked'], 7); self.assertTrue(first['cancelled'])
        self.assertFalse(first['complete'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            list(iter_candidates(self.path, self.players))
        self.assertEqual(len(list(iter_candidates(self.path, self.players, allow_partial=True))), first['saved'])
        second = prepare(self.path, list(reversed(self.players)), batch_size=11)
        self.assertTrue(second['complete']); self.assertEqual(second['checked'], 168)
        self.assertEqual(second['saved'], len(signatures(iter_candidates(self.path, self.players))))
        self.assertEqual(prepare(self.path,self.players)['saved'], second['saved'])

    def test_candidate_limit_is_partial_and_resumable(self):
        first = prepare(self.path,self.players,max_candidates=9)
        self.assertEqual(first['saved'],9); self.assertFalse(first['complete'])
        second = prepare(self.path,self.players,max_candidates=200)
        self.assertTrue(second['complete']); self.assertEqual(second['saved'],168)

    def test_current_captain_pool_flex_lock_fades_status_and_forecasts(self):
        prepare(self.path,self.players)
        current = copy.deepcopy(self.players)
        current[0]['LockCpt'] = current[1]['LockCpt'] = True
        current[2]['LockFlex'] = True
        current[3]['Status'] = 'OUT'
        current[4]['FadeFlex'] = True
        current[0]['FlexProjection'] = 999
        current[0]['CptProjection'] = 1498.5
        rows = list(iter_candidates(self.path,current))
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(row['Captain']['FlexID'], {'1','2'})
            self.assertIn('3', {p['FlexID'] for p in row['Flex']})
            self.assertNotIn('4', {p['FlexID'] for p in [row['Captain'], *row['Flex']]})
            self.assertNotIn('5', {p['FlexID'] for p in row['Flex']})
            if row['Captain']['FlexID']=='1':
                self.assertEqual(row['Captain']['CptProjection'],1498.5)

    def test_salary_identity_date_changes_reject_without_writing(self):
        prepare(self.path,self.players)
        before = self.path.read_bytes()
        for field,value in [('FlexSalary',4001),('FlexID','other'),('GameInfo','A@B next week')]:
            changed = copy.deepcopy(self.players); changed[0][field]=value
            with self.subTest(field=field):
                with self.assertRaises(ValueError): prepare(self.path,changed)
                with self.assertRaises(ValueError): list(iter_candidates(self.path,changed))
                self.assertEqual(self.path.read_bytes(),before)

    def test_duplicate_identities_reject_before_creating_storage(self):
        self.players.append(dict(self.players[0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            prepare(self.path,self.players)
        self.assertFalse(self.path.exists())

    def test_unrelated_database_and_damaged_completion_are_rejected(self):
        with closing(sqlite3.connect(self.path)) as conn:
            with conn: conn.execute('CREATE TABLE unrelated(id INTEGER)')
        before=self.path.read_bytes()
        with self.assertRaises(ValueError): prepare(self.path,self.players)
        self.assertEqual(self.path.read_bytes(),before)
        self.path.unlink();prepare(self.path,self.players,max_candidates=3)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            payload=json.loads(conn.execute('SELECT payload FROM preparation').fetchone()[0])
            payload['complete']=True
            conn.execute('UPDATE preparation SET payload=?',(json.dumps(payload),))
        with self.assertRaisesRegex(ValueError,'completion'): status(self.path)

    def test_current_salary_floor_and_cancelled_reader(self):
        prepare(self.path,self.players)
        rows=list(iter_candidates(self.path,self.players,salary_floor=35000,salary_cap=40000))
        for row in rows:
            salary=row['Captain']['CptSalary']+sum(p['FlexSalary'] for p in row['Flex'])
            self.assertTrue(35000 <= salary <= 40000)
        self.assertEqual(list(iter_candidates(self.path,self.players,cancelled=lambda:True)),[])

    def test_disk_limit_and_deadline_pause_without_claiming_completion(self):
        with patch('showdown_library.MAX_BYTES',1):
            result=prepare(self.path,self.players,batch_size=7)
        self.assertEqual(result['checked'],7);self.assertFalse(result['complete'])
        with patch('showdown_library.time.monotonic',side_effect=[0,2]):
            result=prepare(self.path,self.players,seconds=1)
        self.assertEqual(result['checked'],7);self.assertFalse(result['complete'])
        self.assertTrue(prepare(self.path,self.players)['complete'])

    def test_current_qb_evidence_and_missing_forecasts_are_revalidated(self):
        prepare(self.path,self.players)
        current=copy.deepcopy(self.players)
        current[0].update(Position='QB',NFLDepthOrder=1)
        current[1].update(Position='QB',NFLDepthOrder=2)
        current[2]['ProjectionSource']='Missing forecast'
        rows=list(iter_candidates(self.path,current))
        self.assertTrue(rows)
        for row in rows:
            self.assertFalse({'2','3'} & {p['FlexID'] for p in [row['Captain'],*row['Flex']]})
        current[1]['LockCpt']=True
        with self.assertRaisesRegex(ValueError,'not eligible'):
            list(iter_candidates(self.path,current))

    def test_sql_filter_does_not_hide_corruption_inside_excluded_rosters(self):
        import struct
        damaged=[b'bad',struct.pack('<5H',1,2,3,4,99),struct.pack('<5H',1,2,3,4,256),
                 struct.pack('<5H',1,1,2,3,4),struct.pack('<5H',5,4,3,2,1),
                 struct.pack('<5H',0,1,2,3,4),'0123456789']
        current=copy.deepcopy(self.players);current[1]['InjuryStatus']='OUT'
        for index,encoded in enumerate(damaged):
            with self.subTest(encoded=encoded):
                path=Path(self.temp.name)/f'corrupt-{index}.sdlib'
                prepare(path,self.players)
                with closing(sqlite3.connect(path)) as con,con:
                    con.execute('UPDATE rosters SET flex=? WHERE captain=0 AND flex=(SELECT flex FROM rosters WHERE captain=0 LIMIT 1)',(encoded,))
                with self.assertRaisesRegex(ValueError,'damaged'):
                    list(iter_candidates(path,current,salary_floor=47000))

    def test_malformed_compact_roster_is_rejected(self):
        prepare(self.path,self.players)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('UPDATE rosters SET flex=? WHERE captain=0 AND flex=(SELECT flex FROM rosters WHERE captain=0 LIMIT 1)',(b'bad',))
        with self.assertRaisesRegex(ValueError,'damaged'):
            list(iter_candidates(self.path,self.players))

    def test_deleted_rosters_invalidate_completion_at_read(self):
        prepare(self.path,self.players)
        with closing(sqlite3.connect(self.path)) as conn,conn:
            conn.execute('DELETE FROM rosters WHERE captain=0')
        with self.assertRaisesRegex(ValueError,'saved count'):
            list(iter_candidates(self.path,self.players))

    def test_offline_cli_prepares_and_reports_status_from_snapshot(self):
        from build_snapshots import create_snapshot
        snapshot=create_snapshot(self.players,dict(sport='NFL',contest_kind='showdown',salary_cap=50000),{})
        source=Path(self.temp.name)/'snapshot.json'
        source.write_text(json.dumps(snapshot),encoding='utf-8')
        script=Path(__file__).resolve().parent/'scripts'/'prepare_showdown_library.py'
        run=subprocess.run([sys.executable,str(script),'--snapshot',str(source),'--output',str(self.path)],
                           capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stderr);self.assertTrue(status(self.path)['complete'])
        run=subprocess.run([sys.executable,str(script),'--output',str(self.path),'--status'],
                           capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout)['saved'],168)

    def test_conflicting_writer_cannot_overwrite_newer_checkpoint(self):
        entered=[False]
        def another_writer(value):
            if not entered[0]:
                entered[0]=True
                self.assertTrue(prepare(self.path,self.players)['complete'])
        with self.assertRaises((sqlite3.IntegrityError,ValueError)):
            prepare(self.path,self.players,batch_size=7,progress=another_writer)
        self.assertTrue(status(self.path)['complete'])
        self.assertEqual(status(self.path)['saved'],168)

    def test_invalid_limits_and_salaries_do_not_create_a_library(self):
        for options in [dict(seconds=0),dict(seconds=float('nan')),dict(batch_size=0),dict(max_candidates=0)]:
            with self.subTest(options=options),self.assertRaises(ValueError):
                prepare(self.path,self.players,**options)
            self.assertFalse(self.path.exists())
        self.players[0]['FlexSalary']=None
        with self.assertRaises(ValueError): prepare(self.path,self.players)
        self.assertFalse(self.path.exists())


if __name__=='__main__': unittest.main()
