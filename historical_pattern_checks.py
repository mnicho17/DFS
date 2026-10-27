"""Descriptive cross-slate checks; no strategy selection or model inputs."""
from collections import defaultdict
import json,math,statistics

MIN_SLATES=5
MIN_USERS_PER_SLATE=10
METRICS=('player_pool','captain_pool','repeated_pct','mean_shared_players','mean_salary','double_qb_pct','double_k_pct','double_dst_pct')


def pattern_checks(report,minimum_peers=5):
    if report.get('review_mode')!='prior_qualified':raise ValueError('Use a review selected from strictly earlier history.')
    if type(minimum_peers) is not int or minimum_peers<1:raise ValueError('Minimum peers must be a positive integer.')
    grouped=defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
    users=defaultdict(lambda:defaultdict(lambda:defaultdict(set)))
    for contest in report['contests']:
        scope=contest['format']+' / '+contest['preset'];slate=contest['slate']
        if len(slate)!=3 or slate[0]!=contest['format'] or slate[1]!=contest['date'] or not slate[2]:raise ValueError('Independent salary-slate identity is unresolved.')
        key=json.dumps([slate[0],slate[1],sorted(set(slate[2]))])
        for metric in METRICS:
            pairs=[]
            for p in contest['winners']:
                if p['peer_users']<minimum_peers:continue
                a=p['metrics'].get(metric);b=p['peer_medians'].get(metric)
                if type(a) not in (int,float) or type(b) not in (int,float) or not math.isfinite(a) or not math.isfinite(b):continue
                pairs.append((a,b));users[scope][metric][key].add(p['user_key'])
            if pairs:grouped[scope][metric][key].append((statistics.mean(a for a,b in pairs),statistics.mean(b for a,b in pairs)))
    output={}
    for scope,metrics in sorted(grouped.items()):
        output[scope]={}
        for metric,slates in metrics.items():
            rows=[];excluded=0
            for key,values in sorted(slates.items()):
                n=len(users[scope][metric][key])
                if n<MIN_USERS_PER_SLATE:excluded+=1;continue
                a=statistics.mean(v[0] for v in values);b=statistics.mean(v[1] for v in values)
                rows.append(dict(slate=json.loads(key),users=n,cohort=a,peer=b,delta=a-b))
            differences=[r['delta'] for r in rows];n=len(rows)
            mean=statistics.mean(differences) if n else None
            positive=sum(v>1e-9 for v in differences);negative=sum(v< -1e-9 for v in differences)
            loo=[statistics.mean(differences[:i]+differences[i+1:]) for i in range(n)] if n>1 else []
            direction='higher' if mean is not None and mean>1e-9 else 'lower' if mean is not None and mean< -1e-9 else 'mixed'
            same=positive if direction=='higher' else negative if direction=='lower' else 0
            stable=n>=MIN_SLATES and same/n>=.7 and bool(loo) and ((min(loo)>1e-9) if direction=='higher' else (max(loo)< -1e-9) if direction=='lower' else False)
            output[scope][metric]=dict(slates=n,excluded_small_slates=excluded,mean_delta=mean,positive_slates=positive,negative_slates=negative,zero_slates=n-positive-negative,leave_one_out_min=min(loo) if loo else None,leave_one_out_max=max(loo) if loo else None,direction=direction,consistent_descriptive_direction=stable,status='insufficient independent slates' if n<MIN_SLATES else 'consistent descriptive direction' if stable else 'mixed or sensitive to individual games',games=rows)
    return output


def render_checks(report):
    from winner_portfolio_review import LABELS
    lines=['CROSS-GAME CONSTRUCTION CHECKS',f'Require at least {MIN_SLATES} independent salary slates and {MIN_USERS_PER_SLATE} distinct matched cohort users per slate. Differences are cohort means minus matched-peer medians; contests within each slate and slates receive equal weight.',
           'Higher or lower is descriptive, not better or worse. These checks do not establish significance, skill, profit or a causal rule. Missing metadata is unknown. No defaults or models change.']
    for minimum in (5,10):
        lines += ['',f'Minimum {minimum} matched peers:']
        scopes=pattern_checks(report,minimum)
        if not scopes:lines.append('No matched evidence.')
        for scope,metrics in scopes.items():
            lines.append(scope)
            for metric,v in metrics.items():
                mean='unknown' if v['mean_delta'] is None else f"{v['mean_delta']:+.2f}"
                lo='unavailable' if v['leave_one_out_min'] is None else f"{v['leave_one_out_min']:+.2f} to {v['leave_one_out_max']:+.2f}"
                lines.append(f"{LABELS[metric]}: difference {mean}; {v['slates']} slates; positive/negative/zero {v['positive_slates']}/{v['negative_slates']}/{v['zero_slates']}; leave-one-game-out {lo}; {v['status']}; small supported slates withheld {v['excluded_small_slates']}.")
                for game in v['games']:lines.append(f"  {game['slate'][1]} {', '.join(game['slate'][2])}: cohort {game['cohort']:.2f}; peer {game['peer']:.2f}; difference {game['delta']:+.2f}; {game['users']} distinct matched users.")
    return '\n'.join(lines)
