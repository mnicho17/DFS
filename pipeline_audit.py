"""Descriptive quarterback and defense counts; never changes candidates or their ranking."""
import math


def quarterback_mix(lineups, *, scored=False):
    return _position_mix(lineups, 'QB', scored=scored)


def defense_mix(lineups, *, scored=False):
    return _position_mix(lineups, 'DST', scored=scored)


def _position_mix(lineups, position, *, scored=False):
    result = {'total': 0, 'groups': {}}
    for lineup in lineups:
        roster = [lineup.get('Captain') or {}] + list(lineup.get('Flex') or []) if isinstance(lineup, dict) else list(lineup)
        positions = [str(p.get('Position') or '').upper().replace('D/ST','DST').split('/') for p in roster]
        key = str(sum(position in p for p in positions)) if positions and all(p != [''] for p in positions) else 'unknown'
        group = result['groups'].setdefault(key, {'count': 0, 'scored': 0, 'top1_sum': 0.0})
        result['total'] += 1
        group['count'] += 1
        metrics = getattr(lineup, 'sim_metrics', {}) or {}
        value = metrics.get('sim_top_one_pct') if scored else None
        if isinstance(value, (int, float)) and math.isfinite(value):
            group['scored'] += 1
            group['top1_sum'] += value
    return result


def format_quarterback_pipeline(stages):
    return _format_pipeline(stages, 'Quarterback', 'QB')


def format_defense_pipeline(stages):
    return _format_pipeline(stages, 'Defense', 'DST')


def _format_pipeline(stages, title, unit):
    if not stages:
        return []
    lines = ['', title + ' construction through the build']
    labels = {'generated': 'Generated bank', 'shortlisted': 'Screened shortlist',
              'validated': 'Independent SIM bank', 'selected': 'Selected output',
              'salary_eligible': 'After salary filtering',
              'ranked': 'Individually ranked leaders before portfolio rules'}
    for stage, data in stages.items():
        parts = []
        for key, group in sorted(data.get('groups', {}).items()):
            n = group['count']; total = data.get('total', 0)
            text = f"{key} {unit}: {n:,}/{total:,} ({100*n/max(1,total):.1f}%)"
            if group.get('scored'):
                text += f"; mean top-1% {group['top1_sum']/group['scored']:.2f}% ({group['scored']:,} scored)"
            parts.append(text)
        lines.append('- ' + labels.get(stage, stage) + ': ' + (' | '.join(parts) or 'empty'))
    lines.append('- Captain counts once. Retained lineups are included. SIM averages describe that stage and are not historical accuracy; screening and validation use different samples.')
    return lines
