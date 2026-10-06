"""Frozen latest-main evidence and objective-only computational parity.

The fixture was captured from 46a501f6b35add46bcb1650d3d795f1216ed1584.
Wall-clock measurements, creation timestamps, objective metadata and additive
DC-02A quartiles/ledger summaries are excluded. The original source provenance
ID is pinned. All original candidate identities, numeric metrics, exports and
output ordering remain exact; DC-02A separately tests its metadata and parity.
Core Plans separately tests additive Captain policy reports. The bounded
diversity witness may reorder preselection candidates; compatibility still
requires identical candidate identities and every metric per identity, and
exact final selections, output ordering and exports.
"""
from test_environment import install, network_attempts
install()

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import main_window
import showdown_simulation
from build_diagnostics import create_build_diagnostic, format_build_report
from candidate_library import roster_keys
from entry_safety import _expected_export_row, build_entry_safety_report
from test_nfl_logic import _fixture_players
from test_showdown_performance import _showdown_players


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items()
                if k not in {'created_at', 'started_at', 'finished_at', 'seconds',
                             'contest_objective', 'objective', 'p25', 'p75'}
                and not k.endswith('_seconds')}
    if isinstance(value, (list, tuple)):
        return [stable(v) for v in value]
    return value


def evidence(kind, objective=None, compute_telemetry=False):
    players = _fixture_players() if kind == 'classic' else _showdown_players()
    original = copy.deepcopy(players)
    kwargs = {} if objective is None else {'contest_objective': objective}
    worker = main_window.LineupBuildWorker(
        players, kind=kind, num_lineups=4, salary_cap=50000,
        salary_strategy='Balanced Spend', sim_enabled=True, sim_scenarios=250,
        compute_mode='Fast' if kind == 'classic' else 'Deep',
        deep_time_limit_seconds=300, compute_telemetry=compute_telemetry,
        deep_options=dict(candidates=40, shortlist=20, field=80, screening=250),
        **kwargs)
    stages = []
    target = main_window if kind == 'classic' else showdown_simulation
    name = 'simulate_nfl_contest' if kind == 'classic' else 'simulate_showdown'
    real = getattr(target, name)

    def simulate(rows, *args, **kwargs):
        candidates = [roster_keys(row, kind) for row in rows]
        result = real(rows, *args, **kwargs)
        stages.append(dict(candidates=candidates, count=len(candidates),
            scored=[dict(signature=roster_keys(row, kind),
                         metrics=stable(row.sim_metrics)) for row in result['lineups']],
            report=stable(result['report'])))
        return result

    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)
    # Source-byte provenance changes when observational hooks are added. Pin the
    # original model ID in this frozen fixture only; bank contents, scores and
    # ordering still participate in the exact comparison. Runtime IDs stay strict.
    with patch.object(target, name, side_effect=simulate), patch('repeatability.model_version',
            return_value='88112796771aa83e4212aef834f3ffb2674d78df0a8a711e488b1083b7809ae5'):
        worker.run()
    assert not errors, errors
    assert len(finished) == 1 and len(finished[0]['lineups']) == 4
    assert players == original, 'worker mutated fixture inputs'
    result = finished[0]
    result['sim_report'].pop('compute_ledger', None)
    rows = result['lineups']
    exports = [_expected_export_row(row, kind, 'NFL') for row in rows]
    context = dict(sport='NFL', kind=kind, requested_count=4, salary_cap=50000,
                   settings=dict(build_style='Strategic', salary_strategy='Balanced Spend',
                                 sim_enabled=True, sim_scenarios=250, **kwargs))
    diagnostic = create_build_diagnostic(context=context,
        timing_report=stable(result['timing_report']),
        portfolio_report=stable(result['portfolio_report']),
        sim_report=stable(result['sim_report']), displayed_count=4, lineups=rows)
    diagnostic = stable(diagnostic)
    # Legacy reports have no objective line; the new additive line is tested separately.
    report = '\n'.join(line for line in format_build_report(diagnostic).splitlines()
                       if not line.startswith('- Objective:'))
    return stable(dict(stages=stages, candidate_count=result['candidate_count'],
        selected=[dict(signature=roster_keys(row, kind), metrics=getattr(row, 'sim_metrics', {}))
                  for row in rows], dk_export=exports,
        entry_safety=build_entry_safety_report(rows, kind=kind, sport='NFL', salary_cap=50000,
            export_rows=exports, portfolio_report=result['portfolio_report'], player_pool=players),
        portfolio_report=result['portfolio_report'], diagnostic=diagnostic, build_report=report))


class ObjectiveParityTests(unittest.TestCase):
    @staticmethod
    def compatible_evidence(value):
        value = copy.deepcopy(value)
        report = value['portfolio_report']
        # Only additive safety reporting is excluded from the older fixture.
        report.pop('showdown_policy', None)
        specialist=report.pop('specialist_diagnostics',None)
        if specialist:
            from specialist_diagnostics import text
            observational_lines=set(text(specialist))
            report['text']='\n'.join(line for line in report['text'].split('\n') if line not in observational_lines)
            value['build_report']='\n'.join(line for line in value['build_report'].split('\n') if line not in observational_lines)
        value['diagnostic']['portfolio'].pop('specialist_diagnostics',None)
        report['text'] = '\n'.join(line for line in report['text'].split('\n')
                                   if line.strip() and not line.startswith('Showdown guardrails:'))
        feasibility = value['diagnostic'].get('portfolio_feasibility') or {}
        method = feasibility.pop('method', None)
        if method:
            value['build_report'] = value['build_report'].replace(f' via {method}', '')
        for stage in value['stages']:
            stage['candidates'].sort()
            stage['scored'].sort(key=lambda row: row['signature'])
        return value

    def test_classic_golden_and_all_objectives(self):
        self.check_kind('classic')

    def test_showdown_golden_and_all_objectives(self):
        self.check_kind('showdown')

    def check_kind(self, kind):
        fixture = json.loads((Path(__file__).parent / 'tests' / 'fixtures' / 'co01-golden.json').read_text())
        expected = fixture[kind]
        # Portfolio report ties inherit set order on main. Pin hash seed in a
        # fresh isolated process instead of sorting or weakening exact evidence.
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'evidence.json'
            run = subprocess.run([sys.executable, str(Path(__file__).resolve()), kind, str(output)],
                env=dict(os.environ, PYTHONHASHSEED='0'), capture_output=True, text=True, timeout=180)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            actual = json.loads(output.read_text())
        for objective, result in actual.items():
            with self.subTest(kind=kind, objective=objective):
                self.assertEqual(self.compatible_evidence(result),
                                 self.compatible_evidence(expected))
        self.assertEqual(network_attempts, [])


if __name__ == '__main__':
    kind, destination = sys.argv[1:]
    values = {str(objective): evidence(kind, objective)
              for objective in (None, 'TOURNAMENT', 'DOUBLE_UP', 'MULTIPLIER')}
    assert not network_attempts, network_attempts
    Path(destination).write_text(json.dumps(values, sort_keys=True))
