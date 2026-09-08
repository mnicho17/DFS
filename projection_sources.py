"""Projection provenance and conservative, explicitly uncalibrated NFL fallbacks."""
import math
from statistics import median


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
    fallback = number(player.get('FallbackProjection'))
    if manual is not None:
        value, source = manual, 'Manual override'
    elif imported is not None:
        value, source = imported, 'Imported forecast'
    elif history is not None and history > 0:
        value, source = history, 'Historical average estimate'
    elif fallback is not None:
        value, source = fallback, 'Comparable-player estimate'
    else:
        value, source = 0.0, 'Missing forecast'
    player['BaseProjection'] = value
    player['ProjectionSource'] = source
    player['ProjectionNeedsReview'] = source not in {'Manual override', 'Imported forecast'}
    player['FlexProjection'] = value
    player['CptProjection'] = 1.5 * value


def prepare_nfl_projections(players):
    """Use two or more same-position historical peers only for known top-two roles.

    Salary scaling is a market proxy, not an opportunity forecast. Unknown roles
    and unavailable players receive no rookie estimate. Recompute from original
    history every refresh so prior estimates can never become their own evidence.
    """
    for player in players:
        if not player.get('ProjectionInputsVersion'):
            continue
        player.pop('FallbackProjection', None)
        player['ProjectionEstimateNote'] = ''
        depth = number(player.get('NFLDepthOrder')) or 0
        history = number(player.get('HistoricalPPG')) or 0
        status = str(player.get('NFLAvailability') or player.get('InjuryStatus') or '').upper()
        salary = number(player.get('FlexSalary')) or 0
        pos = str(player.get('Position') or '').upper()
        if history == 0 and depth in (1, 2) and pos in {'QB', 'RB', 'WR', 'TE'} and salary > 0 and status not in {'OUT', 'IR', 'PUP', 'INACTIVE', 'SUSPENDED'} and player.get('NFLActive') is not False:
            peers = [p for p in players if p is not player
                     and p.get('Position') == player.get('Position')
                     and (number(p.get('HistoricalPPG')) or 0) > 0
                     and (number(p.get('FlexSalary')) or 0) >= 3000
                     and (number(p.get('NFLDepthOrder')) or 0) in (1, 2, 3)]
            if len(peers) >= 2:
                estimates = [p['HistoricalPPG'] * min(1.5, max(0.5, salary / p['FlexSalary'])) for p in peers]
                player['FallbackProjection'] = round(median(estimates), 2)
                player['ProjectionEstimateNote'] = (
                    f'Uncalibrated estimate from {len(peers)} same-position historical peers; '
                    'salary ratio limited to 0.5–1.5. Known top-two role required. '
                    'Does not predict carries or targets; replace with a supplied forecast.'
                )
        resolve_projection(player)


def projection_note(player):
    source = player.get('ProjectionSource', 'Legacy / unknown source')
    history = player.get('HistoricalPPG')
    return (f'Source: {source}\nHistorical PPG: {history if history is not None else "unknown"}\n'
            + (str(player.get('ProjectionEstimateNote') or '') if source == 'Comparable-player estimate' else '')
            + '\nDouble-click BaseProj or AdjProj to set or clear a manual forecast.')
