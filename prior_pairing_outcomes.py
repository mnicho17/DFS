"""Within-user later finish contrasts; caller verifies sources and slot identities."""
from collections import defaultdict
import statistics
from construction_outcomes import contrast,ENDPOINTS,SUPPORT_MINIMUMS
from controlled_construction import BANDS,MAX_CELLS
from portfolio_pattern_audit import PatternAuditLimit
from receiver_specialist_review import features,METRICS,LABELS
from showdown_group_review import flags

FIXED_GROUPS=('kickers','defenses','quarterbacks','qb_dst')
MIN_USERS_PER_SLATE=10
MIN_INDEPENDENT_SLATES=5


class WithinUser:
    def __init__(self,cutoff):
        if type(cutoff) is not int or cutoff<=0:raise ValueError('Verified contest rank denominator required.')
        self.cutoff=cutoff;self.cells=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]])
        self.conditional=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]])
        self.one_qb=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]])

    def add(self,slots,rank):
        # Only already resolved Showdown slots reach this accumulator.
        if type(rank) is not int or not 0<rank<=2**53-1:return
        captain=next(p['id'] for _,role,p in slots if role=='CPT')
        salary=sum(p['salary'] for _,_,p in slots)
        band=next(i for i,ceiling in enumerate(BANDS) if salary<ceiling)
        outcome=(1,int(rank<=(self.cutoff+99)//100),int(rank<=(self.cutoff+19)//20),int(rank==1))
        grouping=flags(slots);fixed=tuple(bool(grouping[k]) for k in FIXED_GROUPS)
        qbs=sum(p['position']=='QB' for _,_,p in slots)
        for i,present in enumerate(features(slots)):
            if present is None:continue
            side=self.cells[(captain,band),i][present]
            for j,n in enumerate(outcome):side[j]+=n
            conditional=self.conditional[(captain,band,fixed),i][present]
            for j,n in enumerate(outcome):conditional[j]+=n
            if i==0 and qbs==1:
                side=self.one_qb[(captain,band,fixed),0][present]
                for j,n in enumerate(outcome):side[j]+=n
        if len(self.cells)+len(self.conditional)+len(self.one_qb)>MAX_CELLS:raise PatternAuditLimit('Within-user outcome comparison-cell bound reached.')

    def finish(self,complete):
        return dict(complete=bool(complete),support_checks={str(n):contrast(self.cells if complete else {},n,metrics=METRICS) for n in SUPPORT_MINIMUMS},other_groups_fixed={str(n):contrast(self.conditional if complete else {},n,metrics=METRICS) for n in SUPPORT_MINIMUMS},one_qb_receiver={str(n):contrast(self.one_qb if complete else {},n,metrics=METRICS[:1]) for n in SUPPORT_MINIMUMS})


def summarize(report,minimum=10,*,other_groups_fixed=False,one_qb_receiver=False,minimum_users_per_slate=0):
    if report.get('review_mode')!='prior_qualified':raise ValueError('Use a strictly earlier-selected cohort.')
    if type(minimum) is not int or minimum not in SUPPORT_MINIMUMS:raise ValueError('Use a fixed five- or ten-entry support check.')
    if type(minimum_users_per_slate) is not int or minimum_users_per_slate<0:raise ValueError('Minimum distinct users must be a nonnegative integer.')
    if other_groups_fixed and one_qb_receiver:raise ValueError('Choose one comparison.')
    evidence='one_qb_receiver' if one_qb_receiver else 'other_groups_fixed' if other_groups_fixed else 'support_checks'
    grouped=defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
    users=defaultdict(set)
    for c in report['contests']:
        if minimum_users_per_slate and (len(c['slate'])!=3 or c['slate'][0]!=c['format'] or c['slate'][1]!=c['date'] or not c['slate'][2]):raise ValueError('Independent salary-slate identity is unresolved.')
        scope=c['format']+' / '+c['preset'];slate=(c['date'],tuple(sorted(set(c['slate'][2]))))
        rows=defaultdict(list)
        for p in c['winners']:
            detail=p.get('pairing_outcomes',{})
            if not detail.get('complete'):continue
            if minimum_users_per_slate and (not isinstance(p.get('user_key'),str) or not p['user_key']):continue
            for metric,value in detail.get(evidence,{}).get(str(minimum),{}).items():
                if value['strata']:
                    rows[metric].append(value['difference_pp'])
                    if minimum_users_per_slate:users[scope,metric,slate].add(p['user_key'])
        for metric,values in rows.items():
            grouped[scope][metric][slate].append(dict(users=len(values),rates={e:statistics.mean(v[e] for v in values) for e in ENDPOINTS}))
    result={}
    for scope,metrics in grouped.items():
        result[scope]={}
        for metric,slates in metrics.items():
            games=[dict(date=k[0],games=list(k[1]),user_contests=sum(v['users'] for v in rows),rates={e:statistics.mean(v['rates'][e] for v in rows) for e in ENDPOINTS}) for k,rows in sorted(slates.items()) if not minimum_users_per_slate or len(users[scope,metric,k])>=minimum_users_per_slate]
            endpoints={}
            for e in ENDPOINTS:
                values=[g['rates'][e] for g in games]
                omit=[statistics.mean(values[:i]+values[i+1:]) for i in range(len(values))] if len(values)>=3 else []
                endpoints[e]=dict(difference_pp=statistics.mean(values) if values else None,higher=sum(v>0 for v in values),lower=sum(v<0 for v in values),equal=sum(v==0 for v in values),omit_one_range=[min(omit),max(omit)] if omit else None)
            result[scope][metric]=dict(slates=len(games),user_contests=sum(g['user_contests'] for g in games),endpoints=endpoints,games=games)
            if minimum_users_per_slate:
                for g in games:g['distinct_users']=len(users[scope,metric,(g['date'],tuple(g['games']))])
                result[scope][metric].update(excluded_small_slates=len(slates)-len(games),minimum_users_per_slate=minimum_users_per_slate,evidence_status='insufficient independent games' if len(games)<MIN_INDEPENDENT_SLATES else 'supported descriptive sample')
    return result


def render_evidence_summary(report):
    """Compact reading aid over unchanged, strictly supported outcome summaries."""
    lines=['PAIRING EVIDENCE AT A GLANCE',
        f'Stricter check: 10 entries per side, {MIN_USERS_PER_SLATE} distinct supported users per game, {MIN_INDEPENDENT_SLATES} independent games minimum.',
        'Descriptive findings only. Sample support does not establish significance, profit or a benefit from changing a rule. Settings are unchanged.']
    scopes=sorted({c['format']+' / '+c['preset'] for c in report['contests']})
    if not scopes:lines.append('Unavailable: no evaluated contests.')
    modes=(('Captain and salary matched',{},'support_checks',METRICS),
           ('Four grouping flags also fixed',{'other_groups_fixed':True},'other_groups_fixed',METRICS),
           ('Exactly one QB: own-team versus opposing',{'one_qb_receiver':True},'one_qb_receiver',METRICS[:1]))
    for label,mode,key,metrics in modes:
        data=summarize(report,10,minimum_users_per_slate=MIN_USERS_PER_SLATE,**mode)
        lines.append(label)
        for scope in scopes:
            available=any(key in p.get('pairing_outcomes',{}) for c in report['contests'] if c['format']+' / '+c['preset']==scope for p in c['winners'])
            for metric in metrics:
                prefix=scope+' / '+LABELS[metric]+': '
                if not available:lines.append(prefix+'unavailable in saved evidence.');continue
                if key=='other_groups_fixed' and metric=='kd_three_plus_vs_zero':
                    lines.append(prefix+'structurally unavailable with all four flags fixed.');continue
                v=data.get(scope,{}).get(metric)
                if v is None:
                    lines.append(prefix+'insufficient independent games (0/'+str(MIN_INDEPENDENT_SLATES)+'); no complete supported comparisons.');continue
                counts=f"{v['slates']}/{MIN_INDEPENDENT_SLATES} games minimum; {v['user_contests']} user-contests; {v['excluded_small_slates']} small games withheld"
                if v['slates']<MIN_INDEPENDENT_SLATES:
                    lines.append(prefix+'insufficient independent games - '+counts+'.');continue
                d=v['endpoints']['top1'];omit=d['omit_one_range']
                sensitivity='unavailable' if omit is None else f'{omit[0]:+.3f} to {omit[1]:+.3f} pp'
                lines.append(prefix+f"supported descriptive sample - {counts}; top-cutoff {d['difference_pp']:+.3f} pp; higher/lower/equal {d['higher']}/{d['lower']}/{d['equal']}; omit-one {sensitivity}.")
    lines.append('Detailed comparisons, five-entry sensitivity and all finishing endpoints follow below. Different matching methods can use different users and games.')
    return '\n'.join(lines)


def render(report):
    lines=['WITHIN-USER LATER PAIRING FINISH RATES',
        'Earlier-selected users only. Compare entries of the same user within the same contest, exact Captain ID and salary band. Other construction choices are not held fixed.',
        'All entries of a user must resolve and have valid ranks. Missing ranks or any unresolved roster withholds the entire user portfolio. Top cutoffs use the contest denominator, never the user entry count.',
        'Feature-present minus baseline finish rate, overlap weighted within user; users equally weighted per contest, contests within salary slate, then slates equally. Repeated users/lineups are not independent.',
        'Descriptive associations only, not causal effects, ROI, significance or a reason to change defaults. No supported within-user contrast means unavailable.']
    for fixed,one_qb in ((False,False),(True,False),(False,True)):
        lines.append('Exactly one QB: own-team versus opposing QB, with all four grouping flags fixed' if one_qb else 'Other four grouping flags also held fixed' if fixed else 'Primary Captain/salary comparison')
        if one_qb:lines.append('WR/TE Captains only, exactly one QB in each lineup on both sides. Zero-QB and multiple-QB lineups are excluded. This narrower sample does not isolate individual QB skill or prove a causal stacking benefit.')
        if fixed:lines.append('Match all four flags exactly: two kickers, two defenses, two QBs, QB with opposing DST. Here two K/DST versus zero means one kicker plus one defense; double-kicker or double-defense flags cannot match a zero baseline. Three-plus versus zero is structurally unavailable. Flags are not relaxed.')
        for minimum in SUPPORT_MINIMUMS:
            lines.append(f'At least {minimum} entries on each side within each user/Captain/salary stratum:')
            for users in (0,MIN_USERS_PER_SLATE):
                if users:lines.append(f'Stricter game support: at least {users} distinct supported users per salary slate and {MIN_INDEPENDENT_SLATES} independent slates. Repeat users across contests count once toward game support. This is not a significance test or strategy recommendation.')
                data=summarize(report,minimum,other_groups_fixed=fixed,one_qb_receiver=one_qb,minimum_users_per_slate=users)
                if not data:lines.append('Unavailable: no complete within-user comparisons with this evidence.')
                for scope,metrics in data.items():
                    for metric,v in metrics.items():
                        lines.append(f"{scope} / {LABELS[metric]}: {v['slates']} slates, {v['user_contests']} supported user-contests")
                        if users:lines.append(f"  {v['evidence_status']}; independent games {v['slates']}/{MIN_INDEPENDENT_SLATES} minimum; small games withheld {v['excluded_small_slates']}.")
                        for e,d in v['endpoints'].items():
                            value='unavailable' if d['difference_pp'] is None else f"{d['difference_pp']:+.3f} pp"
                            lines.append(f"  {e}: {value}; higher/lower/equal {d['higher']}/{d['lower']}/{d['equal']}; leave-one-game-out {d['omit_one_range']}")
    return '\n'.join(lines)
