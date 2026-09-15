"""Held-out ranking diagnostics; audit outcomes never drive selection."""
import statistics
import time
from lineup_ranking import ranked_lineups, finish_rank


def audit_ranking(reference, simulate, signature, *, deadline, cancelled=lambda: False):
    started = time.perf_counter()
    report = dict(status='skipped', reason='insufficient time', scenarios=0,
                  candidate_count=len(reference), seed=481516, requested_scenarios=2000)
    if cancelled():
        report['reason'] = 'cancelled'
        return report
    if len(reference) < 20 or min((int(getattr(lu, 'sim_metrics', {}).get('sim_scenarios', 0)) for lu in reference), default=0) < 1000:
        report['reason'] = 'insufficient validated candidates or scenarios'
        return report
    if deadline - started < 120:
        return report
    end = min(deadline, started + 120)
    result = simulate(lambda: cancelled() or time.perf_counter() >= end)
    report['seconds'] = round(time.perf_counter() - started, 2)
    report['scenarios'] = int(result.get('report', {}).get('scenarios', 0))
    audit = result.get('lineups') or []
    if report['scenarios'] < 2000 or len(audit) != len(reference):
        report.update(status='incomplete', reason='audit sample did not finish')
        return report
    original = ranked_lineups(reference)
    repeated = ranked_lineups(audit)
    original_keys = [signature(lu) for lu in original]
    repeated_keys = [signature(lu) for lu in repeated]
    if len(set(original_keys)) != len(original_keys) or set(original_keys) != set(repeated_keys):
        report.update(status='incomplete', reason='candidate identities did not match')
        return report
    # Fixed top 50 for larger banks, never the entire bank or requested output.
    k = min(50, len(original) // 4)
    positions = {key: index + 1 for index, key in enumerate(repeated_keys)}
    shifts = [abs(positions[key] - (index + 1)) for index, key in enumerate(original_keys[:k])]
    report.update(status='completed', reason='', leading_count=k,
                  overlap_pct=round(100 * len(set(original_keys[:k]) & set(repeated_keys[:k])) / k, 1),
                  median_rank_shift=statistics.median(shifts),
                  worst_audit_rank=max(positions[key] for key in original_keys[:k]),
                  boundary_tied=finish_rank(original[k-1]) == finish_rank(original[k]) or finish_rank(repeated[k-1]) == finish_rank(repeated[k]))
    return report


def format_stability(report):
    if not report:
        return []
    if report.get('status') != 'completed':
        return [f"- Ranking audit: {report.get('status')} — {report.get('reason')}; {report.get('scenarios', 0):,}/2,000 scenarios"]
    return [f"- Independent ranking audit: {report['scenarios']:,} new scenarios; same {report['candidate_count']:,} candidates; fresh opponent field",
            f"- Top {report['leading_count']} overlap: {report['overlap_pct']:.1f}%; median absolute rank movement: {report['median_rank_shift']:g}; worst audit rank among original leaders: {report['worst_audit_rank']}",
            '- Audit measures sensitivity to scenario and opponent sampling within this model; it does not measure historical accuracy and is not used for selection.'
            + (' Exact ties occur at a group boundary.' if report.get('boundary_tied') else '')]
