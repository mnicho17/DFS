"""Held-out ranking diagnostics; audit outcomes never drive selection."""
import statistics
import time
from lineup_ranking import ranked_lineups, finish_rank


def audit_ranking(reference, simulate, signature, *, deadline, cancelled=lambda: False):
    started = time.perf_counter()
    report = dict(status='skipped', reason='insufficient time', scenarios=0,
                  candidate_count=len(reference), seed=481516, requested_scenarios=2000,
                  time_budget_seconds=120, available_seconds=round(max(0, deadline-started), 2))
    if cancelled():
        report.update(reason='cancelled', stop_reason='cancelled')
        return report
    if len(reference) < 20 or min((int(getattr(lu, 'sim_metrics', {}).get('sim_scenarios', 0)) for lu in reference), default=0) < 1000:
        report['reason'] = 'insufficient validated candidates or scenarios'
        return report
    if deadline - started < 120:
        return report
    end = min(deadline, started + 120)
    stopped = []
    def audit_stop():
        reason = ''
        if cancelled():
            reason = 'cancelled'
        elif time.perf_counter() >= end:
            reason = 'audit_time_limit' if end < deadline else 'build_deadline'
        if reason and not stopped:
            stopped.append(reason)
        return bool(reason)
    result = simulate(audit_stop)
    report['seconds'] = round(time.perf_counter() - started, 2)
    report['scenarios'] = int(result.get('report', {}).get('scenarios', 0))
    audit = result.get('lineups') or []
    if cancelled() or stopped == ['cancelled']:
        report.update(status='incomplete', reason='cancelled', stop_reason='cancelled')
        return report
    if report['scenarios'] < 2000:
        stop_reason = stopped[0] if stopped else 'partial_sample'
        reason = {'audit_time_limit': 'two-minute audit limit reached',
                  'build_deadline': 'reserved build deadline reached'}.get(stop_reason, 'audit sample did not finish')
        report.update(status='incomplete', reason=reason, stop_reason=stop_reason)
        return report
    if len(audit) != len(reference):
        report.update(status='incomplete', reason='candidate count did not match', stop_reason='candidate_mismatch')
        return report
    original = ranked_lineups(reference)
    repeated = ranked_lineups(audit)
    original_keys = [signature(lu) for lu in original]
    repeated_keys = [signature(lu) for lu in repeated]
    if len(set(original_keys)) != len(original_keys) or set(original_keys) != set(repeated_keys):
        report.update(status='incomplete', reason='candidate identities did not match', stop_reason='candidate_mismatch')
        return report
    # Fixed top 50 for larger banks, never the entire bank or requested output.
    k = min(50, len(original) // 4)
    positions = {key: index + 1 for index, key in enumerate(repeated_keys)}
    shifts = [abs(positions[key] - (index + 1)) for index, key in enumerate(original_keys[:k])]
    if cancelled():
        report.update(status='incomplete', reason='cancelled', stop_reason='cancelled')
        return report
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
        lines = [f"- Ranking audit: {report.get('status')} — {report.get('reason')}; {report.get('scenarios', 0):,}/{report.get('requested_scenarios', 2000):,} scenarios"]
        if 'time_budget_seconds' in report:
            runtime = (f"{report['seconds']:g}s elapsed; " if 'seconds' in report else '')
            lines.append(f"- Ranking audit budget: {runtime}{report['time_budget_seconds']:g}s diagnostic cap; {report.get('available_seconds', 0):g}s available before reserved selection time. Incomplete audit results are not used for selection.")
        return lines
    runtime = (f"; {report['seconds']:g}s elapsed; {report['time_budget_seconds']:g}s diagnostic allowance"
               if 'seconds' in report and 'time_budget_seconds' in report else '')
    return [f"- Independent ranking audit: {report['scenarios']:,} new scenarios; same {report['candidate_count']:,} candidates; fresh opponent field" + runtime,
            f"- Top {report['leading_count']} overlap: {report['overlap_pct']:.1f}%; median absolute rank movement: {report['median_rank_shift']:g}; worst audit rank among original leaders: {report['worst_audit_rank']}",
            '- Audit measures sensitivity to scenario and opponent sampling within this model; it does not measure historical accuracy and is not used for selection.'
            + (' Exact ties occur at a group boundary.' if report.get('boundary_tied') else '')]
