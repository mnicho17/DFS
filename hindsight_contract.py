"""Detached RL-07A v1 values; no storage, live players, forecasts or Qt objects."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re

import review_build_evidence as be

VERSION = 1
SCALE = 20_000  # Four base-score decimal places, plus exact Captain 3/2.
MAX_BASE_POINTS = 10_000
MAX_ATHLETES = 2_000
DEFAULT_SECONDS = 30
DEFAULT_TIES = 20
SCOPE = ('Any certified optimum is limited to the complete supplied salary revision under the displayed roster rules. '
         'Completeness of the original contest-wide player pool is not independently established.')
RULES = dict(version='NFL-supplied-pool-v1', salary_cap=50_000, minimum_teams=2,
             basis='explicit supported ruleset; historical platform rule revision not verified',
             classic_slots=['QB','RB','RB','WR','WR','WR','TE','FLEX','DST'],
             showdown_slots=['CPT','FLEX','FLEX','FLEX','FLEX','FLEX'])


class Cancelled(Exception):
    pass


def check(cancelled):
    if cancelled():
        raise Cancelled()


@dataclass(frozen=True)
class Frozen:
    encoded: str

    @classmethod
    def freeze(cls, value):
        return cls(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False))

    @property
    def data(self):
        return json.loads(self.encoded)


def decimal(value):
    """Lossless original text only. No bool, float, nonfinite or exotic coercion."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('unsupported_numeric_input')
    text = str(value).strip()
    if not text or len(text)>80 or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?',text):
        raise ValueError('missing_or_invalid_number')
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise ValueError('invalid_number') from None
    if not result.is_finite() or abs(result.adjusted())>1000:
        raise ValueError('nonfinite_or_unsupported_magnitude')
    return result


def score(value, captain=False):
    result = decimal(value)
    # Equivalent trailing zero lexemes remain equivalent, without rounding.
    if abs(result)>MAX_BASE_POINTS*(Decimal('1.5') if captain else 1):
        raise ValueError('unsupported_score_precision_or_magnitude')
    if result != result.quantize(Decimal('0.00001' if captain else '0.0001')):
        raise ValueError('unsupported_score_precision_or_magnitude')
    if result*SCALE != (result*SCALE).to_integral_value():
        raise ValueError('unsupported_integer_score_scale')
    return result


def integer(value, maximum=1_000_000):
    result = decimal(value)
    if result != result.to_integral_value() or not 0<=result<=maximum:
        raise ValueError('invalid_exact_integer')
    return int(result)


def points(units):
    return None if units is None else str(Decimal(units)/SCALE)


def signature(roster, kind):
    return tuple(sorted(('@cpt:' if kind=='showdown' and s['role']=='CPT' else '')+s['key'] for s in roster))


def restrictions():
    return dict(salary_cap=50_000, require=[], exclude=[], groups=[], inventory=[], blockers=[])


def validate_roster(roster, pool, kind, rules=None, require_scores=True):
    """Independent witness checker, using captured evidence, never MILP expressions."""
    rules = rules or restrictions()
    if not isinstance(roster,list) or be.roster_kind([be.normalize_role(s.get('role')) for s in roster])!=kind:
        raise ValueError('invalid_roster_shape')
    by_key = {p['key']:p for p in pool}
    if len(by_key)!=len(pool) or len({s.get('key') for s in roster})!=len(roster):
        raise ValueError('duplicate_athlete')
    checked=[]; total_salary=0; total=0; known=True; teams=set(); games=set()
    for slot in roster:
        role=be.normalize_role(slot.get('role')); p=by_key.get(slot.get('key'))
        if not p or role not in p['roles']:
            raise ValueError('unknown_identity_or_recorded_eligibility')
        if kind=='classic' and not (p['position'] in ('RB','WR','TE') if role=='FLEX' else role==p['position']):
            raise ValueError('position_conflict')
        r=p['roles'][role]
        for field in ('id','salary','score_units'):
            if field in slot and (isinstance(slot[field],bool) or slot[field]!=r[field]):
                raise ValueError('witness_'+field+'_conflict')
        if not isinstance(r['salary'],int) or isinstance(r['salary'],bool) or r['salary']<0:
            raise ValueError('invalid_salary')
        value=r['score_units']
        if value is None:
            known=False
        elif not isinstance(value,int) or isinstance(value,bool):
            raise ValueError('invalid_score_units')
        else:
            total+=value
        if not p['team'] or p['team'] not in p['game'].split('@') or len(p['game'].split('@'))!=2:
            raise ValueError('game_membership_conflict')
        total_salary+=r['salary']; teams.add(p['team']); games.add(p['game'])
        checked.append(dict(key=p['key'],role=role,**r))
    if total_salary>rules['salary_cap'] or len(teams)<2:
        raise ValueError('salary_or_team_rule')
    if kind=='showdown' and (len(games)!=1 or teams!=set(next(iter(games)).split('@'))):
        raise ValueError('showdown_game_rule')
    def included(key,role):
        return any(s['key']==key and (role=='ANY' or s['role']==role) for s in checked)
    if any(not included(*item) for item in rules['require']) or any(included(*item) for item in rules['exclude']):
        raise ValueError('local_lock_or_exclusion')
    keys={s['key'] for s in checked}
    for group in rules['groups']:
        overlap=len(keys.intersection(group['keys']))
        if (group['type']=='at_least_one' and overlap<1) or (group['type']=='never_together' and overlap>1):
            raise ValueError('local_group')
    if require_scores and not known:
        raise ValueError('unknown_actual_score')
    checked.sort(key=lambda s:(RULES[kind+'_slots'].index(s['role']),s['key']))
    return dict(roster=checked,signature=list(signature(checked,kind)),salary=total_salary,
                score_units=total if known else None,points=points(total) if known else None)
