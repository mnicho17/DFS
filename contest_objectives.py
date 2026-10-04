"""Recorded contest intent, independent of the executed selection strategy.

Execution defaults are deliberately separate from historical evidence. Never
derive an objective from a contest name, payout table, or observed result.
"""
from collections import Counter

TOURNAMENT = 'TOURNAMENT'
DOUBLE_UP = 'DOUBLE_UP'
MULTIPLIER = 'MULTIPLIER'
OBJECTIVES = (TOURNAMENT, DOUBLE_UP, MULTIPLIER)
EXECUTION_DEFAULT = TOURNAMENT
_LABELS = {TOURNAMENT: 'Tournament', DOUBLE_UP: 'Double-Up', MULTIPLIER: 'Multiplier'}
_ALIASES = {'TOURNAMENT': TOURNAMENT, 'GPP': TOURNAMENT,
            'DOUBLE_UP': DOUBLE_UP, 'DOUBLEUP': DOUBLE_UP, 'MULTIPLIER': MULTIPLIER}
FRAMEWORK_NOTE = 'Recorded intent alone does not establish the strategy executed by an older build.'


def recorded_objective(value):
    """Canonical recorded evidence, or None for absent/unrecognized evidence."""
    if not isinstance(value, str):
        return None
    key = '_'.join(value.strip().upper().replace('-', ' ').replace('_', ' ').split())
    return _ALIASES.get(key)


def normalize_objective(value, *, default=TOURNAMENT):
    """Execution/configuration normalization; always return a canonical value."""
    return recorded_objective(value) or recorded_objective(default) or EXECUTION_DEFAULT


def objective_label(value):
    """Execution label. Use objective_evidence_label for historical artifacts."""
    return _LABELS[normalize_objective(value)]


def objective_evidence_label(value):
    return _LABELS.get(recorded_objective(value), 'Not recorded')


def objective_report_label(value):
    recorded = recorded_objective(value)
    label = objective_evidence_label(recorded)
    if recorded in (DOUBLE_UP, MULTIPLIER):
        label += ' (recorded intent; see selection strategy for executed behavior)'
    return label


def objective_counts(values):
    counts = Counter(recorded_objective(value) for value in values)
    return {objective_evidence_label(key): counts[key] for key in (*OBJECTIVES, None)}
