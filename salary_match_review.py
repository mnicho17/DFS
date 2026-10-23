"""Read-only review guidance; never chooses or saves a qualified association."""
from collections import Counter
import json

LABELS = dict(missing_player='Missing players', id_conflict='Explicit ID conflict',
    role_conflict='Roster role conflict', name_conflict='Name conflict for explicit ID', ambiguous_identity='Ambiguous identity',
    analysis_identity_conflict='Analysis identity conflict', source='Source unavailable or changed',
    date='Contest date review', format='Roster format conflict', games='Game/team conflict',
    contest='Multiple contests', unreadable='No readable rosters', compatible='Compatible revision',
    date_correction='Date correction candidate', unknown='Inspect qualification details')


def candidate_details(result, salary, qualified, identity_index, *, source_problem='', date_correctable=False, id_index=None):
    """Explain the existing validator's result without promoting skipped checks."""
    r, s = result['manifest'], salary['manifest']
    issues = []
    if id_index is None:
        id_index = {}
        for player in s.get('players', []):id_index.setdefault(player['id'], []).append(player)
    if source_problem:
        kinds = ['source']
    elif qualified.get('identity_checked'):
        for role, name, player_id in r['observed']:
            if (role, name, player_id) in qualified['matches']:
                continue
            named = identity_index.get(name, [])
            exact = [p for p in named if not player_id or p['id'] == player_id]
            allowed = [p for p in exact if (p['role'] == role if s['format'] == 'showdown' else role in p['role'].split('/'))]
            elsewhere = id_index.get(player_id, []) if player_id else []
            kind = ('name_conflict' if not named and elsewhere else 'missing_player' if not named else
                    'id_conflict' if not exact else 'role_conflict' if not allowed else 'ambiguous_identity')
            evidence = exact or named or elsewhere
            issues.append(dict(kind=kind, role=role, name=name, player_id=player_id,
                               salary_roles=sorted({p['role'] for p in evidence}),
                               salary_ids=sorted({str(p['id']) for p in evidence})[:8],
                               salary_names=sorted({p['name'] for p in evidence})[:8], salary_rows=len(evidence)))
        kinds = [i['kind'] for i in issues]
        if not kinds:kinds = ['compatible' if qualified['compatible'] else 'analysis_identity_conflict']
    else:
        kinds = []
        if not r.get('format') or r.get('format') != s.get('format'):kinds.append('format')
        if len(r.get('dates', [])) > 1 or (r.get('dates') and r['dates'] != s.get('dates')) or len(s.get('dates', [])) != 1:kinds.append('date')
        if r.get('games') and not set(r['games']) <= set(s.get('games', [])):kinds.append('games')
        if len(r.get('contest_ids', [])) > 1 or len(r.get('contest_names', [])) > 1:kinds.append('contest')
        if not r.get('observed'):kinds.append('unreadable')
        if not kinds:kinds = ['unknown']
    counts = dict(sorted(Counter(kinds).items()))
    return dict(categories=counts, issues=sorted(issues, key=lambda i:(i['kind'],i['role'],i['name'],str(i['player_id'])))[:12],
                identity_checked=bool(qualified.get('identity_checked')), date_correctable=bool(date_correctable),
                note='Issue examples are bounded to 12 per contest; category counts cover all observed conflicts. Skipped identity checks remain unknown.')


def plan(row):
    candidates = row.get('candidates', [])
    saved = row.get('saved_pair')
    if saved:
        return dict(kind='saved', candidate_hashes=[saved['hash']], candidate=saved,
                    action=('Inspect the saved source conflict. Existing associations stay fixed; no replacement is selected.'
                            if row['status'] == 'INVALID_SAVED_PAIR' else 'Existing qualified association is preserved; no new selection is needed.'))
    review = row.get('review_association')
    recorded = [c for c in candidates if review and c['hash'] == review['salary_hash']]
    def rank(c):
        return (bool(c.get('compatible')),bool(c.get('date_correctable')),
                bool(c.get('identity_checked')),c.get('matched',0) if c.get('identity_checked') else -1)
    if recorded:
        best = recorded
    else:
        top = max((rank(c) for c in candidates),default=None)
        best = [c for c in candidates if rank(c) == top]
    best = sorted(best,key=lambda c:(c['hash'],c['name']))
    hashes = [c['hash'] for c in best]
    if len(best) != 1:
        return dict(kind='ambiguous' if best else 'missing', candidate_hashes=hashes, candidate=None,
            action='Several revisions are equally plausible; inspect them individually. No revision was selected.' if best else
                   'Import the appropriate salary revision, then refresh this review. No salary evidence is available.')
    c = best[0]
    categories = c.get('review_details', {}).get('categories', {})
    if review:
        kind, action = 'recorded', 'Recorded intended revision; resolve its conflicts before an explicit compatible Save. Background imports cannot promote this choice.'
    elif c.get('compatible'):
        kind, action = 'compatible', 'Inspect this revision and use the existing Save controls for this contest. Date confirmation is still required when missing.'
    elif c.get('date_correctable'):
        kind, action = 'date_correction', 'Review the recorded contest date against this revision. Changing the date requires the existing reason and explicit confirmation; the original file remains intact.'
    else:
        kind = 'conflict'
        action = ('Restore the original source snapshot before retrying.' if 'source' in categories else
                  'Find a complete salary revision with the exact observed player IDs.' if any(k in categories for k in ('missing_player','id_conflict','name_conflict')) else
                  'Review the recorded roles and the salary revision; do not relabel players to force qualification.' if 'role_conflict' in categories else
                  'Inspect the qualification details and import the correct revision.')
        action += ' An intended association may be recorded as Needs review; it supplies no qualified historical metadata.'
    return dict(kind=kind,candidate_hashes=hashes,candidate=c,action=action)


