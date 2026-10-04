"""Explicit contest execution policy, separate from historical objective labels."""
import bisect
from contest_objectives import normalize_objective, TOURNAMENT, DOUBLE_UP, MULTIPLIER
from contest_profiles import normalize_contest_profile, payout_for_tied_ranks


def execution_profile(profile, objective=None):
    intent = normalize_objective(objective if objective is not None else (profile or {}).get('objective'))
    if not profile:
        if intent != TOURNAMENT:
            raise ValueError('Double-Up and Multiplier optimization require a real contest profile: field size, entry fee and payout table. Open Settings > Contest-Aware SIM.')
        return None
    result = normalize_contest_profile(profile)
    result['objective'] = intent
    if intent == DOUBLE_UP:
        tiers = [tier for tier in result['payouts'] if tier['amount'] > 0]
        end = 0
        for tier in tiers:
            if tier['start'] != end + 1 or tier['amount'] != tiers[0]['amount'] or tier['amount'] <= result['entry_fee']:
                raise ValueError('Double-Up requires one contiguous paid rank range starting at first place, with the same payout above the entry fee. Use Tournament or Multiplier for a different payout structure.')
            end = tier['end']
    return result


def sampled_payout(ranked, score, profile):
    """Scale sampled opponents, then split prizes over the full tied rank range."""
    size = profile['field_size']
    left = bisect.bisect_left(ranked, score)
    right = bisect.bisect_right(ranked, score)
    scale = (size - 1) / max(1, len(ranked))
    above = min(size - 1, max(0, round((len(ranked) - right) * scale)))
    ties = min(size - 1 - above, max(0, round((right - left) * scale)))
    return payout_for_tied_ranks(profile['payouts'], above + 1, min(size, above + 1 + ties))


def selection_fields(profile):
    return ('sim_cash_rate', 'sim_expected_profit', 'sim_mean') if profile['objective'] == DOUBLE_UP else (
        'sim_expected_profit', 'sim_cash_rate', 'sim_mean')


def strategy_label(profile):
    if not profile:
        return 'Tournament: top 1% → top 2% → top 5% → first-place rate → mean points'
    if profile['objective'] == DOUBLE_UP:
        return 'Double-Up: paid-finish rate → expected profit → mean points'
    return ('Multiplier' if profile['objective'] == MULTIPLIER else 'Tournament') + ': expected profit → paid-finish rate → mean points (exact payouts; ties split prizes)'


def attach_strategy(metrics, profile):
    if profile:
        from build_snapshots import fingerprint
        metrics.update(sim_selection_objective=profile['objective'],
                       sim_contest_profile_id=fingerprint(profile),
                       sim_selection_fields=list(selection_fields(profile)),
                       sim_selection_label=strategy_label(profile),
                       sim_payout_model='exact-rank-tie-split-v1')
    return metrics
