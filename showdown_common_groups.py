"""Opt-in current-slate Showdown shortcuts compiled to existing player groups."""
from copy import deepcopy
from portfolio_rules import player_key

RULES = {
    'kickers': 'At most one kicker',
    'defenses': 'At most one defense',
    'quarterbacks': 'At most one quarterback',
    'qb_dst': 'No QB with opposing defense',
    'captain_dst': 'Block Captain + opposing DST',
}
PREFIX = 'Common Showdown: '


def catalog(players):
    pool={};ids={};games=set()
    for p in players:
        key=player_key(p)
        if not key or not p.get('FlexID'):
            raise ValueError('Load a salary slate with explicit player IDs first.')
        row=(str(p.get('Team') or '').strip().upper(),str(p.get('Position') or '').strip().upper(),str(p['FlexID']),str(p.get('CptID') or ''))
        ident=str(p['FlexID'])
        if ident in ids and ids[ident]!=(key,row):
            raise ValueError('One explicit player ID maps to conflicting slate players.')
        ids[ident]=(key,row)
        game=str(p.get('GameInfo') or p.get('GameKey') or '').strip().upper()
        if game:games.add(game)
        if key in pool and pool[key][0]!=row:
            raise ValueError('Conflicting player identities in the current slate.')
        pool[key]=(row,p)
    teams={row[0][0] for row in pool.values()}
    if len(games)>1:
        raise ValueError('Common Showdown groups require one game.')
    if len(teams)!=2 or '' in teams:
        raise ValueError('Common Showdown groups require exactly two known teams.')
    result={key:[] for key in RULES}
    def position(p):
        pos=str(p.get('Position') or '').strip().upper()
        return 'DST' if pos in ('DST','D/ST','DEF','D') else pos
    def group(rule,keys,suffix=''):
        if len(set(keys))>=2:
            result[rule].append(dict(type='never_together',player_keys=sorted(set(keys)),label=PREFIX+RULES[rule]+suffix))
    values=[p for _,p in pool.values()]
    for rule,pos in (('kickers','K'),('defenses','DST'),('quarterbacks','QB')):
        group(rule,[player_key(p) for p in values if position(p)==pos])
    for qb in sorted((p for p in values if position(p)=='QB'),key=player_key):
        team=str(qb['Team']).strip().upper()
        opponent=str(qb.get('Opponent') or '').strip().upper()
        if opponent and opponent not in teams-{team}:
            raise ValueError('QB opponent metadata conflicts with the slate teams.')
        for dst in sorted((p for p in values if position(p)=='DST' and str(p['Team']).strip().upper()!=team),key=player_key):
            group('qb_dst',[player_key(qb),player_key(dst)],': '+str(qb.get('Name') or player_key(qb))+' / '+str(dst.get('Name') or player_key(dst)))
    for captain in sorted(values,key=player_key):
        team=str(captain['Team']).strip().upper()
        opponent=str(captain.get('Opponent') or '').strip().upper()
        if opponent and opponent not in teams-{team}:
            raise ValueError('Captain opponent metadata conflicts with the slate teams.')
        for dst in sorted((p for p in values if position(p)=='DST' and str(p['Team']).strip().upper()!=team),key=player_key):
            group('captain_dst',['CPT:'+player_key(captain),player_key(dst)],': '+str(captain.get('Name') or player_key(captain))+' / '+str(dst.get('Name') or player_key(dst)))
    return result


def managed(group):
    label=str(group.get('label') or '')
    return any(label==PREFIX+title or label.startswith(PREFIX+title+': ') for title in RULES.values())


def selection(players,groups):
    options=catalog(players)
    signatures={(g.get('type'),tuple(sorted(g.get('player_keys') or []))) for g in groups}
    return {rule for rule,rows in options.items() if rows and all((g['type'],tuple(g['player_keys'])) in signatures for g in rows)}


def apply(players,groups,selected):
    if set(selected)-RULES.keys():raise ValueError('Unknown common Showdown rule.')
    options=catalog(players)
    result=deepcopy([g for g in groups if not managed(g)])
    seen={(g.get('type'),tuple(sorted(g.get('player_keys') or []))) for g in result}
    for rule in RULES:
        if rule not in selected:continue
        for group in options[rule]:
            signature=(group['type'],tuple(group['player_keys']))
            if signature not in seen:result.append(group);seen.add(signature)
    return result
