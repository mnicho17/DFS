"""Read-only historical SIM calibration. Never imported by a live optimizer.

The input is the existing qualified pregame distribution comparison table. No
simulation is reconstructed from hindsight, and no model/settings are written.
"""
from collections import defaultdict
import datetime as dt
import json
import math
import re
import statistics

ROLES = {'Recorded unavailable', 'Recorded starters / eligible QBs', 'Backup QBs',
         'Unverified / excluded QBs', 'Other depth roles / rotation', 'Unknown recorded role'}
POSITIONS = {'QB', 'RB', 'WR', 'TE', 'K', 'DST'}


def qualified(row):
    try:
        if row['kind'] not in ('classic', 'showdown') or not row['name']:
            return False
        if not re.fullmatch('[0-9a-f]{64}', row['model']):
            return False
        kickoff = dt.datetime.fromisoformat(row['game'].split('|')[-1])
        capture = dt.datetime.fromisoformat(row['captured_at'])
        if not kickoff.tzinfo or not capture.tzinfo or capture >= kickoff:
            return False
        if not isinstance(row['scenarios'], int) or row['scenarios'] <= 0:
            return False
        numbers = ('actual', 'mean', 'p10', 'p50', 'p90', 'expected_below_p10', 'expected_above_p90')
        if not all(isinstance(row.get(k), (int, float)) and not isinstance(row[k], bool)
                   and math.isfinite(row[k]) for k in numbers):
            return False
        if any(k in row for k in ('p25', 'p75')):
            if not all(isinstance(row.get(k), (int, float)) and math.isfinite(row[k]) for k in ('p25', 'p75')):
                return False
            if not row['p10'] <= row['p25'] <= row['p50'] <= row['p75'] <= row['p90']:
                return False
        return (row['p10'] <= row['p50'] <= row['p90']
            and all(0 <= row[k] <= 1 for k in ('expected_below_p10', 'expected_above_p90')))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _metrics(rows):
    mean = statistics.mean
    result = dict(player_games=len(rows), games=len({r['game'] for r in rows}),
        below_p10_pct=100 * mean(r['actual'] < r['p10'] for r in rows),
        inside_p10_p90_pct=100 * mean(r['p10'] <= r['actual'] <= r['p90'] for r in rows),
        above_p90_pct=100 * mean(r['actual'] > r['p90'] for r in rows),
        expected_below_p10_pct=100 * mean(r['expected_below_p10'] for r in rows),
        expected_above_p90_pct=100 * mean(r['expected_above_p90'] for r in rows),
        mean_score_mae=mean(abs(r['actual'] - r['mean']) for r in rows),
        actual_minus_mean_bias=mean(r['actual'] - r['mean'] for r in rows),
        median_score_mae=mean(abs(r['actual'] - r['p50']) for r in rows))
    # At-or-below CDF coverage includes ties. Tail metrics above use strict < / >.
    result['quantile_coverage'] = {}
    for q in ('p10', 'p25', 'p50', 'p75', 'p90'):
        selected = [r for r in rows if isinstance(r.get(q), (int, float)) and math.isfinite(r[q])]
        result['quantile_coverage'][q] = dict(player_games=len(selected),
            at_or_below_pct=100 * mean(r['actual'] <= r[q] for r in selected) if selected else None)
    return result


