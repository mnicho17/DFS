"""Within-user later finish contrasts; caller verifies sources and slot identities."""
from collections import defaultdict
import statistics
from construction_outcomes import contrast,ENDPOINTS,SUPPORT_MINIMUMS
from controlled_construction import BANDS,MAX_CELLS
from portfolio_pattern_audit import PatternAuditLimit
from receiver_specialist_review import features,METRICS,LABELS
from showdown_group_review import flags

FIXED_GROUPS=('kickers','defenses','quarterbacks','qb_dst')


class WithinUser:
    def __init__(self,cutoff):
        if type(cutoff) is not int or cutoff<=0:raise ValueError('Verified contest rank denominator required.')
        self.cutoff=cutoff;self.cells=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]])
        self.conditional=defaultdict(lambda:[[0,0,0,0],[0,0,0,0]])

    def add(self,slots,rank):
        # Only already resolved Showdown slots reach this accumulator.
        if type(rank) is not int or not 0<rank<=2**53-1:return
        captain=next(p['id'] for _,role,p in slots if role=='CPT')
        salary=sum(p['salary'] for _,_,p in slots)
        band=next(i for i,ceiling in enumerate(BANDS) if salary<ceiling)
        outcome=(1,int(rank<=(self.cutoff+99)//100),int(rank<=(self.cutoff+19)//20),int(rank==1))
        grouping=flags(slots);fixed=tuple(bool(grouping[k]) for k in FIXED_GROUPS)
        for i,present in enumerate(features(slots)):
            if present is None:continue
            side=self.cells[(captain,band),i][present]
            for j,n in enumerate(outcome):side[j]+=n
            conditional=self.conditional[(captain,band,fixed),i][present]
            for j,n in enumerate(outcome):conditional[j]+=n
        if len(self.cells)+len(self.conditional)>MAX_CELLS:raise PatternAuditLimit('Within-user outcome comparison-cell bound reached.')

    def finish(self,complete):
        return dict(complete=bool(complete),support_checks={str(n):contrast(self.cells if complete else {},n,metrics=METRICS) for n in SUPPORT_MINIMUMS},other_groups_fixed={str(n):contrast(self.conditional if complete else {},n,metrics=METRICS) for n in SUPPORT_MINIMUMS})


def summarize(report,minimum=10,*,other_groups_fixed=False):
    if report.get('review_mode')!='prior_qualified':raise ValueError('Use a strictly earlier-selected cohort.')
    if type(minimum) is not int or minimum not in SUPPORT_MINIMUMS:raise ValueError('Use a fixed five- or ten-entry support check.')
    grouped=defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
    for c in report['contests']:
        scope=c['format']+' / '+c['preset'];slate=(c['date'],tuple(sorted(set(c['slate'][2]))))
        rows=defaultdict(list)
        for p in c['winners']:
            detail=p.get('pairing_outcomes',{})
            if not detail.get('complete'):continue
            for metric,value in detail.get('other_groups_fixed' if other_groups_fixed else 'support_checks',{}).get(str(minimum),{}).items():
                if value['strata']:rows[metric].append(value['difference_pp'])
        for metric,values in rows.items():
            grouped[scope][metric][slate].append(dict(users=len(values),rates={e:statistics.mean(v[e] for v in values) for e in ENDPOINTS}))
    result={}
    for scope,metrics in grouped.items():
        result[scope]={}
        for metric,slates in metrics.items():
            games=[dict(date=k[0],games=list(k[1]),user_contests=sum(v['users'] for v in rows),rates={e:statistics.mean(v['rates'][e] for v in rows) for e in ENDPOINTS}) for k,rows in sorted(slates.items())]
            endpoints={}
            for e in ENDPOINTS:
                values=[g['rates'][e] for g in games]
                omit=[statistics.mean(values[:i]+values[i+1:]) for i in range(len(values))] if len(values)>=3 else []
                endpoints[e]=dict(difference_pp=statistics.mean(values),higher=sum(v>0 for v in values),lower=sum(v<0 for v in values),equal=sum(v==0 for v in values),omit_one_range=[min(omit),max(omit)] if omit else None)
            result[scope][metric]=dict(slates=len(games),user_contests=sum(g['user_contests'] for g in games),endpoints=endpoints,games=games)
    return result


def render(report):
    lines=['WITHIN-USER LATER PAIRING FINISH RATES',
        'Earlier-selected users only. Compare entries of the same user within the same contest, exact Captain ID and salary band. Other construction choices are not held fixed.',
        'All entries of a user must resolve and have valid ranks. Missing ranks or any unresolved roster withholds the entire user portfolio. Top cutoffs use the contest denominator, never the user entry count.',
        'Feature-present minus baseline finish rate, overlap weighted within user; users equally weighted per contest, contests within salary slate, then slates equally. Repeated users/lineups are not independent.',
        'Descriptive associations only, not causal effects, ROI, significance or a reason to change defaults. No supported within-user contrast means unavailable.']
    for fixed in (False,True):
        lines.append('Other four grouping flags also held fixed' if fixed else 'Primary Captain/salary comparison')
        if fixed:lines.append('Match all four flags exactly: two kickers, two defenses, two QBs, QB with opposing DST. Here two K/DST versus zero means one kicker plus one defense; double-kicker or double-defense flags cannot match a zero baseline. Three-plus versus zero is structurally unavailable. Flags are not relaxed.')
        for minimum in SUPPORT_MINIMUMS:
            lines.append(f'At least {minimum} entries on each side within each user/Captain/salary stratum:')
            data=summarize(report,minimum,other_groups_fixed=fixed)
            if not data:lines.append('Unavailable: no complete within-user comparisons with this evidence.')
            for scope,metrics in data.items():
                for metric,v in metrics.items():
                    lines.append(f"{scope} / {LABELS[metric]}: {v['slates']} slates, {v['user_contests']} supported user-contests")
                    for e,d in v['endpoints'].items():lines.append(f"  {e}: {d['difference_pp']:+.3f} pp; higher/lower/equal {d['higher']}/{d['lower']}/{d['equal']}; leave-one-game-out {d['omit_one_range']}")
    return '\n'.join(lines)
