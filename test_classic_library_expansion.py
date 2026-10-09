import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_snapshots import create_snapshot
from candidate_library import (STYLES, classic_expansion_pool, connect,
    load_candidates, metadata, run_search, valid_classic_candidate)
from nfl_eligibility import apply_qb_eligibility, eligible_players
from optimizers import MultiSportClassicOptimizer
from portfolio_rules import player_key
from test_nfl_logic import _fixture_players

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')


class ClassicExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.players=eligible_players(apply_qb_eligibility(_fixture_players()))
        cls.qbs=sorted((p for p in cls.players if p['Position']=='QB'),key=player_key)
        cls.templates={}
        for qb in cls.qbs:
            pool=[p for p in cls.players if p['Position']!='QB' or player_key(p)==player_key(qb)]
            rows=MultiSportClassicOptimizer(pool,seed=41,salary_strategy='Flexible').build_lineups(2)
            if len(rows)!=2:raise AssertionError('Fixture must have two legal rosters per QB')
            cls.templates[player_key(qb)]=rows

    def snapshot(self,players=None,rules=None):
        return create_snapshot(players or self.players,dict(sport='NFL',contest_kind='classic',
            salary_strategy='Flexible',classic_coverage_expansion=True),rules or {})

    def test_schedule_covers_every_qb_and_style_without_mutating_flags_or_caps(self):
        players=copy.deepcopy(self.players)
        players[1]['LockFlex']=True
        for p in players:p['MaxPct']=30
        before=copy.deepcopy(players);targets=[]
        width=len(self.qbs)+1
        for index in range(width*len(STYLES)):
            pool,target,style,cycle=classic_expansion_pool(players,index)
            self.assertEqual(cycle,width*len(STYLES))
            if target:
                targets.append((target,style))
                self.assertEqual([player_key(p) for p in pool if p['Position']=='QB' and not p.get('FadeFlex')],[target])
                self.assertEqual([p.get('LockFlex') for p in pool],[p.get('LockFlex') for p in before])
                self.assertTrue(all(p['MaxPct']==30 for p in pool))
        self.assertEqual(len(set(targets)),len(self.qbs)*len(STYLES))
        self.assertEqual(players,before)
        self.assertEqual(classic_expansion_pool(players,1)[1],classic_expansion_pool(list(reversed(players)),1)[1])

    def test_manual_qb_lock_fade_unavailability_and_verified_pool_are_preserved(self):
        players=copy.deepcopy(self.players);players[0]['LockFlex']=True
        self.assertIsNone(classic_expansion_pool(players,1)[1])
        players[0]['LockFlex']=False;players[0]['FadeFlex']=True
        players[8]['Status']='OUT'
        allowed={player_key(p) for p in players if p['Position']=='QB' and not p.get('FadeFlex') and p.get('Status')!='OUT'}
        targets={classic_expansion_pool(players,i)[1] for i in range(len(allowed)+1)}-{None}
        self.assertEqual(targets,allowed)
        players.append(dict(players[0],FlexID='backup',NFLDepthOrder=2,FadeFlex=False))
        verified=eligible_players(apply_qb_eligibility(players))
        self.assertNotIn('backup',{p['FlexID'] for p in verified})

    def test_expansion_fills_missing_coverage_and_resume_matches_continuous(self):
        snapshot=self.snapshot();before=copy.deepcopy(snapshot)
        width=len(self.qbs)+1
        with tempfile.TemporaryDirectory() as folder:
            paths=[Path(folder)/'continuous.dfslib',Path(folder)/'resumed.dfslib']
            for path in paths:
                calls=[]
                def provider(opt,**kwargs):
                    active=[player_key(p) for p in opt.players if p['Position']=='QB' and not p.get('FadeFlex')]
                    calls.append((kwargs['num_lineups'],opt.seed if hasattr(opt,'seed') else 0))
                    rows=self.templates[active[0]]
                    return [row for row in rows if tuple(sorted(player_key(p) for p in row)) not in kwargs['exact_excluded_signatures']]
                with patch.object(MultiSportClassicOptimizer,'build_lineups',provider):
                    if 'resumed' in path.name:
                        run_search(path,snapshot,batch_size=2,candidate_limit=100,cancelled=lambda:len(calls)>=4)
                    run_search(path,snapshot,batch_size=2,candidate_limit=100,cancelled=lambda:len(calls)>=width)
                rows,report=load_candidates(path,self.players,kind='classic',salary_cap=50000,salary_strategy='Flexible')
                self.assertFalse(report['coverage']['uncovered_quarterbacks'])
                self.assertEqual(metadata(path)['batches'],width)
                with connect(path) as con:
                    self.assertEqual(con.execute('SELECT count(*) FROM coverage_batches').fetchone()[0],len(self.qbs))
            def saved(path):
                with connect(path) as con:
                    return list(con.execute('SELECT signature,batch,style,seed FROM candidates ORDER BY signature'))
            self.assertEqual(saved(paths[0]),saved(paths[1]))
        self.assertEqual(snapshot,before)

    def test_stagnation_attempts_all_pools_and_styles_without_claiming_exhaustion(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'empty.dfslib';messages=[]
            with patch.object(MultiSportClassicOptimizer,'build_lineups',return_value=[]) as build:
                self.assertEqual(run_search(path,self.snapshot(),candidate_limit=100,progress=messages.append),0)
                self.assertEqual(build.call_count,(len(self.qbs)+1)*len(STYLES))
            self.assertTrue(any('does not prove' in message for message in messages))

    def test_checkpoint_rejects_focused_pool_mismatch_and_current_rules(self):
        row=self.templates[player_key(self.qbs[0])][0]
        pool,target,_,_=classic_expansion_pool(self.players,2)
        self.assertFalse(valid_classic_candidate(row,pool,50000,'Flexible',{}))
        self.assertFalse(valid_classic_candidate(row,self.players,1,'Flexible',{}))
        self.assertFalse(valid_classic_candidate(row,self.players,50000,'Flexible',
            {'groups':[{'type':'at_least_one','player_keys':['missing']}]}))
        cheap=copy.deepcopy(self.players)
        for p in cheap:p['FlexSalary']=100
        self.assertFalse(valid_classic_candidate(row,cheap,50000,'Near Cap',{}))
        self.assertTrue(valid_classic_candidate(row,self.players,50000,'Flexible',{}))

    def test_focused_real_builder_preserves_manual_quarterback_cap(self):
        pool,target,_,_=classic_expansion_pool(copy.deepcopy(self.players),1)
        qb=next(p for p in pool if player_key(p)==target)
        qb['MaxPct']=25
        rows=MultiSportClassicOptimizer(pool,seed=41,salary_strategy='Flexible').build_lineups(4)
        self.assertEqual(len(rows),1)
        self.assertFalse(qb.get('LockFlex'))
        self.assertTrue(all(any(player_key(p)==target for p in row) for row in rows))

    def test_new_classic_dialog_records_expansion_in_frozen_snapshot(self):
        from PyQt5 import QtWidgets
        from long_search_ui import LongSearchDialog
        from build_snapshots import validate_snapshot
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        parent=QtWidgets.QWidget()
        original=create_snapshot(self.players,dict(sport='NFL',contest_kind='classic'),{})
        before=copy.deepcopy(original)
        parent._capture_snapshot=lambda:original
        dialog=LongSearchDialog(parent)
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'new.dfslib')
            with patch('build_diagnostics.build_history_path',return_value=Path(folder)/'history.sqlite'), \
                    patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(path,'')):
                dialog.new_library()
            self.assertEqual(dialog.path.text(),path)
            self.assertTrue(validate_snapshot(dialog.snapshot)['inputs']['recipe']['classic_coverage_expansion'])
        self.assertEqual(original,before)
        dialog.close();parent.close()

    def test_incompatible_resume_does_not_add_provenance_table_to_old_library(self):
        from candidate_library import initialize
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'older.dfslib';snapshot=self.snapshot()
            initialize(path,snapshot)
            with connect(path) as con:con.execute('DROP TABLE coverage_batches')
            before=path.read_bytes()
            with patch('candidate_library.code_id',return_value='different-code'):
                with self.assertRaisesRegex(ValueError,'different inputs or app code'):
                    run_search(path,snapshot)
            self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main()
