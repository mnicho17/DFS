"""Read-only provenance notices for recorded defense forecasts, never calibration."""
import json
import math


def _number(value, signed=False):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and (signed or result >= 0) else None


def summarize_defense_forecasts(players):
    records = {}; reviewed = set(); unknown = set(); identities = set()
    for player in players:
        if str(player.get('Position') or '').upper() not in {'DST', 'D/ST', 'DEF'}:
            continue
        identity = (str(player.get('FlexID') or player.get('Name') or ''),
                    str(player.get('Team') or ''))
        identities.add(identity)
        source = str(player.get('ProjectionSource') or 'Legacy / unknown source')
        historical = source == 'Historical average estimate'
        unverified = source == 'Legacy / unknown source'
        if historical:
            reviewed.add(identity)
        if unverified:
            unknown.add(identity)
        total = _number(player.get('NFLVegasGameTotal'))
        row = dict(player_id=identity[0], name=str(player.get('Name') or 'Unknown'),
                   team=identity[1], source=source,
                   base_forecast=_number(player.get('BaseProjection')),
                   effective_forecast=_number(player.get('FlexProjection')),
                   historical_ppg=_number(player.get('HistoricalPPG')),
                   game_total=total if total and total > 0 else None,
                   spread=_number(player.get('NFLVegasSpread'), signed=True),
                   historical_average=historical, unknown_provenance=unverified)
        # Identical copies collapse, differing recorded provenance stays visible.
        records[json.dumps(row, sort_keys=True, allow_nan=False)] = row
    rows = sorted(records.values(), key=lambda r: (r['name'], r['team'], r['player_id'],
                                                  json.dumps(r, sort_keys=True)))
    return dict(version=1, checked=len(identities), review_players=len(reviewed),
                unknown_players=len(unknown), rows=rows)


def format_defense_forecasts(report):
    if not report or not report.get('checked'):
        return []
    lines = ['', 'Defense forecast review — recorded inputs',
             f"- {report['checked']} defenses; {report['review_players']} use historical-average estimates; "
             f"{report['unknown_players']} have unknown forecast provenance."]
    rows = report.get('rows') or []
    def points(value):
        return f'{value:.2f}' if value is not None else 'not recorded'
    for row in rows[:6]:
        context = (f"game total {points(row['game_total'])}; "
                   + (f"spread {row['spread']:+.2f}" if row['spread'] is not None else 'spread not recorded'))
        lines.append(f"- {row['name']} [{row['team']} DST]: {row['source']}; "
                     f"base {points(row['base_forecast'])}; effective FLEX {points(row['effective_forecast'])}; "
                     f"historical PPG {points(row['historical_ppg'])}; {context}.")
    if len(rows) > 6:
        lines.append(f'- {len(rows)-6} additional defense records retained in saved build diagnostics.')
    if report.get('review_players'):
        lines.append('- Historical averages are fallback estimates, not matchup-calibrated defense forecasts. Review the source or supply a forecast before relying on them.')
    lines.append('- Recorded game totals/spreads describe available inputs; their presence does not establish that the defense forecast was adjusted for this matchup. Missing inputs are not zero. Supplied/manual forecasts are shown without judging their accuracy. No forecasts, scoring distributions, rankings or exposure limits were changed.')
    return lines