def summarize(rows):
    """Latest pregame capture per format/model/player/game; formats aren't pooled."""
    valid = [r for r in rows if isinstance(r, dict) and qualified(r)]
    outcomes = defaultdict(list)
    for row in valid:
        outcomes[(row['game'], row['name'])].append(row['actual'])
    conflicts = {key for key, values in outcomes.items() if max(values) - min(values) > .02}
    retained = {}
    for row in sorted(valid, key=lambda r: (dt.datetime.fromisoformat(r['captured_at']),
            str(r.get('capture_id', '')), json.dumps(r, sort_keys=True)), reverse=True):
        if (row['game'], row['name']) not in conflicts:
            retained.setdefault((row['kind'], row['model'], row['game'], row['name']), row)
    kept = list(retained.values())
    groups = defaultdict(list)
    for row in kept:
        pos = row.get('position') if row.get('position') in POSITIONS else 'Unknown'
        role = row.get('role') if row.get('role') in ROLES else 'Unknown recorded role'
        for axis, label in (('all', 'All'), ('position', pos), ('role', role), ('position_role', pos + ' / ' + role)):
            groups[(row['kind'], row['model'], axis, label)].append(row)
    result = dict(schema_version=1, status='available' if kept else 'unavailable', report_only=True,
        input_comparisons=len(rows), excluded_invalid=len(rows)-len(valid),
        excluded_repeated_or_conflicting=len(valid)-len(kept), conflicting_player_games=len(conflicts),
        unique_player_games=len({(r['game'], r['name']) for r in kept}), independent_games=len({r['game'] for r in kept}),
        groups=[], basis='existing completed pregame captures; latest per format/model/player/game; no reconstructed ranges')
    for (kind, model, axis, label), values in sorted(groups.items()):
        by_game = defaultdict(list)
        for row in values:
            by_game[row['game']].append(row)
        game_metrics = [_metrics(group) for group in by_game.values()]
        pooled = _metrics(values)
        # One weight per game prevents large slates/repeated contests from
        # masquerading as independent model validations.
        equal_game = {key: statistics.mean(g[key] for g in game_metrics) for key in (
            'below_p10_pct', 'inside_p10_p90_pct', 'above_p90_pct', 'mean_score_mae', 'actual_minus_mean_bias')}
        result['groups'].append(dict(kind=kind, model=model, axis=axis, label=label,
            player_weighted=pooled, equal_game_weighted=equal_game))
    return result


def calibration_summary(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='distribution_validations'").fetchone():
        return summarize([])
    rows = []
    for encoded, in conn.execute('SELECT payload FROM distribution_validations ORDER BY import_id'):
        try:
            value = json.loads(encoded)
            if isinstance(value, dict) and isinstance(value.get('rows'), list):
                rows.extend(value['rows'])
        except (ValueError, TypeError):
            continue
    return summarize(rows)


def report_lines(summary):
    lines = ['', 'SIM distribution calibration (report only)',
        f"- Independent-game accounting: {summary['independent_games']} scheduled games; {summary['unique_player_games']} distinct player-game outcomes. Formats/models share outcomes; their samples must not be added.",
        f"- Excluded: {summary['excluded_invalid']} invalid comparisons; {summary['excluded_repeated_or_conflicting']} repeated/conflicting comparisons; {summary['conflicting_player_games']} conflicting player-game outcomes."]
    for group in summary['groups']:
        p = group['player_weighted']; g = group['equal_game_weighted']
        quantiles = ', '.join(q + '=' + (f"{v['at_or_below_pct']:.1f}% (n={v['player_games']})"
            if v['at_or_below_pct'] is not None else 'unavailable') for q, v in p['quantile_coverage'].items())
        lines.append(f"- {group['kind']} / model {group['model'][:12]} / {group['axis']}: {group['label']}; {p['games']} games, {p['player_games']} player-games. At/below {quantiles}. Equal-game below/inside/above p10-p90: {g['below_p10_pct']:.1f}/{g['inside_p10_p90_pct']:.1f}/{g['above_p90_pct']:.1f}%; mean-score MAE {g['mean_score_mae']:.2f}; actual-minus-mean bias {g['actual_minus_mean_bias']:+.2f}.")
    lines.append('- Games, not duplicated entries or Captain multipliers, define evidence units. Within-game outcomes are correlated; no independence between players or accuracy guarantee is implied. Missing legacy p25/p75 stay unavailable. No weights, forecasts or compute settings are updated.')
    return lines
