"""Exact indexed-entry-count peers for descriptive winner portfolio comparisons."""
from collections import defaultdict
import statistics
import analysis_imports as ai
from portfolio_pattern_audit import PatternAuditLimit
from winner_pairing_review import Observer,counts,rates,add,LABELS

MAX_PEER_USERS=100000
MAX_PEER_ENTRIES=1000000
MAX_REVIEW_PEER_ENTRIES=5000000
SUPPORT=(5,10)


class SizeMatchedObserver(Observer):
    def __init__(self,conn,key,cancelled):
        super().__init__(conn,key,cancelled)
        self.peers={};self.peer_entries=0
        sizes=sorted({row['entries'] for row in self.users.values()})
        if not sizes:return
        placeholders=','.join('?' for _ in sizes)
        sql='''SELECT e.user_key,COUNT(*),u.username FROM opponent_entries e
            LEFT JOIN opponent_users u ON u.user_key=e.user_key
            WHERE e.contest_key=? AND e.user_key NOT IN
            (SELECT user_key FROM opponent_entries WHERE contest_key=? AND rank=1)
            GROUP BY e.user_key HAVING COUNT(*) IN ('''+placeholders+') ORDER BY e.user_key'
        from opponent_analysis import username_key
        for user,n,name in conn.execute(sql,[key,key,*sizes]):
            ai._check(cancelled)
            self.peer_entries+=n
            if len(self.peers)>=MAX_PEER_USERS or self.peer_entries>MAX_PEER_ENTRIES:
                raise PatternAuditLimit('Size-matched peer audit bound reached; no partial report published.')
            self.peers[user]=dict(entries=n,seen=0,known=0,counts=[0]*6,
                identity_ok=bool(name and isinstance(user,str) and user=='dk:'+username_key(name)))

    def observe(self,eid,rank,pattern,entry_user=None):
        super().observe(eid,rank,pattern,entry_user)
        item=self.peers.get(entry_user)
        if item is None:return
        item['seen']+=1
        if pattern is not None:item['known']+=1;add(item['counts'],counts(pattern))

    def finish(self,accepted):
        result=super().finish(accepted)
        bins={};excluded=0
        for _,item in sorted(self.peers.items()):
            ai._check(self.cancelled)
            if item['seen']!=item['entries']:
                raise ValueError('Peer portfolio observation count differs from its indexed entry receipt.')
            group=bins.setdefault(str(item['entries']),dict(users=0,qualified=0,excluded=0,metric_users=[0]*5,values=[[] for _ in LABELS]))
            group['users']+=1
            if not item['identity_ok'] or item['known']!=item['entries']:
                group['excluded']+=1;excluded+=1;continue
            group['qualified']+=1
            for i,value in enumerate(rates(item['counts'])):
                if value is not None:group['values'][i].append(value)
        for group in bins.values():
            group['metric_users']=[len(v) for v in group['values']]
            group['means']=[statistics.mean(v) if v else None for v in group.pop('values')]
        result.update(size_matched_peers=bins,peer_users=len(self.peers),peer_entries=self.peer_entries,peer_excluded=excluded)
        return result


def summarize(contests,minimum):
    games=defaultdict(list);winner_support=[0]*len(LABELS)
    for contest in contests:
        detail=contest['winner_portfolios'];values=[[] for _ in LABELS]
        for row in detail['users']:
            if not row['complete']:continue
            peer=detail.get('size_matched_peers',{}).get(str(row['entries']))
            if peer is None:continue
            for i,value in enumerate(row['rates']):
                if value is not None and peer['metric_users'][i]>=minimum and peer['means'][i] is not None:
                    values[i].append(value-peer['means'][i]);winner_support[i]+=1
        games[tuple(contest['game'])].append([statistics.mean(v) if v else None for v in values])
    metrics=[]
    for i,label in enumerate(LABELS):
        values=[]
        for _,rows in sorted(games.items()):
            observed=[r[i] for r in rows if r[i] is not None]
            if observed:values.append(statistics.mean(observed))
        metrics.append(dict(label=label,games=len(values),winner_portfolios=winner_support[i],
            difference_pp=statistics.mean(values) if values else None,
            higher_games=sum(v>0 for v in values),lower_games=sum(v<0 for v in values),equal_games=sum(v==0 for v in values),
            omit_one_range=[min(statistics.mean(values[:j]+values[j+1:]) for j in range(len(values))),
                            max(statistics.mean(values[:j]+values[j+1:]) for j in range(len(values)))] if len(values)>=3 else None))
    return dict(minimum_peers=minimum,metrics=metrics)


def render(report):
    if 'size_matched_by_preset' not in report:
        return '\nSame-size peer comparisons are unavailable in this older saved report.'
    lines=['','WINNERS VERSUS SAME-SIZE INDEXED PORTFOLIOS',
        'Same contest and exact indexed entry count. Peer users have no recorded rank-one entry in that contest; all observed winners/ties are excluded from peers.',
        'Every indexed peer entry is audited. One unresolved roster or inconsistent username excludes the entire peer portfolio. Receiver support counts only peers with WR/TE Captain entries.',
        'Equal-user peer means; winner-minus-peer differences averaged across winners within contest, contests within game, then games equally. Minimum five and ten eligible peers checked separately.',
        'No broad entry-count fallback. This is not matching by Captain, salary or prior success; partial exports and hindsight winner selection remain limitations. No settings change.']
    for preset,checks in report.get('size_matched_by_preset',{}).items():
        lines+=['',preset or 'Unknown entry limit']
        for minimum,detail in checks.items():
            lines.append(f'  At least {minimum} eligible peer users:')
            for row in detail['metrics']:
                gap=row['difference_pp'];text='unavailable' if gap is None else f'{gap:+.3f} pp'
                lines.append(f"    {row['label']}: {text}; {row['winner_portfolios']} supported winner portfolios / {row['games']} games; higher/lower/equal {row['higher_games']}/{row['lower_games']}/{row['equal_games']}; omit-one-game range {row.get('omit_one_range')}")
    for c in report['contests']:
        d=c['winner_portfolios']
        if 'peer_users' in d:lines.append(f"{c['name']}: {d['peer_users']} peer users / {d['peer_entries']:,} indexed peer entries; {d['peer_excluded']} incomplete peer portfolios withheld.")
    return '\n'.join(lines)
