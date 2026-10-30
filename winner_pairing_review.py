"""Bounded, read-only full-portfolio usage for all observed rank-one entrants."""
from collections import defaultdict
import statistics
import analysis_imports as ai
from portfolio_pattern_audit import PatternAuditLimit

MAX_ENTRIES=300000
LABELS=('WR/TE Captain with own QB','Zero K/DST','One K/DST','Two K/DST','Three or more K/DST')


def counts(pattern):
    category=next((i for i,v in enumerate(pattern[1:],1) if v==1),0)
    return [int(pattern[0] is not None),int(pattern[0]==1),*(int(category==i) for i in range(4))]


def rates(values):
    n=sum(values[2:])
    return [100*values[1]/values[0] if values[0] else None,
            *(100*v/n if n else None for v in values[2:])]


def add(target,value):
    for i,n in enumerate(value):target[i]+=n


class Observer:
    def __init__(self,conn,key,cancelled):
        from opponent_analysis import username_key
        self.cancelled=cancelled;self.entries={};self.users={};self.field=[0]*6;self.field_seen=0;self.field_known=0
        cursor=conn.execute('''SELECT e.entry_id,e.user_key,e.rank,u.username FROM opponent_entries e
            LEFT JOIN opponent_users u ON u.user_key=e.user_key
            WHERE e.contest_key=? AND e.user_key IN
            (SELECT user_key FROM opponent_entries WHERE contest_key=? AND rank=1)
            ORDER BY e.entry_id''',(key,key))
        for eid,user,rank,name in cursor:
            ai._check(cancelled)
            if len(self.entries)>=MAX_ENTRIES:raise PatternAuditLimit('Winner portfolio entry bound reached; no partial report published.')
            self.entries[eid]=user
            item=self.users.setdefault(user,dict(user_key=user,username=name or '',entries=0,seen=0,known=0,
                winning_entries=0,known_wins=0,missing_rank=0,counts=[0]*6,winning_counts=[0]*6,
                identity_ok=bool(name and isinstance(user,str) and user=='dk:'+username_key(name))))
            item['entries']+=1;item['winning_entries']+=int(rank==1)

    def observe(self,eid,rank,pattern):
        self.field_seen+=1
        user=self.entries.get(eid);item=self.users.get(user)
        if item is not None:
            item['seen']+=1
            item['missing_rank']+=int(type(rank) is not int or not 0<rank<=2**53-1)
        if pattern is None:return
        value=counts(pattern);self.field_known+=1;add(self.field,value)
        if item is None:return
        item['known']+=1;add(item['counts'],value)
        if rank==1:item['known_wins']+=1;add(item['winning_counts'],value)

    def finish(self,accepted):
        ai._check(self.cancelled)
        if self.field_seen!=accepted:raise ValueError('Field observation count differs from the indexed receipt.')
        rows=[]
        for user,item in sorted(self.users.items()):
            ai._check(self.cancelled)
            if item['seen']!=item['entries']:raise ValueError('Winner portfolio observation count differs from its entry receipt.')
            complete=bool(item['identity_ok'] and item['known']==item['entries'] and item['known_wins']==item['winning_entries'])
            other=[a-b for a,b in zip(item['counts'],item['winning_counts'])]
            rows.append(dict(user_key=user,username=item['username'],entries=item['entries'],known=item['known'],
                unknown=item['entries']-item['known'],winning_entries=item['winning_entries'],missing_rank=item['missing_rank'],
                complete=complete,counts=item['counts'] if complete else None,
                winning_counts=item['winning_counts'] if complete else None,
                other_counts=other if complete else None,
                rates=rates(item['counts']) if complete else [None]*5,
                winning_rates=rates(item['winning_counts']) if complete else [None]*5,
                other_rates=rates(other) if complete else [None]*5))
        return dict(field_entries=accepted,field_known=self.field_known,field_unknown=accepted-self.field_known,
                    field_counts=self.field,field_rates=rates(self.field),users=rows)


