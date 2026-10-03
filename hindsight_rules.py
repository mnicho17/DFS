"""Classify raw frozen restrictions before any defaulting/sanitizing normalizer."""
import re
from decimal import Decimal

from build_recipes import RECIPE_KEYS
from compute_settings import DEEP_CONTROLS
from portfolio_rules import player_key
from hindsight_contract import decimal, integer, restrictions

FLAGS = {'LockFlex','LockCpt','FadeFlex','FadeCpt','NFLQBEligible'}
PCTS = {'MinPct','MaxPct','MinCptPct','MaxCptPct','MaxFlexPct'}
PORTFOLIO = {'min_unique','max_team_pct','max_game_pct','team_max_pct','game_max_pct',
             'requested_lineups','retained','retained_rows','retained_lineups','exposure_recovery'}
STRATEGY = set(RECIPE_KEYS)-PORTFOLIO-{'salary_cap'}
RULE_KEYS = PORTFOLIO | {'salary_cap','balance_ownership','groups','player_constraints'}


def classify(snapshot, pool, kind):
    out=restrictions(); items=out['inventory']; blockers=out['blockers']; aliases={}
    players=snapshot['inputs']['players']; keys={p['key'] for p in pool}
    def note(path,value,disposition,reason):
        items.append(dict(path=path,value=value,disposition=disposition,reason=reason))
    def blocked(path,value,reason):
        blockers.append(reason);note(path,value,'unsupported_local',reason)
    for p in players:
        key=str(p.get('FlexID'))
        if key not in keys:
            blocked('players',key,'snapshot_pool_identity_conflict');continue
        alias=player_key(p)
        if not alias or alias in aliases and aliases[alias]!=key:
            blocked('players',alias,'ambiguous_snapshot_rule_key')
        aliases[alias]=key
    def resolve(value):
        if not isinstance(value,str) or value not in aliases:
            raise ValueError('unknown_snapshot_rule_key')
        return aliases[value]
    seen={}
    def player_rules(raw,key,path,strict_keys=False):
        for field,value in sorted(raw.items()):
            where=path+'.'+field
            if field in FLAGS:
                if not isinstance(value,bool):
                    blocked(where,value,'malformed_flag');continue
                if (key,field) in seen and seen[key,field]!=value:
                    blocked(where,value,'conflicting_recorded_flag');continue
                seen[key,field]=value
                if field=='NFLQBEligible':
                    if not value:
                        out['exclude'].append([key,'ANY'])
                    note(where,value,'applied_local','recorded snapshot eligibility only; never recalculated')
                elif 'Cpt' in field and kind!='showdown':
                    if value:blocked(where,value,'captain_rule_in_classic')
                    else:note(where,value,'applied_local','inactive role flag')
                else:
                    role='CPT' if 'Cpt' in field else 'FLEX' if kind=='showdown' else 'ANY'
                    if value:out['require' if field.startswith('Lock') else 'exclude'].append([key,role])
                    note(where,value,'applied_local','exact role-specific recorded flag')
            elif field in PCTS:
                if value in (None,''):continue
                try:
                    pct=decimal(str(value) if isinstance(value,float) else value)
                    if not 0<=pct<=100:raise ValueError()
                except ValueError:
                    blocked(where,value,'malformed_exposure_percentage');continue
                role='CPT' if 'Cpt' in field else 'FLEX' if field=='MaxFlexPct' and kind=='showdown' else 'ANY'
                if role=='CPT' and kind!='showdown':
                    blocked(where,value,'captain_rule_in_classic');continue
                if field.startswith('Max') and pct==0:
                    out['exclude'].append([key,role]); disposition='applied_local';reason='explicit zero excludes this role scope'
                elif field.startswith('Min') and pct==100:
                    out['require'].append([key,role]); disposition='applied_local';reason='explicit 100% minimum requires this role scope'
                else:
                    disposition='portfolio_only';reason='not evaluated by a single-lineup model; no requested=1 conversion'
                note(where,value,disposition,reason)
            elif field not in ('Name','FlexNamePlusID') and (strict_keys or re.search(r'lock|fade|eligib|exclud|restrict|constraint|^min|^max|^ban',field,re.I)):
                blocked(where,value,'unknown_player_restriction')
    for p in players:
        player_rules(p,str(p.get('FlexID')),'player:'+str(p.get('FlexID')))
    raw=snapshot['inputs']['rules']; recipe=snapshot['inputs']['recipe']; caps=[]
    for origin,values in (('recipe',recipe),('rules',raw)):
        for field,value in sorted(values.items()):
            path=origin+'.'+field
            if field=='salary_cap':
                try:
                    cap=integer(str(value) if isinstance(value,float) else value,50_000)
                    caps.append(cap);note(path,value,'applied_local','recorded cap cannot exceed the supported 50,000 cap')
                except ValueError:blocked(path,value,'unsupported_salary_cap')
            elif origin=='rules' and field=='groups':
                if not isinstance(value,list):blocked(path,value,'malformed_groups');continue
                for n,group in enumerate(value):
                    where=f'{path}[{n}]'
                    try:
                        if not isinstance(group,dict) or set(group)-{'type','player_keys','label'} or group.get('type') not in ('at_least_one','never_together'):
                            raise ValueError('unsupported_group')
                        members=group.get('player_keys')
                        if not isinstance(members,list) or not members or len(set(members))!=len(members):
                            raise ValueError('malformed_group_members')
                        resolved=sorted(resolve(m) for m in members)
                        out['groups'].append(dict(type=group['type'],keys=resolved))
                        note(where,group,'applied_local','exact snapshot-scoped group membership')
                    except (ValueError,TypeError) as exc:blocked(where,group,str(exc))
            elif origin=='rules' and field=='player_constraints':
                if not isinstance(value,dict):blocked(path,value,'malformed_player_constraints');continue
                for alias,p in sorted(value.items()):
                    try:
                        key=resolve(alias)
                        if not isinstance(p,dict):raise ValueError('malformed_player_rule')
                        player_rules(p,key,path+'.'+alias,True)
                    except ValueError as exc:blocked(path+'.'+alias,p,str(exc))
            elif field in PORTFOLIO:
                note(path,value,'portfolio_only','not evaluated here; no claim that this lineup extends to a feasible portfolio')
            elif field in STRATEGY or field=='balance_ownership':
                if field=='deep_compute' and (not isinstance(value,dict) or set(value)-set(DEEP_CONTROLS)-{'all_styles','selection_mode'}):
                    blocked(path,value,'unsupported_nested_compute_policy');continue
                note(path,value,'strategy_policy','recorded setting only; no inferred hard constraint or automatic recovery')
            else:
                blocked(path,value,'unknown_frozen_local_rule')
    if len(set(caps))>1:blockers.append('conflicting_recorded_salary_caps')
    # The contest profile normally contains descriptive entry/payout metadata.
    # A recorded hard-rule/period variant cannot hide there and be ignored.
    for key,value in snapshot['inputs']['contest'].items():
        if re.search(r'cap|salary|eligib|roster|restrict|period|constraint|^min|^max|lock|fade',key,re.I):
            blocked('contest.'+key,value,'unsupported_contest_specific_rule')
    if caps:out['salary_cap']=min(caps)
    out['require']=sorted({tuple(r) for r in out['require']})
    out['exclude']=sorted({tuple(r) for r in out['exclude']})
    out['groups']=sorted(out['groups'],key=lambda g:(g['type'],g['keys']))
    out['blockers']=sorted(set(blockers))
    out['full_build_constraints']='not_established; portfolio and effective pipeline policies are not evaluated'
    return out
