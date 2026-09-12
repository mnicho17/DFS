"""Read saved ownership comparisons without changing SIM scores or selection."""
import json
import math
from pathlib import Path
from build_snapshots import fingerprint
from repeatability import candidates, identity, model_version
from ownership_sensitivity import load_sensitivity_bank, PROFILES

COLUMNS = ('Own baseline %', 'Lowest tested %', 'Own drop pp', 'Test rank min', 'Test rank max')
PROJECTION_COLUMNS = ('Proj baseline %', 'Proj lowest %', 'Proj drop pp', 'Proj rank min', 'Proj rank max')
KEYS = ('baseline', 'lowest', 'drop', 'rank_min', 'rank_max')

def roster_key(lineup, kind):
    roster = [lineup['Captain']] + sorted(lineup['Flex'], key=lambda p: fingerprint(p)) if kind == 'showdown' else sorted(lineup, key=lambda p: fingerprint(p))
    return fingerprint({'kind': kind, 'roster': roster})

def load_review(path, bank_folder, comparison='ownership'):
    path = Path(path)
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError('Comparison exceeds 100 MB.')
    report = json.loads(path.read_text(encoding='utf-8'))
    if comparison not in ('ownership','projection') or report.get('comparison_type','ownership') != comparison:
        raise ValueError('Choose the matching comparison type.')
    profiles=PROFILES
    if comparison=='projection':
        from projection_sensitivity import PROFILES as profiles
    bank_id = report.get('bank_id', '')
    if len(bank_id) != 64 or any(c not in '0123456789abcdef' for c in bank_id):
        raise ValueError('Invalid bank ID.')
    bank = load_sensitivity_bank(Path(bank_folder) / (bank_id + '.dfsbank'))
    payload = bank['payload']
    if report.get('status') != 'completed' or report.get('completed_batches', 0) != report.get('requested_batches') or not 1 <= report.get('completed_batches', 0) <= 5:
        raise ValueError('Choose a completed comparison with all requested batches.')
    if report.get('kind') != payload['kind'] or report.get('input_id') != payload['input_id'] or report.get('saved_model') != payload['model_version']:
        raise ValueError('Comparison does not match its saved bank.')
    reference = candidates(payload)
    if report.get('candidate_count') != len(reference) or len(report.get('rows', [])) != 3 * len(reference):
        raise ValueError('Incomplete candidate comparison.')
    grouped = {}
    for row in report['rows']:
        rank = row.get('saved_rank'); profile = row.get('profile')
        if type(rank) is not int or not 1 <= rank <= len(reference) or profile not in profiles or (rank, profile) in grouped:
            raise ValueError('Invalid or duplicate comparison row.')
        for key in ('mean_top1', 'best_rank', 'worst_rank'):
            value = row.get(key)
            if not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError('Missing comparison values.')
        if not 0 <= row['mean_top1'] <= 100 or not 1 <= row['best_rank'] <= row['worst_rank'] <= len(reference):
            raise ValueError('Comparison values are out of range.')
        grouped[rank, profile] = row
    rows = []
    for rank, lineup in enumerate(reference, 1):
        values = [grouped[rank, p] for p in profiles]
        base = values[0]['mean_top1']; lowest = min(v['mean_top1'] for v in values)
        players = [lineup['Captain']] + lineup['Flex'] if payload['kind'] == 'showdown' else list(lineup)
        label = ' | '.join(('CPT: ' if payload['kind'] == 'showdown' and i == 0 else '') + str(p.get('Name', '')) for i, p in enumerate(players))
        rows.append(dict(saved_rank=rank, lineup=label, key=roster_key(lineup, payload['kind']),
                         baseline=base, lowest=lowest, drop=base-lowest,
                         rank_min=min(v['best_rank'] for v in values), rank_max=max(v['worst_rank'] for v in values)))
    return dict(report=report, rows=rows, kind=payload['kind'], comparison=comparison, compatible=report.get('current_model') == model_version())

def comparison_value(window, lineup, kind, comparison='ownership'):
    review = getattr(window, '_' + kind + '_' + comparison + '_review', None)
    if not review or not review['compatible']:
        return None
    try:
        return review['lookup'].get(roster_key(lineup, kind))
    except (TypeError, ValueError):
        return None

def tooltip(review):
    r = review['report']
    return (f"Saved {review.get('comparison','ownership')} comparison: bank {r['bank_id'][:12]}; {r['completed_batches']} batches, {r['scenarios']:,} scenarios/profile.\n"
            'Baseline is the paired comparison baseline, not the original build rate. Lowest tested is the lowest profile-average top1 rate, including baseline; drop is baseline minus lowest.\n'
            'Rank min/max span all profiles and completed batches within the saved candidate bank. These are tested model outcomes, not confidence bounds or guaranteed worst cases. Sorting does not optimize a portfolio. Ownership and projection checks are separate experiments; baselines may differ and no combined stress is measured.')
