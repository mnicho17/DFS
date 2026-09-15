"""Server preparation using the desktop parser, enrichment and ownership worker."""
import hashlib
from pathlib import Path
from data_io import read_players_csv
from nfl_auto_data import apply_auto_nfl_context
from nfl_simulation import build_nfl_role_pool
from nfl_workload import available
from optimizers import _pkey


def apply_ownership(players, result):
    eligible = set((result.get('meta') or {}).get('eligible_keys') or [])
    for p in players:
        p["OwnershipSource"] = "Lineup simulation"
        p["OwnershipUnits"] = "percent_of_entries"
        key = _pkey(p)
        for column, source in [('ProjOwnPct', 'total'), ('ProjCptOwnPct', 'cpt'), ('ProjFlexOwnPct', 'flex')]:
            p[column] = float((result.get(source) or {}).get(key, 0) or 0)
        if eligible:
            p['NFLFieldEligible'] = key in eligible


def prepare_nfl_slate(csv_path, *, mode='classic', ownership_sims=1000, template_sim=False, context=None):
    if mode not in {'classic', 'showdown'}:
        raise ValueError('Mode must be classic or showdown')
    if not 1 <= ownership_sims <= 100000:
        raise ValueError('Ownership sample must be 1–100000')
    players = read_players_csv(str(csv_path))
    summary = apply_auto_nfl_context(players, **(context or {}))
    for p in players:
        if not available(p):
            p['FadeFlex'] = True
            p['FadeCpt'] = True
    # The exact worker used by Recalc Own% (Sim). It needs Qt's library but
    # creates no window or event loop when _simulate is called synchronously.
    from main_window import OwnershipSimWorker
    result = OwnershipSimWorker(players, mode=mode, num_sims=ownership_sims,
                                salary_cap=50000, sport='NFL', template_sim=template_sim)._simulate()
    if not result.get('total') or sum(result['total'].values()) <= 0:
        raise ValueError('Ownership simulation produced no valid entries; inspect pool/projections')
    apply_ownership(players, result)
    pool = build_nfl_role_pool(players) if mode == 'classic' else [p for p in players if available(p)]
    root = Path(__file__).resolve().parent
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
              ('data_io.py', 'nfl_auto_data.py', 'nfl_workload.py', 'projection_sources.py', 'nfl_simulation.py', 'nfl_specialists.py', 'nfl_kickers.py', 'main_window.py', 'nfl_pipeline.py')}
    return dict(players=players, role_pool=pool, summary=summary,
                preparation=dict(mode=mode, ownership_sims=ownership_sims, template_sim=template_sim,
                                 source_hashes=hashes, ownership_total=sum(result['total'].values())))