def summarize(contests):
    games=defaultdict(list);paired_games=defaultdict(list);users=qualified=entries=missing=0
    for contest in contests:
        detail=contest['winner_portfolios'];users+=len(detail['users'])
        complete=[row for row in detail['users'] if row['complete']]
        qualified+=len(complete);entries+=sum(row['entries'] for row in complete)
        missing+=sum(row['missing_rank'] for row in complete)
        gaps=[]
        for i,field in enumerate(detail['field_rates']):
            values=[row['rates'][i] for row in complete if row['rates'][i] is not None]
            gaps.append(statistics.mean(values)-field if values and field is not None else None)
        games[tuple(contest['game'])].append(gaps)
        paired=[]
        for i in range(len(LABELS)):
            values=[row['winning_rates'][i]-row['other_rates'][i] for row in complete
                    if row.get('winning_rates',[None]*5)[i] is not None and row.get('other_rates',[None]*5)[i] is not None]
            paired.append(statistics.mean(values) if values else None)
        paired_games[tuple(contest['game'])].append(paired)
    metrics=[]
    for i,label in enumerate(LABELS):
        values=[]
        for _,contests_in_game in sorted(games.items()):
            observed=[row[i] for row in contests_in_game if row[i] is not None]
            if observed:values.append(statistics.mean(observed))
        metrics.append(dict(label=label,games=len(values),difference_pp=statistics.mean(values) if values else None,
            higher_games=sum(v>0 for v in values),lower_games=sum(v<0 for v in values),equal_games=sum(v==0 for v in values)))
    paired_metrics=[]
    for i,label in enumerate(LABELS):
        values=[]
        for _,rows in sorted(paired_games.items()):
            observed=[row[i] for row in rows if row[i] is not None]
            if observed:values.append(statistics.mean(observed))
        paired_metrics.append(dict(label=label,games=len(values),difference_pp=statistics.mean(values) if values else None,
            higher_games=sum(v>0 for v in values),lower_games=sum(v<0 for v in values),equal_games=sum(v==0 for v in values)))
    return dict(winner_user_contests=users,qualified_user_contests=qualified,excluded_user_contests=users-qualified,
                qualified_entries=entries,qualified_missing_rank=missing,metrics=metrics,winning_minus_other=paired_metrics)


def render(report):
    if any('winner_portfolios' not in c for c in report['contests']):
        return '\nComplete winner-portfolio usage is unavailable in this older saved report.'
    lines=['','OBSERVED WINNERS: COMPLETE INDEXED PORTFOLIOS',
        'All observed rank-one entrants, including ties. Every indexed entry for each username in that contest is inspected.',
        'A portfolio with any unresolved entry or inconsistent username identity has unavailable rates. Field usage includes only resolved entries; it includes winners.',
        'Usage includes entries with missing ranks; they are not inferred to have lost. Receiver percentages use only WR/TE Captain entries as their denominator.',
        'Summary differences: equal-user portfolio averages minus entry-weighted field usage within each contest; contests averaged within games, then games equally.',
        'These descriptive usage differences are not matched by Captain, salary or portfolio size and are not predictive evidence or profit. Ties and repeated users across contests are not independent. Incomplete contest exports can omit entries.']
    for preset,detail in report.get('winner_portfolios_by_preset',{}).items():
        lines+=['',preset or 'Unknown entry limit',
            f"Winner user-contests: {detail['winner_user_contests']}; qualified complete: {detail['qualified_user_contests']}; excluded: {detail['excluded_user_contests']}; entries in qualified portfolios: {detail['qualified_entries']:,}."]
        for row in detail['metrics']:
            gap=row['difference_pp'];value='unavailable' if gap is None else f'{gap:+.3f} pp'
            lines.append(f"  {row['label']}: {value}; {row['games']} games; higher/lower/equal {row['higher_games']}/{row['lower_games']}/{row['equal_games']}")
        lines.append('  Within-user rank-one entries minus other indexed entries (users need both eligible denominators):')
        for row in detail['winning_minus_other']:
            gap=row['difference_pp'];value='unavailable' if gap is None else f'{gap:+.3f} pp'
            lines.append(f"    {row['label']}: {value}; {row['games']} supported games")
    lines+=['','Individual winner portfolios (rates ordered as '+', '.join(LABELS)+'):']
    for contest in report['contests']:
        lines.append(contest['name'])
        for row in contest['winner_portfolios']['users']:
            lines.append(f"  {row['username']}: {row['entries']} indexed entries; {row['winning_entries']} rank-one entries; {row['unknown']} unresolved; {row['missing_rank']} missing ranks; complete indexed portfolio: {row['complete']}")
            for name,key in (('Whole portfolio','rates'),('Rank-one entries only','winning_rates'),('Other indexed entries','other_rates')):
                count_key={'rates':'counts','winning_rates':'winning_counts','other_rates':'other_counts'}[key]
                denominator=row[count_key][0] if row[count_key] is not None else 'unavailable'
                lines.append('    '+name+f' (WR/TE Captain denominator {denominator}): '+', '.join('unavailable' if v is None else f'{v:.2f}%' for v in row[key]))
    return '\n'.join(lines)
