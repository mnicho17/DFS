"""Describe actual sampled entries without changing the opponent model."""
from collections import Counter
import math
from optimizers import _pkey, _salary, _cpt_salary


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def summarize_field(entries, players, *, showdown=False, salary_cap=50000, fallback=False):
    n = len(entries)
    if not n:
        return {'available': False, 'entries': 0}
    total, captain, flex, signatures, splits, stacks = (Counter() for _ in range(6))
    salaries = []
    both_dst = specialists = 0
    known = {_pkey(p): p for p in players}
    for entry in entries:
        roster = [entry['Captain']] + list(entry['Flex']) if showdown else list(entry)
        keys = [_pkey(p) for p in roster]
        known.update({_pkey(p): p for p in roster})
        total.update(set(keys))
        signatures[(keys[0], tuple(sorted(keys[1:]))) if showdown else tuple(sorted(keys))] += 1
        if showdown:
            captain[keys[0]] += 1
            flex.update(keys[1:])
        salary = (_cpt_salary(roster[0]) + sum(_salary(p) for p in roster[1:])) if showdown else sum(_salary(p) for p in roster)
        salaries.append(salary)
        teams = Counter(str(p.get('Team') or '').upper() for p in roster)
        splits['-'.join(str(count) for count in sorted(teams.values(), reverse=True))] += 1
        pos = lambda p: str(p.get('Position') or '').upper().replace('D/ST', 'DST').split('/')[0]
        dst = sum(pos(p) == 'DST' for p in roster)
        both_dst += dst >= 2
        specialists += sum(pos(p) in ('DST', 'K') for p in roster) >= 3
        for qb in (p for p in roster if pos(p) == 'QB'):
            receivers = sum(pos(p) in ('WR', 'TE') and p.get('Team') == qb.get('Team') for p in roster)
            stacks[str(receivers)] += 1
    def rows(counter, field):
        values = []
        for key, player in known.items():
            supplied = number(player.get(field))
            observed = 100 * counter[key] / n
            values.append(dict(label=f"{player.get('Name', key)} [{player.get('Team', '')} {player.get('Position', '')}]",
                               count=counter[key], sampled_pct=round(observed, 3), input_pct=supplied,
                               gap_pp=round(observed-supplied, 3) if supplied is not None else None))
        return sorted(values, key=lambda r: (-(abs(r['gap_pp']) if r['gap_pp'] is not None else -1), r['label']))
    return dict(available=True, entries=n, unique_entries=len(signatures),
                repeated_entry_pct=round(100 * (n-len(signatures)) / n, 2),
                largest_duplicate_count=max(signatures.values()), candidate_fallback=fallback,
                salary_mean=round(sum(salaries)/n, 2), salary_min=min(salaries), salary_max=max(salaries),
                at_cap_pct=round(100 * sum(s == salary_cap for s in salaries)/n, 2),
                team_splits=dict(sorted(splits.items())), qb_receiver_stacks=dict(sorted(stacks.items())),
                qb_stack_observations=sum(stacks.values()), both_defenses_pct=round(100*both_dst/n, 2),
                three_specialists_pct=round(100*specialists/n, 2),
                ownership=rows(total, 'ProjOwnPct'),
                captain_ownership=rows(captain, 'ProjCptOwnPct') if showdown else [],
                flex_ownership=rows(flex, 'ProjFlexOwnPct') if showdown else [],
                ownership_sum_pct=round(100*sum(total.values())/n, 3), showdown=showdown)


def format_field(report):
    if not report.get('available'):
        return []
    lines = ['', 'Sampled opponent field',
             f"- Entries: {report['entries']:,}; unique: {report['unique_entries']:,}; repeated copies beyond the first: {report['repeated_entry_pct']:.2f}%; largest identical group: {report['largest_duplicate_count']}",
             f"- Salary: average ${report['salary_mean']:,.0f}; range ${report['salary_min']:,.0f}–${report['salary_max']:,.0f}; at cap {report['at_cap_pct']:.1f}%",
             '- Team-count splits: ' + '; '.join(f'{k}: {v}' for k,v in report['team_splits'].items()),
             '- Same-team WR/TE per QB: ' + '; '.join(f'{k} receivers: {v}' for k,v in report['qb_receiver_stacks'].items()) + f" ({report['qb_stack_observations']} QB observations)",
             f"- Sampled total ownership sums to {report['ownership_sum_pct']:.1f}% across players."]
    if report['showdown']:
        lines.append(f"- Both defenses: {report['both_defenses_pct']:.1f}%; three-plus kickers/defenses: {report['three_specialists_pct']:.1f}%")
    if report['candidate_fallback']:
        lines.append('- Field generation failed; candidates were used as fallback opponents. This is not an independently generated field.')
    for key, label in [('ownership', 'Total'), ('captain_ownership', 'Captain'), ('flex_ownership', 'FLEX')]:
        rows = report[key]
        if not rows:
            continue
        supplied = [r for r in rows if r['input_pct'] is not None]
        leaders = sorted(rows, key=lambda r: (-r['sampled_pct'], r['label']))[:5]
        lines.append(f'- {label} most sampled: ' + '; '.join(f"{r['label']} {r['sampled_pct']:.2f}%" for r in leaders))
        lines.append(f'- {label} ownership: {len(supplied)}/{len(rows)} players have recorded inputs. Largest absolute differences:')
        for row in supplied[:5]:
            lines.append(f"  - {row['label']}: input {row['input_pct']:.2f}%; sampled {row['sampled_pct']:.2f}%; difference {row['gap_pp']:+.2f} percentage points")
        if not supplied:
            lines.append('  - No recorded ownership inputs to compare.')
    lines.append('- Ownership inputs are sampling weights, not guaranteed field exposures. Missing slot inputs can use total-ownership or projection fallbacks. These are simulated entries, not actual contest results; bootstrap copies are not counted again.')
    return lines
