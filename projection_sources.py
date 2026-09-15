"""Projection provenance and conservative, explicitly uncalibrated NFL fallbacks."""
import math


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def initialize_projection(player, historical=None, imported=None):
    player['HistoricalPPG'] = number(historical)
    player['ImportedProjection'] = number(imported)
    player['ProjectionInputsVersion'] = 1
    resolve_projection(player)


def resolve_projection(player):
    # Old snapshots retain their values and unknown provenance; never guess that
    # a legacy BaseProjection was a historical average or a supplied forecast.
    if not player.get('ProjectionInputsVersion') and 'ManualProjection' not in player:
        return
    manual = number(player.get('ManualProjection'))
    imported = number(player.get('ImportedProjection'))
    history = number(player.get('HistoricalPPG'))
    workload = number(player.get('WorkloadProjection'))
    kicker = number(player.get('KickerProjection'))
    if manual is not None:
        value, source = manual, 'Manual override'
    elif imported is not None:
        value, source = imported, 'Imported forecast'
    elif kicker is not None:
        value, source = kicker, 'Automatic kicker opportunities'
    elif workload is not None:
        value, source = workload, 'Automatic workload estimate'
    elif history is not None and history > 0:
        value, source = history, 'Historical average estimate'
    else:
        value, source = 0.0, 'Missing forecast'
    player['BaseProjection'] = value
    player['ProjectionSource'] = source
    player['ProjectionNeedsReview'] = source not in {'Manual override', 'Imported forecast'}
    player['FlexProjection'] = value
    player['CptProjection'] = 1.5 * value


def prepare_nfl_projections(players):
    from nfl_workload import prepare_workloads
    prepare_workloads(players)
    from nfl_kickers import prepare_kickers
    prepare_kickers(players)
    for player in players:
        if not player.get('ProjectionInputsVersion'):
            continue
        player.pop('FallbackProjection', None)
        player['ProjectionEstimateNote'] = ''
        resolve_projection(player)


def projection_note(player):
    source = player.get('ProjectionSource', 'Legacy / unknown source')
    history = player.get('HistoricalPPG')
    w = player.get('NFLWorkload') or {}
    detail = (f"Expected attempts {w.get('attempts', 0):.1f}; carries {w.get('carries', 0):.1f}; targets {w.get('targets', 0):.1f}\n{w.get('assumptions', '')}" if source == 'Automatic workload estimate' else '')
    if source == 'Automatic kicker opportunities':
        k = player.get('NFLKickerOpportunities') or {}
        detail = (f"Expected FG attempts {k.get('fga',0):.2f}; XP attempts {k.get('xpa',0):.2f}; FG accuracy {k.get('fg_rate',0):.0%}\n"
                  f"Kicking history: {k.get('games',0)} games, season {k.get('season','unknown')}\n{k.get('assumptions','')}")
    return (f'Source: {source}\nHistorical PPG: {history if history is not None else "unknown"}\n'
            + detail
            + '\nDouble-click BaseProj or AdjProj to set or clear a manual forecast.')
