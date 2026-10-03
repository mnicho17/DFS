"""Offline before/after check using a local Showdown salary file.

Run this script with --repository pointing at a clean baseline checkout to
exercise that revision with identical inputs. No live feeds or user storage.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('salary_file', type=Path)
parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args()
source = args.salary_file.resolve()
sys.path.insert(0, str(args.repository.resolve()))
from test_environment import install, network_attempts
install()
from PyQt5.QtWidgets import QApplication
from data_io import read_players_csv
from entry_safety import build_entry_safety_report
from main_window import LineupBuildWorker

app = QApplication([])
players = read_players_csv(str(source))
rules = {'min_unique': 1, 'balance_ownership': True}
before = deepcopy((players, rules))
worker = LineupBuildWorker(players, kind='showdown', sport='NFL', num_lineups=150,
    salary_cap=50000, build_style='Strategic', salary_strategy='Balanced Spend',
    portfolio_rules=rules, sim_enabled=True, compute_mode='Deep', sim_scenarios=2500,
    deep_time_limit_seconds=60,
    deep_options={'candidates': 150, 'shortlist': 150, 'field': 100, 'screening': 250})
results, errors = [], []
worker.finished.connect(results.append)
worker.error.connect(errors.append)
worker.run()
result = results[0] if results else {}
rows = result.get('lineups', [])
report = result.get('portfolio_report', {})
safety = build_entry_safety_report(rows, kind='showdown', sport='NFL', salary_cap=50000,
    player_pool=players, portfolio_report=report) if rows else {}
summary = dict(input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    game=sorted({p.get('GameKey', '') for p in players}), player_count=len(players),
    requested=150, selected=len(rows), errors=errors, safety_blockers=safety.get('blockers'),
    recovery=report.get('portfolio_recovery'), warnings=report.get('warnings', []),
    inputs_unchanged=(players, rules) == before, network_attempts=network_attempts)
print(json.dumps(summary, indent=2, sort_keys=True))
raise SystemExit(0 if len(rows) == 150 and not safety.get('blockers') and not network_attempts
                 and summary['inputs_unchanged'] else 1)
