"""Read-only Single Entry Showdown construction usage and later outcomes."""
from collections import Counter,defaultdict
import statistics
import analysis_imports as ai
from controlled_construction import BANDS
from showdown_group_review import flags

LABELS={**{'captain_'+p:'Captain '+p for p in ('QB','RB','WR','TE','K','DST')},
    'own_qb':'WR/TE Captain with own QB','opposing_dst':'Captain with opposing DST',
    **{'kd_'+str(n):str(n)+(' or more' if n==3 else '')+' K/DST' for n in range(4)},
    **{'split_'+str(n):str(n)+'-'+str(6-n)+' team split' for n in (3,4,5)},
    **{'salary_'+str(n):v for n,v in enumerate(('< $40,000','$40,000-$44,999','$45,000-$47,499','$47,500-$48,999','$49,000-$50,000'))},
    'kickers':'Two kickers','defenses':'Two defenses','quarterbacks':'Two QBs','qb_dst':'QB with opposing DST'}
KEYS=tuple(LABELS)
ENDPOINTS=('top1','top01','rank1')
MIN_GAMES=5
MAX_REVIEW_ENTRIES=1000000


def features(slots):
    captain=next(p for _,role,p in slots if role=='CPT')
    positions=Counter(p['position'] for _,_,p in slots);teams=Counter(p['team'] for _,_,p in slots)
    kd=positions['K']+positions['DST'];salary=sum(p['salary'] for _,_,p in slots)
    band=next(i for i,ceiling in enumerate(BANDS) if salary<ceiling)
    values={'captain_'+p:int(captain['position']==p) for p in ('QB','RB','WR','TE','K','DST')}
    values.update(own_qb=int(any(p['position']=='QB' and p['team']==captain['team'] for _,_,p in slots)) if captain['position'] in ('WR','TE') else None,
        opposing_dst=int(any(p['position']=='DST' and p['team']!=captain['team'] for _,_,p in slots)))
    values.update({'kd_'+str(n):int(kd==n if n<3 else kd>=3) for n in range(4)})
    values.update({'split_'+str(n):int(max(teams.values())==n) for n in (3,4,5)})
    values.update({'salary_'+str(n):int(band==n) for n in range(5)})
    values.update({k:int(v) for k,v in flags(slots).items() if k in KEYS})
    return tuple(values[k] for k in KEYS)


