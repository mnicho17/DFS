"""Compare overlapping same-date standings without assuming identical slates."""
import json
from itertools import combinations
from results_audit import _player_results


def reconciliation_report(conn):
    from learning_db import _normalize_roster_token
    records = []
    for ident, payload, path in conn.execute('SELECT c.import_id,c.payload,h.source_path FROM construction_reviews c JOIN historical_imports h ON h.import_id=c.import_id'):
        info = json.loads(payload)
        base = dict(conn.execute('SELECT player,points FROM contest_player_scores WHERE import_id=?', (ident,)))
        if not info.get('date') or not base:
            continue
        slots, _, problem = _player_results(path or '', _normalize_roster_token)
        records.append((info, base, slots, problem))
    lines = ['', 'Cross-contest score reconciliation']
    pairs = 0
    for a, b in combinations(records, 2):
        if a[0]['date'] != b[0]['date']:
            continue
        shared = sorted(a[1].keys() & b[1].keys())
        if not shared:
            continue
        pairs += 1
        lines.append(f"- {a[0]['date']}: {a[0]['name']} versus {b[0]['name']}")
        conflicts = [k for k in shared if abs(a[1][k]-b[1][k]) > .02]
        lines.append(f'  Base scores: {len(shared)} shared players; {len(conflicts)} conflicting scores (>0.02 DK points).')
        for k in conflicts[:15]:
            lines.append(f'    {k}: {a[1][k]:.2f} versus {b[1][k]:.2f}.')
        if len(conflicts)>15:
            lines.append(f'    {len(conflicts)-15} additional conflicts omitted.')
        for left, right in ((a,b),(b,a)):
            missing = sorted(left[1].keys()-right[1].keys())
            if missing:
                lines.append(f"  Base players only in {left[0]['name']}: {len(missing)}; " + ', '.join(missing[:15]) + (f' (+{len(missing)-15} more)' if len(missing)>15 else '') + '.')
        if not a[3] and not b[3]:
            common = sorted(a[2].keys() & b[2].keys())
            changed = [k for k in common if abs(a[2][k]-b[2][k])>.02]
            only_a = set(a[2])-set(b[2]); only_b = set(b[2])-set(a[2])
            lines.append(f'  Original slot tables: {len(common)} shared slots; {len(changed)} score differences; {len(only_a)} / {len(only_b)} slots present in only one file (first / second).')
            for label, missing in [('first', only_a), ('second', only_b)]:
                if missing:
                    lines.append(f'    Slots only in {label}: ' + ', '.join(k.replace('@cpt:', 'Captain: ') for k in sorted(missing)[:15]) + (f' (+{len(missing)-15} more)' if len(missing)>15 else '') + '.')
            for k in changed[:15]:
                label = k.replace('@cpt:', 'Captain: ')
                lines.append(f'    {label}: {a[2][k]:.2f} versus {b[2][k]:.2f}.')
            for info, base, slots, _ in (a,b):
                bad = [k for k in slots if k.startswith('@cpt:') and k[5:] in base and abs(slots[k]-1.5*base[k[5:]])>.02]
                if bad:
                    lines.append(f"  Captain/base inconsistencies in {info['name']}: " + ', '.join(k[5:] for k in bad[:15]) + '.')
            if not changed and not only_a and not only_b:
                lines.append('  Slot score tables agree within tolerance.')
            elif not changed:
                lines.append('  Different table fingerprints are explained by slot coverage; shared scores agree within tolerance.')
        else:
            lines.append('  Original slot tables unavailable; comparison uses cached base scores only.')
    if not pairs:
        lines.append('- No dated contest pair with overlapping player scores is available.')
    lines.append('- Dates are filename-derived and unverified. Shared players identify comparison candidates, not verified identical games. Missing rows are not zero scores. Conflicts are reported, never silently overwritten; conflicting player/date scores are excluded from performance averages.')
    return lines
