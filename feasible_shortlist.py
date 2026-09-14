"""Keep a complete compliant portfolio available through SIM shortlisting."""
import time


def preserve(rows, shortlist_fn, limit, requested, *, kind, rules, retained,
             reserved, signature, individual_ranking, deadline, cancelled):
    from portfolio_rules import select_portfolio
    start = time.perf_counter()
    witness = []
    if not cancelled() and start < deadline:
        found = select_portfolio(rows, requested, kind=kind, rules=rules,
            retained_lineups=retained, individual_ranking=individual_ranking,
            allow_relaxation=False, feasibility_only=True,
            repair_time_limit=min(20, deadline-start))
        witness = found['lineups'] if len(found['lineups']) == requested else []
    # Feasibility and retained entries take priority if reservations exceed the limit.
    required = list(dict.fromkeys([signature(lu) for lu in retained + witness]))
    ordered = required + [sig for sig in reserved if sig not in set(required)]
    short = shortlist_fn(rows, max(limit, len(required)), reserved_signatures=ordered,
                        individual_ranking=individual_ranking)
    return short, witness, dict(status='preserved' if witness else 'not found within budget',
        lineups=len(witness), searched=len(rows), seconds=round(time.perf_counter()-start, 2))
