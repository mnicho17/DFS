"""Report-only statistics of already drawn base-player outcomes; no RNG or model edits."""
from collections import Counter, defaultdict
import math
import statistics

SPECIALISTS = {'K', 'DST'}
MAX_PAIRS = 20000
MAX_PAIR_SCENARIOS = 20000000


def _count(value, scenarios):
    return type(value) is int and 0 <= value <= scenarios


def valid_zero(row, scenarios):
    return _count(row.get('zero_count'), scenarios)


def attach_interactions(rows, players, values):
    """Finish-time only, bounded by same-game pairs; no extra scenario storage."""
    names = Counter((p['game'], p['name']) for p in players.values())
    eligible = {key: p for key, p in players.items() if p['game'] and
        p.get('team') in p['game'].split('|')[:2] and names[p['game'], p['name']] == 1}
    pairs = []
    for key, p in sorted(eligible.items()):
        if p['position'] not in SPECIALISTS:
            continue
        for other, q in sorted(eligible.items()):
            if other == key or q['game'] != p['game']:
                continue
            if q['position'] in SPECIALISTS and other < key:
                continue
            pairs.append((key, other))
            if len(pairs) > MAX_PAIRS:
                return  # All interaction fields unavailable; never a selected partial sample.
    if not pairs or len(pairs)*len(values[pairs[0][0]]) > MAX_PAIR_SCENARIOS:
        return
    lookup = {(r.get('game'), r['name']): r for r in rows}
    moments = {}
    for key in {key for pair in pairs for key in pair}:
        v = values[key]; mean = statistics.mean(v)
        moments[key] = (mean, math.fsum((x-mean)**2 for x in v))
    for key, other in pairs:
        p, q = eligible[key], eligible[other]
        x, y = values[key], values[other]
        mx, vx = moments[key]; my, vy = moments[other]
        correlation = (max(-1., min(1., math.fsum((a-mx)*(b-my) for a,b in zip(x,y)) /
            math.sqrt(vx*vy))) if vx > 0 and vy > 0 else None)
        pair = dict(name=q['name'], position=q['position'],
            relationship='same team' if p['team']==q['team'] else 'opposing team',
            both_positive_count=sum(a>0 and b>0 for a,b in zip(x,y)),
            both_zero_count=sum(a==0 and b==0 for a,b in zip(x,y)), correlation=correlation)
        lookup[p['game'], p['name']].setdefault('interactions', []).append(pair)


def validate_capture_statistics(payload):
    """Optional extension: absent legacy fields remain unknown; present fields are strict."""
    n = payload['scenarios']; rows = payload['players']
    identities = Counter((r.get('game'), r['name']) for r in rows)
    lookup = {(r.get('game'), r['name']): r for r in rows}
    count = 0; seen = set()
    for row in rows:
        if 'zero_count' in row and not valid_zero(row, n):
            raise ValueError('Invalid exact zero count')
        if 'interactions' not in row:
            continue
        if not isinstance(row['interactions'], list) or row['position'] not in SPECIALISTS:
            raise ValueError('Invalid specialist interactions')
        for pair in row['interactions']:
            count += 1
            if count > MAX_PAIRS or not isinstance(pair, dict):
                raise ValueError('Invalid interaction budget')
            if not isinstance(pair.get('name'), str):
                raise ValueError('Invalid interaction player name')
            key = (row.get('game'), pair['name'])
            other = lookup.get(key)
            teams = str(row.get('game') or '').split('|')[:2]
            if (not other or key == (row['game'], row['name']) or identities[key] != 1 or
                    identities[row['game'], row['name']] != 1 or not row['game'] or
                    row.get('team') not in teams or other.get('team') not in teams or
                    pair.get('position') != other['position']):
                raise ValueError('Invalid interaction identity')
            pair_key = (row['game'], *sorted((row['name'], other['name'])))
            expected = 'same team' if row['team']==other['team'] else 'opposing team'
            if pair_key in seen or pair.get('relationship') != expected:
                raise ValueError('Conflicting interaction identity')
            seen.add(pair_key)
            if not all(_count(pair.get(k), n) for k in ('both_positive_count','both_zero_count')):
                raise ValueError('Invalid joint outcome counts')
            if (not valid_zero(row,n) or not valid_zero(other,n) or
                    pair['both_zero_count'] > min(row['zero_count'],other['zero_count']) or
                    pair['both_zero_count'] < max(0,row['zero_count']+other['zero_count']-n) or
                    pair['both_positive_count'] > n-max(row['zero_count'],other['zero_count']) or
                    pair['both_positive_count']+pair['both_zero_count'] > n):
                raise ValueError('Inconsistent joint outcome counts')
            c = pair.get('correlation')
            if c is not None and (type(c) not in (int,float) or not math.isfinite(c) or not -1<=c<=1):
                raise ValueError('Invalid outcome correlation')


def summarize_interactions(rows):
    """Both observed scores must qualify in the same exact capture, format and model."""
    lookup = {(r['kind'],r['model'],r['game'],r['name']):r for r in rows}
    groups = defaultdict(list)
    for row in rows:
        for pair in row.get('interactions', []):
            other = lookup.get((row['kind'],row['model'],row['game'],pair['name']))
            if not other or other.get('capture_id') != row.get('capture_id'):
                continue
            label = row['position']+' / '+pair['relationship']+' / '+pair['position']
            groups[row['kind'],row['model'],label].append(dict(game=row['game'],
                expected_positive=pair['both_positive_count']/row['scenarios'],
                expected_zero=pair['both_zero_count']/row['scenarios'],
                actual_positive=row['actual']>0 and other['actual']>0,
                actual_zero=row['actual']==0 and other['actual']==0, correlation=pair['correlation']))
    result=[]
    for (kind,model,label), pairs in sorted(groups.items()):
        games = defaultdict(list)
        for pair in pairs: games[pair['game']].append(pair)
        metric = lambda key: statistics.mean(statistics.mean(p[key] for p in v) for v in games.values())
        correlations = [p['correlation'] for p in pairs if p['correlation'] is not None]
        result.append(dict(kind=kind,model=model,label=label,games=len(games),pair_games=len(pairs),
            sim_both_positive_pct=100*metric('expected_positive'),actual_both_positive_pct=100*metric('actual_positive'),
            sim_both_zero_pct=100*metric('expected_zero'),actual_both_zero_pct=100*metric('actual_zero'),
            mean_sim_correlation=statistics.mean(correlations) if correlations else None,
            correlation_pairs=len(correlations)))
    return result
