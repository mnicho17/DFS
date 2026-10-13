"""Source-bound, prior-game construction evidence for explicit Showdown builds."""
from collections import Counter
from contextlib import closing
from datetime import date
import json
import math

import analysis_imports as ai
from build_snapshots import fingerprint
from field_history_validation import _counts
from historical_field import CATEGORIES, distributions
from opponent_history import VERSION, _connect

MODEL = 'showdown-history-blend-v1'
HISTORY_WEIGHT = .35


def slate_cutoff(players):
    from results_snapshot_learning import game_start
    starts = [game_start(p.get('GameInfo')) for p in players]
    if not starts or not all(starts) or len(set(starts)) != 1:
        raise ValueError('Historical fields require one exact recorded Showdown kickoff.')
    return starts[0].date().isoformat()


def prepare_profile(db_path, cutoff, preset, cancelled=lambda: False, progress=lambda text: None):
    """Equal game weights, whole-field constructions, no finish-rank selection."""
    from learning_db import _infer_field_preset
    cutoff = date.fromisoformat(cutoff).isoformat()
    if preset not in ('Single Entry', '3-Max', '20-Max', '150-Max'):
        raise ValueError('Choose an explicit entry-limit preset.')
    verified, manifests, eligible, excluded = {}, {}, [], []
    with closing(_connect(db_path, True)) as conn:
        conn.execute('BEGIN')
        sources = {s['hash']: s for s in ai._sources(conn)}
        pairs = dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        rows = conn.execute('SELECT contest_key,result_hash,salary_hash,start_date,end_date,name,audit_json FROM opponent_contests WHERE format=? AND version=? AND end_date<? ORDER BY end_date,contest_key', ('showdown', VERSION, cutoff)).fetchall()
        if len(rows) > 500:
            raise ValueError('Historical field preparation supports at most 500 prior contests.')
        for i, (key, r, s, start, end, name, audit) in enumerate(rows):
            ai._check(cancelled)
            if _infer_field_preset(name) != preset:
                continue
            progress(f'Verifying prior contest {i+1}/{len(rows)}: {name}')
            try:
                if r not in sources or s not in sources or pairs.get(r) != s:
                    raise ValueError('Saved salary association is unresolved.')
                for digest in (r, s):
                    if digest not in verified:
                        ai._verify(sources[digest], cancelled); verified[digest] = sources[digest]
                q = ai.qualify_pair(sources[r], sources[s])
                if not q['compatible']:
                    raise ValueError(q['reason'])
                if s not in manifests:
                    manifests[s] = ai._salary_manifest(sources[s]['snapshot'], cancelled)
                m = manifests[s]
                if len(m['dates']) != 1 or len(m['games']) != 1 or start != end or end != m['dates'][0]:
                    raise ValueError('A single dated game is required.')
                if json.loads(audit).get('conflicting_entry_ids_excluded'):
                    raise ValueError('Conflicting entry identities require review.')
                probabilities, known, entries = _counts(conn, key, cancelled)
                if entries <= 0 or known / entries < .95 or any(not probabilities[c] for c in CATEGORIES):
                    raise ValueError('At least 95% readable construction coverage is required.')
                eligible.append(dict(contest_key=key, result_hash=r, salary_hash=s, date=end,
                    game=m['games'][0], known=known, entries=entries, probabilities=probabilities))
            except ai.ImportCancelled:
                raise
            except Exception as exc:
                excluded.append(dict(contest_key=key, reason=str(exc)))
        games = {}
        for row in sorted(eligible, key=lambda r: (-r['known'], r['contest_key'])):
            games.setdefault((row['date'], row['game']), row)
        training = sorted(games.values(), key=lambda r: (r['date'], r['game']))
        if len(training) < 3 or sum(r['known'] for r in training) < 1000:
            raise ValueError('Historical fields need at least three prior games and 1,000 known entries for this preset.')
        priors = {c: Counter() for c in CATEGORIES}
        for row in training:
            for c in CATEGORIES:
                priors[c].update({label: value / len(training) for label, value in row['probabilities'][c].items()})
        # A remap after the read snapshot cannot qualify the superseded evidence.
    with closing(_connect(db_path, True)) as conn:
        current = dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        current_sources = {s['hash']: s for s in ai._sources(conn)}
        for row in training:
            if current.get(row['result_hash']) != row['salary_hash']:
                raise ValueError('Salary association changed during preparation; retry.')
            for digest in (row['result_hash'], row['salary_hash']):
                if digest not in current_sources or current_sources[digest]['snapshot'] != verified[digest]['snapshot']:
                    raise ValueError('Saved source changed during preparation; retry.')
    for source in verified.values():
        ai._verify(source, cancelled)
    ai._check(cancelled)
    payload = dict(model=MODEL, version=VERSION, cutoff=cutoff, preset=preset,
        history_weight=HISTORY_WEIGHT, training=training, priors={c: dict(v) for c, v in priors.items()},
        excluded=excluded, coverage_basis='observed entries; complete field not established',
        validation_status='experimental; structural holdout is not return validation')
    return dict(payload=payload, profile_id=fingerprint(payload))


