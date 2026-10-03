"""Showdown pre-scoring coverage, including role-specific Captain alternatives."""
from candidate_recovery import expand_candidates


def expand_capped_candidates(rows, players, requested, rules, *, salary_cap,
                             own_mode, own_weight, build_style, cancelled=lambda: False,
                             seconds=20, max_additions=None, retained=(), automatic=True,
                             salary_strategy='Balanced Spend'):
    return expand_candidates(rows, players, requested, rules, kind='showdown',
        salary_cap=salary_cap, own_mode=own_mode, own_weight=own_weight,
        build_style=build_style, cancelled=cancelled, seconds=seconds,
        max_additions=max_additions, retained=retained, automatic=automatic, salary_strategy=salary_strategy)
