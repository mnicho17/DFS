import json
import sqlite3
import unittest
from unittest.mock import patch
from performance_review import ensure_tables
from score_reconciliation import reconciliation_report
from pipeline_audit import quarterback_mix, format_quarterback_pipeline
from optimizers import ShowdownLineup
from build_diagnostics import create_build_diagnostic, format_build_report


class ReconciliationPipelineTests(unittest.TestCase):
    def test_coverage_is_distinct_from_conflicts_and_dates(self):
        conn=sqlite3.connect(':memory:');ensure_tables(conn)
        conn.execute('CREATE TABLE historical_imports(import_id TEXT,source_path TEXT)')
        for ident,date,points in [('a','2026-09-10',10),('b','2026-09-10',10),('c','2026-09-11',99)]:
            conn.execute('INSERT INTO historical_imports VALUES (?,?)',(ident,ident))
            conn.execute('INSERT INTO construction_reviews VALUES (?,?,?,?)',(ident,'hash',json.dumps({'name':ident,'date':date}),'now'))
            conn.execute('INSERT INTO contest_player_scores VALUES (?,?,?,?,?)',(ident,'player',points,date,'filename'))
        tables={'a':{'player':10,'@cpt:player':15},'b':{'player':10},'c':{'player':99}}
        with patch('score_reconciliation._player_results',side_effect=lambda path,_:(tables[path],{},'')):
            text='\n'.join(reconciliation_report(conn))
            self.assertIn('0 conflicting scores',text)
            self.assertIn('explained by slot coverage',text)
            self.assertNotIn('versus c',text)
            tables['b']['player']=11
            conn.execute("UPDATE contest_player_scores SET points=11 WHERE import_id='b'")
            text='\n'.join(reconciliation_report(conn))
            self.assertIn('1 conflicting scores',text)
            self.assertIn('player: 10.00 versus 11.00',text)
        with patch('score_reconciliation._player_results',return_value=({}, {}, 'missing')):
            self.assertIn('cached base scores only','\n'.join(reconciliation_report(conn)))
        conn.close()

    def test_captain_counts_once_metrics_and_report_roundtrip(self):
        qb={'Position':'QB'};wr={'Position':'WR'}
        one=ShowdownLineup(qb,[wr]*5);one.sim_metrics={'sim_top_one_pct':8.5}
        two=ShowdownLineup(wr,[qb,qb,wr,wr,wr]);two.sim_metrics={'sim_top_one_pct':0}
        stages={'generated':quarterback_mix([one,two]),'selected':quarterback_mix([one,two],scored=True)}
        self.assertEqual(stages['generated']['groups']['1']['scored'],0)
        self.assertEqual(stages['selected']['groups']['2']['scored'],1)
        self.assertEqual(quarterback_mix([[qb]+[wr]*8])['groups']['1']['count'],1)
        self.assertIn('unknown',quarterback_mix([[{}]])['groups'])
        record=create_build_diagnostic(context={'kind':'showdown','sport':'NFL'},timing_report={},sim_report={'quarterback_pipeline':stages})
        text=format_build_report(json.loads(json.dumps(record)))
        self.assertIn('mean top-1% 8.50%',text)
        self.assertIn('2 QB: 1/2 (50.0%)',text)
        self.assertEqual(format_quarterback_pipeline({}),[])