def validate_profile(profile, players=None, preset=None):
    if not isinstance(profile, dict):
        raise ValueError('Prepare a historical field profile first.')
    p = profile.get('payload', {})
    if profile.get('profile_id') != fingerprint(p) or p.get('model') != MODEL or p.get('version') != VERSION:
        raise ValueError('Historical field profile is modified or unsupported.')
    training = p.get('training', [])
    if p.get('history_weight') != HISTORY_WEIGHT or len(training) < 3 or sum(r['known'] for r in training) < 1000:
        raise ValueError('Historical field evidence is incomplete.')
    if len({(r['date'], r['game']) for r in training}) != len(training):
        raise ValueError('Historical field counts a game more than once.')
    cutoff = date.fromisoformat(p['cutoff']).isoformat()
    if any(r['date'] >= cutoff for r in p['training']):
        raise ValueError('Historical field includes current or later game results.')
    if players is not None and slate_cutoff(players) < cutoff:
        raise ValueError('Historical field cutoff is after this slate; prepare it again.')
    if preset is not None and p['preset'] != preset:
        raise ValueError('Historical field profile belongs to another entry-limit preset.')
    for c in CATEGORIES:
        values = p['priors'].get(c, {})
        if not values or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values.values()) or abs(sum(values.values()) - 1) > 1e-6:
            raise ValueError('Invalid historical construction probabilities.')
    return p


def revalidate_profile(profile, db_path, players, preset, cancelled=lambda: False, progress=lambda text: None):
    p = validate_profile(profile, players, preset)
    current = prepare_profile(db_path, p['cutoff'], p['preset'], cancelled, progress)
    if current != profile:
        raise ValueError('Historical evidence changed; prepare the field profile again.')


def sample_field(pool, count, profile, *, salary_cap=50000, seed=0, cancelled=lambda: False):
    from showdown_field import sample_field as baseline_sample
    from historical_ownership import sample_calibrated_history_field, _targets, _error, _construction_error
    p = validate_profile(profile)
    baseline = baseline_sample(pool, count, salary_cap=salary_cap, seed=seed, cancel_callback=cancelled)
    receipt = dict(status='baseline fallback', profile_id=profile['profile_id'], preset=p['preset'],
        cutoff=p['cutoff'], games=len(p['training']), entries=sum(r['known'] for r in p['training']),
        history_weight=HISTORY_WEIGHT, coverage_basis=p['coverage_basis'], validation_status=p['validation_status'])
    baseline.diagnostic['history_use'] = receipt
    if salary_cap != 50000:
        receipt['reason'] = 'Historical salary-spending profiles require the recorded $50,000 cap.'
        return baseline
    if cancelled() or len(baseline) != count or not count:
        receipt['reason'] = 'Baseline field incomplete or cancelled.'
        return baseline
    try:
        targets = _targets(pool)
    except ValueError as exc:
        receipt['reason'] = str(exc)
        return baseline
    base = distributions(baseline, salary_cap)
    priors = {c: {label: (1-HISTORY_WEIGHT)*base[c].get(label, 0) + HISTORY_WEIGHT*p['priors'][c].get(label, 0)
        for label in base[c].keys() | p['priors'][c].keys()} for c in CATEGORIES}
    learned = sample_calibrated_history_field(pool, count, priors, salary_cap=salary_cap, seed=seed, cancelled=cancelled)
    if cancelled():
        return learned
    receipt['baseline_ownership_mae_pp'] = _error(baseline, targets)
    receipt['ownership_tolerance_pp'] = .5
    if len(learned) == count:
        receipt['historical_ownership_mae_pp'] = _error(learned, targets)
    if len(learned) != count or _error(learned, targets) > receipt['baseline_ownership_mae_pp'] + .5 or _construction_error(learned, priors, salary_cap) > _construction_error(baseline, priors, salary_cap):
        receipt['reason'] = 'Historical field did not pass completeness, ownership or construction-fit guards.'
        return baseline
    learned.diagnostic.update(model=MODEL, history_use=dict(receipt, status='applied'))
    return learned
