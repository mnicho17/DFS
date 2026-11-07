"""Read-only original screening membership, never inferred rejection causes."""
from collections import Counter
import hashlib

from hindsight_contract import check, signature, validate_roster, decimal, Cancelled
import review_build_evidence as be

STAGES = dict(generated='generated_order', retained='retained_order',
    salary_eligible='salary_eligible_order', screened='coarse_order',
    shortlisted='shortlisted', captain_reserved='captain_reserved',
    feasible_reserved='feasible_reserved', selected='selected')


def capture(matches, chosen, pool, kind, reader, cancelled=lambda: False):
    check(cancelled)
    missing = dict(status='unavailable_evidence', reason='invalid_original_screening_binding')
    if kind != 'showdown': return dict(status='not_recorded')
    if not matches: return missing
    keys = {be.digest([a[0]['raw'], a[1], a[4]]) for a in matches}
    if len(keys) != 1: return missing
    snap, meta, _, _, rows = matches[0]
    receipt = meta.get('screening_evidence') or {}
    captured = reader.screening.get(next(iter(keys)), [])
    if not receipt and not any(raw is not None for raw in captured):
        return dict(status='not_recorded')
    if receipt.get('status') != 'bound':
        return dict(status='not_recorded') if receipt.get('status')=='not_recorded' and not any(raw is not None for raw in captured) else missing
    try:
        if not captured or any(raw is None for raw in captured): raise ValueError('missing_member')
        if len({hashlib.sha256(raw).hexdigest() for raw in captured}) != 1:
            raise ValueError('conflicting_member_occurrences')
        raw = captured[0]
        if (receipt.get('member') != 'screening-audit.json.gz' or
                receipt.get('sha256') != hashlib.sha256(raw).hexdigest() or receipt.get('bytes') != len(raw)):
            raise ValueError('member_receipt_conflict')
        from screening_audit import load_audit_bytes
        envelope = load_audit_bytes(raw, check=lambda: check(cancelled), consume=reader.consume)
        payload = envelope['payload']; inputs = chosen['raw']['inputs']
        if (meta.get('build_status') != 'completed' or be.digest(snap['raw']) != be.digest(chosen['raw']) or
                receipt.get('audit_id') != envelope['audit_id'] or
                payload['input_id'] != chosen['raw']['input_id'] or payload['players'] != inputs['players'] or
                payload['context']['rules'] != inputs['rules']):
            raise ValueError('frozen_input_conflict')
        fields = dict(salary_cap='salary_cap', salary_strategy='salary_strategy', build_style='build_style',
                      own_mode='ownership_mode', own_weight='ownership_weight', requested_count='requested_lineups')
        if any(key not in payload['context'] or target not in inputs['recipe'] or
               payload['context'][key] != inputs['recipe'][target] for key, target in fields.items()):
            raise ValueError('frozen_context_conflict')
        times = [be.timestamp(v) for v in (chosen['raw'].get('created_at'), payload.get('frozen_at'),
                                           payload.get('saved_at'), meta.get('created_at'))]
        if any(t is None for t in times) or times != sorted(times):
            raise ValueError('audit_chronology_conflict')
        from nfl_simulation import player_key
        aliases = {player_key(p): str(p['FlexID']) for p in payload['players']}
        if len(aliases) != len(payload['players']): raise ValueError('ambiguous_audit_players')
        by_id = {p['key']: p for p in pool}
        if set(aliases.values()) != set(by_id) or len(set(aliases.values())) != len(payload['players']):
            raise ValueError('audit_salary_pool_conflict')
        for player in payload['players']:
            check(cancelled)
            historical = by_id[str(player['FlexID'])]
            for role, prefix in (('FLEX', 'Flex'), ('CPT', 'Cpt')):
                original = historical['roles'][role]
                if (str(player[prefix+'ID']) != original['id'] or
                        decimal(str(player[prefix+'Salary'])) != decimal(str(original['salary']))):
                    raise ValueError('audit_salary_role_identity_conflict')
        signatures = []
        for row in payload['candidates']:
            check(cancelled)
            recorded = row['signature']
            roster = [dict(key=aliases[recorded[0][4:]], role='CPT')]
            roster += [dict(key=aliases[key], role='FLEX') for key in recorded[1:]]
            signatures.append(validate_roster(roster, pool, kind, require_scores=False)['signature'])
        from portfolio_risk_evidence import _archive_rosters
        def stopped():
            check(cancelled)
            return False
        output = _archive_rosters(rows, chosen['players'], kind, stopped)
        selected = Counter(tuple(signatures[i]) for i in payload['selected'])
        if selected != Counter(tuple(signature(row, kind)) for row in output):
            raise ValueError('selected_occurrence_conflict')
        status = payload['screening_status']
        if status not in ('not run', 'partial', 'complete'): raise ValueError('unknown_screening_status')
        if not Counter(payload['coarse_order']) <= Counter(payload['screening_input_order']):
            raise ValueError('coarse_rows_outside_screening_input')
        if status == 'not run' and any(payload[field] for field in ('coarse_order','shortlisted','captain_reserved','feasible_reserved')):
            raise ValueError('inconsistent_not_run_scoring')
        if status == 'complete':
            report = payload['screening_report']
            if (report['scenarios'] != payload['context']['screening']['scenarios'] or
                    Counter(payload['coarse_order']) != Counter(payload['screening_input_order'])):
                raise ValueError('inconsistent_complete_scoring')
        return dict(status='ready', audit_id=envelope['audit_id'], screening_status=status,
                    candidate_signatures=signatures,
                    stage_orders={stage: payload[field] for stage, field in STAGES.items()},
                    scope='recorded_membership_only; absence_is_not_rejection')
    except (Cancelled, be.Cancelled):
        raise
    except OverflowError:
        raise
    except Exception:
        return missing