class Observer:
    def __init__(self,selected,denominator):
        self.selected=set(selected);self.denominator=denominator;self.seen=0;self.unknown=0;self.missing_rank=0;self.eligible=0;self.cohort_eligible=0
        self.field={k:[[0]*4 for _ in range(2)] for k in KEYS}
        self.cohort={k:[[0]*4 for _ in range(2)] for k in KEYS}
    def observe(self,eid,rank,pattern,entry_user=None):
        self.last_pattern=pattern
        self.seen+=1;ranked=type(rank) is int and 0<rank<=2**53-1
        self.unknown+=int(pattern is None);self.missing_rank+=int(not ranked)
        if pattern is None or not ranked:return
        self.eligible+=1;selected=entry_user in self.selected;self.cohort_eligible+=int(selected)
        outcome=(1,int(rank<=(self.denominator+99)//100),int(rank<=(self.denominator+999)//1000),int(rank==1))
        for key,present in zip(KEYS,pattern):
            if present is None:continue
            for i,n in enumerate(outcome):
                self.field[key][present][i]+=n
                if selected:self.cohort[key][present][i]+=n
    def finish(self,accepted):
        if self.seen!=accepted:raise ValueError('Single-entry field coverage disagrees with its receipt.')
        return dict(accepted=accepted,eligible=self.eligible,unknown_rosters=self.unknown,missing_ranks=self.missing_rank,
            selected_users=len(self.selected),cohort_eligible=self.cohort_eligible,rank_denominator=self.denominator,
            field=self.field,cohort=self.cohort)


class Duplication:
    """Receives only exact, already resolved role/ID signatures from the audit."""
    def __init__(self):self.rosters={}
    def observe(self,eid,rank,signature,pattern=None):
        if pattern is not None:
            pattern=tuple(pattern)
            if len(pattern)!=len(KEYS) or any(v is not None and (type(v) is not int or v not in (0,1)) for v in pattern):raise ValueError('Invalid verified construction features.')
        from controlled_construction import MAX_SIGNATURES
        from portfolio_pattern_audit import PatternAuditLimit
        if signature not in self.rosters:
            if len(self.rosters)>=MAX_SIGNATURES:raise PatternAuditLimit('Single Entry duplication signature bound reached; no partial report published.')
            self.rosters[signature]=[0,0,pattern]
        elif self.rosters[signature][2]!=pattern:raise ValueError('Same verified roster has conflicting construction features.')
        row=self.rosters[signature];row[0]+=1;row[1]+=int(type(rank) is int and rank==1)
    def finish(self):
        histogram=Counter(row[0] for row in self.rosters.values())
        rows=[row[:2] for row in self.rosters.values()]
        from duplication_construction import build_rows
        constructions=build_rows(self.rosters.values(),KEYS)
        return dict(resolved_entries=sum(n for n,_ in rows),unique_rosters=len(rows),
            singleton_rosters=histogram[1],duplicated_rosters=sum(v for k,v in histogram.items() if k>1),
            entries_in_duplicated_rosters=sum(n for n,_ in rows if n>1),extra_copies=sum(n-1 for n,_ in rows),
            maximum_copies=max(histogram,default=0),rank_one_resolved_entries=sum(w for _,w in rows),
            distinct_rank_one_rosters=sum(w>0 for _,w in rows),rank_one_entries_in_shared_rosters=sum(w for n,w in rows if n>1),
            copy_histogram=[dict(copies=k,rosters=v,entries=k*v) for k,v in sorted(histogram.items())],by_construction=constructions)


def validate_users(conn,key,cancelled):
    from opponent_analysis import username_key
    for user,n,name in conn.execute('SELECT e.user_key,COUNT(*),u.username FROM opponent_entries e LEFT JOIN opponent_users u ON u.user_key=e.user_key WHERE e.contest_key=? GROUP BY e.user_key',(key,)):
        ai._check(cancelled)
        if n!=1 or not name or user!='dk:'+username_key(name):raise ValueError('Single Entry requires exactly one indexed entry per verified username.')


def audit(conn,key,players,accepted,size,selected,cancelled):
    from construction_outcomes import contest_outcomes
    observer=Observer(selected,size if type(size) is int and size>0 else accepted)
    duplication=Duplication()
    contest_outcomes(conn,key,players,accepted,size,None,cancelled,_features=features,_metrics=KEYS,_rule_metrics=(),_observer=observer,_signature_observer=lambda eid,rank,signature:duplication.observe(eid,rank,signature,observer.last_pattern))
    result=observer.finish(accepted);result['duplication']=duplication.finish()
    if result['duplication']['resolved_entries']!=accepted-result['unknown_rosters']:raise ValueError('Duplication coverage differs from resolved field coverage.')
    return result


LOWER_BOUND_BUCKET='10,000 or more (observed lower bound; total unknown)'


def field_bucket(size,observed=None):
    if type(size) is int and type(observed) is int and size<observed:return 'Unknown field size'
    if size is None and type(observed) is int and observed>=10000:return LOWER_BOUND_BUCKET
    if type(size) is not int or size<=0:return 'Unknown field size'
    return 'Under 1,000' if size<1000 else '1,000-9,999' if size<10000 else '10,000 or more'


def field_scope(contest):
    detail=contest.get('single_entry') or {}
    accepted=contest.get('accepted')
    # Older or inconsistent captures cannot establish a verified observed bound.
    observed=accepted if type(accepted) is int and type(detail.get('accepted')) is int and detail['accepted']==accepted else None
    return field_bucket(contest.get('field_size'),observed)


def summarize(report,minimum=10):
    if minimum not in (5,10) or type(minimum) is not int:raise ValueError('Use five or ten entries per side.')
    groups=defaultdict(lambda:defaultdict(list))
    for c in report['contests']:
        if c['format']!='showdown' or c['preset']!='Single Entry':continue
        detail=c.get('single_entry')
        if detail is None:continue
        for metric in KEYS:
            field=detail['field'][metric];cohort=detail['cohort'][metric]
            for endpoint,index in zip(ENDPOINTS,(1,2,3)):
                if min(field[0][0],field[1][0])>=minimum:
                    gap=100*(field[1][index]/field[1][0]-field[0][index]/field[0][0])
                    groups[(field_scope(c),metric,endpoint,'field finish rate')][(c['date'],tuple(c['slate'][2]))].append(gap)
            # Compare usage against noncohort entries, without selecting on later success.
            n=sum(v[0] for v in cohort);other=[field[i][0]-cohort[i][0] for i in (0,1)];m=sum(other)
            if min(n,m)>=minimum:
                gap=100*(cohort[1][0]/n-other[1]/m)
                groups[(field_scope(c),metric,'usage','earlier cohort minus noncohort')][(c['date'],tuple(c['slate'][2]))].append(gap)
    out=[]
    for key,games in sorted(groups.items()):
        values=[statistics.mean(v) for _,v in sorted(games.items())]
        omit=[statistics.mean(values[:i]+values[i+1:]) for i in range(len(values))] if len(values)>=3 else []
        out.append(dict(bucket=key[0],metric=key[1],endpoint=key[2],comparison=key[3],games=len(values),
            status='supported descriptive sample' if len(values)>=MIN_GAMES else 'insufficient independent games',
            difference_pp=statistics.mean(values),higher=sum(v>0 for v in values),lower=sum(v<0 for v in values),equal=sum(v==0 for v in values),
            omit_one_range=[min(omit),max(omit)] if omit else None))
    return out


def render(report):
    lines=['SINGLE ENTRY SHOWDOWN - USAGE AND LATER FINISHES',
        'Field sizes stay separate: under 1,000; 1,000-9,999; 10,000 or more; unknown. A bucket is a descriptive scope, not a strategy recommendation.',
        'With a missing total, at least 10,000 captured accepted entries establish only a lower bound. These contests have a separate scope; neither total size nor completeness is inferred. Smaller observed counts cannot establish an upper bound.',
        'Earlier cohort: at least three earlier ranked salary slates and two earlier top-cutoff finishes in Single Entry. Same-day and future results never qualify; later losses stay included.',
        'Field finish rate: feature-present minus feature-absent. Cohort usage: earlier-selected users minus remaining field. These are different questions.',
        'Only resolved, ranked entries contribute. Each verified username must have exactly one indexed entry. All rank-one ties count. Unknown rosters/ranks are excluded, never losses.',
        'Rank cutoffs are ceil(denominator/100) and ceil(denominator/1000); supplied field size or observed entries if unavailable. Partial exports do not establish true field percentiles.',
        'Contests average equally within a salary game/date, then games equally. Repeated users and lineups are not independent. Five games is a descriptive minimum, not a significance test.',
        'No Captain/salary matching or control for player quality in these raw associations. No automatic grouping, ranking or generation changes.',
        'Entry-fee matching and payouts/ROI are unavailable in this report. Rank-one ties do not establish identical lineups.',
        'Payout evidence checks source column names only. Values, cash prize type, compatible currency, entry identity and coverage remain unverified. Rank, contest titles, prize pools and dollar symbols cannot establish returns; missing winnings are not zero.',
        'Exact duplication uses resolved Captain ID plus the five unordered FLEX IDs, within each contest. Counts include resolved entries with missing ranks; finishing comparisons still exclude missing ranks. Unresolved and unseen copies remain unknown, so observed singletons are not proof of full-field uniqueness.']
    contests=[c for c in report['contests'] if c.get('single_entry') is not None]
    if not contests:lines.append('Unavailable: no verified Single Entry Showdown field evidence in this review.')
    for c in contests:
        d=c['single_entry'];lines.append(f"{c['date']} {c['name']} | {field_scope(c)}: {d['eligible']}/{d['accepted']} ranked/resolved; {d['unknown_rosters']} unresolved rosters; {d['missing_ranks']} missing ranks; earlier cohort {d['cohort_eligible']}/{d['selected_users']}; complete-field receipt {c['complete_field']}; rank denominator {d['rank_denominator']}.")
        from historical_cash_coverage import render as render_cash_coverage
        lines.append(render_cash_coverage(d.get('cash_evidence')))
        duplication=d.get('duplication')
        if duplication is None:lines.append('  Exact duplication: unavailable in this older captured review; rerun the source-verified review to collect it.')
        else:
            lines.append(f"  Exact duplication: {duplication['resolved_entries']} resolved entries; {duplication['unique_rosters']} distinct observed rosters; {duplication['singleton_rosters']} observed singleton rosters; {duplication['duplicated_rosters']} shared rosters containing {duplication['entries_in_duplicated_rosters']} entries; {duplication['extra_copies']} extra copies; largest observed copy count {duplication['maximum_copies']}.")
            lines.append(f"  Resolved rank-one entries: {duplication['rank_one_resolved_entries']}; distinct rank-one rosters: {duplication['distinct_rank_one_rosters']}; rank-one entries whose rosters are shared: {duplication['rank_one_entries_in_shared_rosters']}. No payout split is inferred.")
            lines.append('  Observed copy distribution (copies per roster: number of rosters): '+', '.join(str(v['copies'])+': '+str(v['rosters']) for v in duplication['copy_histogram']))
        from single_entry_construction_display import render as render_construction_shares
        lines.append(render_construction_shares(d))
        for metric in KEYS:
            field=d['field'][metric];cohort=d['cohort'][metric];denom=sum(v[0] for v in field);n=sum(v[0] for v in cohort)
            lines.append(f"  {LABELS[metric]}: field usage {field[1][0]}/{denom}; earlier-cohort usage {cohort[1][0]}/{n}; feature-present top1/top0.1/rank1 counts {field[1][1:]}; feature-absent counts {field[0][1:]}.")
    for minimum in (5,10):
        lines.append(f'At least {minimum} entries per side for field finishes, or {minimum} cohort and {minimum} noncohort eligible entries for usage (metric-specific):')
        rows=summarize(report,minimum)
        if not rows:lines.append('Unavailable: no supported comparisons.')
        for row in rows:
            prefix=f"{row['bucket']} / {LABELS[row['metric']]} / {row['comparison']} / {row['endpoint']}: {row['games']} games; {row['status']}"
            if row['games']>=MIN_GAMES:prefix+=f"; {row['difference_pp']:+.3f} pp; higher/lower/equal {row['higher']}/{row['lower']}/{row['equal']}; omit-one {row['omit_one_range']}"
            lines.append(prefix+'.')
    from duplication_construction import render as render_duplication
    lines+=['',render_duplication(report)]
    return '\n'.join(lines)


def review(db_path,through,cancelled=lambda:False,progress=lambda text:None,*,since='',followup_from=''):
    from historical_series_review import _inventory
    from winner_portfolio_review import review_winners
    before=_inventory(db_path)
    result=review_winners(db_path,through,cancelled,progress,contest_format='showdown',since=since,review_mode='prior_qualified',followup_from=followup_from,single_entry=True)
    if _inventory(db_path)!=before:raise ValueError('Historical index or salary mappings changed during Single Entry review; rerun.')
    ai._check(cancelled)
    return result
