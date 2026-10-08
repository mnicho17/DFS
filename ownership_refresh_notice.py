"""Observational ownership provenance; never changes player or scoring inputs."""
from collections import Counter


def describe(players, refresh=None):
    counts = Counter(str(p.get('OwnershipSource') or 'Not recorded') for p in players)
    sources = ', '.join(f'{source}: {count}' for source, count in sorted(counts.items()))
    fallback = int((refresh or {}).get('ownership_simulation_replaced', 0) or 0)
    # A fresh simulation supersedes the saved refresh notice, including on replay.
    active = bool(fallback and counts.get('Quick roster-slot estimate'))
    notice = (f'Live refresh replaced simulated ownership for {fallback} players with quick estimates. '
              'Run ownership simulation again to use simulated ownership.' if active else '')
    return dict(sources=sources, notice=notice)