def groups(rows):
    grouped = {}
    for row in rows:
        if row.get('saved_pair') and row['status'] != 'INVALID_SAVED_PAIR':continue
        view = plan(row)
        key = json.dumps([view['kind'] == 'saved',view['candidate_hashes'],row.get('format'),sorted(row.get('dates',[]))],sort_keys=True)
        group = grouped.setdefault(key,dict(key=key,result_hashes=[],dates=sorted(row.get('dates',[])),
            format=row.get('format'),candidate_hashes=view['candidate_hashes'],candidate=view['candidate'],
            categories={},examples={},actions=set(),candidate_names=sorted({(c['name'],c['hash']) for c in row.get('candidates', []) if c['hash'] in view['candidate_hashes']})))
        group['result_hashes'].append(row['hash']);group['actions'].add(view['action'])
        details = (view['candidate'] or {}).get('review_details', {})
        for category,count in details.get('categories', {}).items():group['categories'][category]=group['categories'].get(category,0)+count
        for issue in details.get('issues', []):group['examples'][json.dumps(issue,sort_keys=True)]=issue
    result = []
    for key,g in sorted(grouped.items()):
        g['result_hashes'].sort();g['actions']=sorted(g['actions'])
        g['examples']=[g['examples'][k] for k in sorted(g['examples'])][:12]
        c = g['candidate']
        revision = c['name']+' ['+c['hash'][:12]+']' if c else 'Review multiple revisions' if g['candidate_hashes'] else 'Import salary evidence'
        g['label'] = f"{', '.join(g['dates']) or 'Unknown date'} | {g['format'] or 'Unknown format'} | {len(g['result_hashes'])} {'contest' if len(g['result_hashes']) == 1 else 'contests'} | {revision}"
        result.append(g)
    return sorted(result,key=lambda g:(g['dates'],g['format'] or '',g['key']))


def group_text(group):
    lines = [group['label'], 'Grouping is navigation only; every contest retains its own qualification and confirmation.']
    if not group['candidate'] and group['candidate_hashes']:
        lines.append('Equally ranked revisions: '+'; '.join(name+' ['+digest[:12]+']' for name,digest in group.get('candidate_names', [])[:6]))
    if group['categories']:
        lines.append('Conflict/check observations across contests (repeated identities may repeat): '+', '.join(
            LABELS.get(k,k)+': '+str(v) for k,v in sorted(group['categories'].items())))
    if group['examples']:
        lines.append('Examples: '+'; '.join(i['role']+' '+i['name']+(' (ID '+str(i['player_id'])+')' if i['player_id'] else '')+' — '+LABELS[i['kind']] for i in group['examples'][:4]))
    lines += ['Next: '+action for action in group['actions']]
    return '\n'.join(lines)


def issue_text(candidate):
    details = candidate.get('review_details', {})
    lines = ['Selected revision conflict examples (no aliases or roles were substituted):'] if details.get('issues') else []
    for issue in details.get('issues', []):
        lines.append(f"- {LABELS[issue['kind']]}: result {issue['role']} {issue['name']} | explicit ID {issue['player_id'] or 'not recorded'}; "
            + 'salary names '+(', '.join(issue.get('salary_names', [])) or 'none')
            + '; IDs '+(', '.join(issue['salary_ids']) or 'none')
            + '; roles '+(', '.join(issue['salary_roles']) or 'none')
            + f"; matching salary rows {issue.get('salary_rows', 0)}.")
    if lines:lines.append(details['note'])
    return '\n'.join(lines)
