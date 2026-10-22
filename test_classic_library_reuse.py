import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_snapshots import create_snapshot
from candidate_library import (classic_coverage, connect, coverage_text,
    load_candidates, metadata, run_search)
from optimizers import MultiSportClassicOptimizer
from portfolio_rules import player_key
from test_nfl_logic import _fixture_players


class ClassicLibraryReuseTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'classic.dfslib'
        self.players=_fixture_players()
        self.snapshot=create_snapshot(self.players,dict(sport='NFL',contest_kind='classic',
            salary_strategy='Flexible'),{})
        self.rows=MultiSportClassicOptimizer(self.players,seed=41).build_lineups(4)
        self.assertTrue(self.rows)
        with patch.object(MultiSportClassicOptimizer,'build_lineups',return_value=self.rows):
            run_search(self.path,self.snapshot,candidate_limit=len(self.rows))

    def load(self,players=None,**kwargs):
        return load_candidates(self.path,players if players is not None else self.players,
            kind='classic',salary_cap=kwargs.pop('salary_cap',50000),
            salary_strategy='Flexible',**kwargs)

    def test_code_update_reuses_rosters_with_current_forecasts_without_mutation(self):
        current=copy.deepcopy(self.players)
        for p in current:p['FlexProjection']=99;p['Ownership']=12
        before=metadata(self.path)
        with patch('candidate_library.code_id',return_value='new-app-code'):
            rows,report=self.load(current)
        self.assertTrue(report['code_changed'])
        self.assertEqual(report['accepted'],len(self.rows))
        self.assertTrue(all(p['FlexProjection']==99 for row in rows for p in row))
        self.assertEqual(metadata(self.path),before)
        self.assertFalse(any(getattr(row,'sim_metrics',{}) for row in rows))

    def test_resume_stays_strict_and_legacy_or_unknown_formats_fail_closed(self):
        with patch('candidate_library.code_id',return_value='new-app-code'):
            with self.assertRaisesRegex(ValueError,'different inputs or app code'):
                run_search(self.path,self.snapshot)
        with connect(self.path) as con:
            con.execute("DELETE FROM library_meta WHERE key='roster_format'")
        with patch('candidate_library.code_id',return_value='new-app-code'):
            with self.assertRaisesRegex(ValueError,'different app code'):self.load()
        with connect(self.path) as con:
            con.execute('INSERT INTO library_meta VALUES (?,?)',('roster_format','future-v2'))
        with self.assertRaisesRegex(ValueError,'Unsupported candidate roster format'):self.load()

    def test_current_salary_availability_fades_locks_positions_groups_and_slate_checked(self):
        with patch('candidate_library.code_id',return_value='new-app-code'):
            for field,value in [('Status','OUT'),('FadeFlex',True),('FlexSalary',60000),('Position','DST')]:
                current=copy.deepcopy(self.players)
                for p in current:p[field]=value
                with self.subTest(field=field),self.assertRaisesRegex(ValueError,'No saved candidates'):
                    self.load(current)
            current=copy.deepcopy(self.players);current[0]['GameInfo']='next week'
            with self.assertRaisesRegex(ValueError,'different player slate'):self.load(current)
            current=copy.deepcopy(self.players)
            for p in current:
                if p['Position']=='WR':p['LockFlex']=True
            with self.assertRaisesRegex(ValueError,'No saved candidates'):self.load(current)
            with self.assertRaisesRegex(ValueError,'No saved candidates'):self.load(salary_cap=1)
            with self.assertRaisesRegex(ValueError,'No saved candidates'):
                self.load(rules={'groups':[{'type':'at_least_one','player_keys':['absent-id']}]})

    def test_coverage_is_deterministic_and_reports_missing_qbs_and_stack_counts(self):
        rows,report=self.load()
        coverage=report['coverage']
        self.assertEqual(sum(coverage['quarterbacks'].values()),len(rows))
        self.assertEqual(sum(coverage['stack_shapes'].values()),len(rows))
        self.assertEqual(coverage,classic_coverage(list(reversed(rows)),list(reversed(self.players))))
        represented=set(coverage['quarterbacks'])
        expected={player_key(p) for p in self.players if p['Position']=='QB'}-represented
        self.assertEqual({p['key'] for p in coverage['uncovered_quarterbacks']},expected)
        self.assertFalse(coverage['exhaustive'])
        self.assertIn('does not prove',coverage_text(report))
        current=copy.deepcopy(self.players)
        qb=next(p for p in current if player_key(p) in represented);qb['LockFlex']=True
        self.assertFalse(classic_coverage(rows,current)['uncovered_quarterbacks'])


if __name__=='__main__':unittest.main()
