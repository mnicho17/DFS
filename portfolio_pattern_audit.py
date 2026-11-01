"""Bounded read-only Showdown peer-pattern audit against qualified salary metadata."""
from collections import Counter,OrderedDict,defaultdict
import analysis_imports as ai
from controlled_construction import Builder,MAX_ENTRIES
from showdown_group_review import flags

METRICS=('split_5_1_pct','split_4_2_pct','split_3_3_pct','qb_opposing_dst_pct','all_four_pct')
LABELS=dict(split_5_1_pct='5–1 team construction (%)',split_4_2_pct='4–2 team construction (%)',split_3_3_pct='3–3 team construction (%)',qb_opposing_dst_pct='QB with opposing DST, all slots (%)',all_four_pct='Meets all four grouping restrictions (%)')
class PatternAuditLimit(ValueError):
    pass

MAX_USERS=MAX_ENTRIES
MAX_RESOLUTIONS=200000
MAX_CACHE=4096


def features(slots):
    teams=sorted(Counter(p['team'] for _,_,p in slots).values())
    value=flags(slots)
    return (int(teams==[1,5]),int(teams==[2,4]),int(teams==[3,3]),int(value['qb_dst']),int(value['all_four']))


def audit_peers(conn,key,players,users,cancelled=lambda:False,progress=lambda text:None,*,pairing_usage=False):
    """Caller verifies original sources/receipts. Every eligible peer entry is read.

    Cache keys include every raw slot identity/label/role/metadata field, not the
    name-only lineup signature. Unknown copies never borrow a known resolution.
    Limits are additional to (and do not enlarge) the selected-portfolio limits.
    """
    ai._check(cancelled)
    if len(users)>MAX_USERS:raise PatternAuditLimit('Peer-pattern user audit limit reached.')
    if any(type(n) is not int or n<=0 for n in users.values()):raise ValueError('Peer-pattern expected entrant count is invalid.')
    if sum(users.values())>MAX_ENTRIES:raise PatternAuditLimit('Peer-pattern entry audit limit reached; narrow the evaluation.')
    totals={u:dict(entries=0,known=0,counts=[0]*len(METRICS),usage=[0]*6) for u in users}
    builder=Builder(players,max(1,sum(users.values())));cache=OrderedDict();missing=object();reads=resolutions=slot_reads=0
    ordered=sorted(users)
    for start in range(0,len(ordered),400):
        wanted=ordered[start:start+400]
        sql='SELECT entry_id,user_key,metadata_complete FROM opponent_entries WHERE contest_key=? AND user_key IN ('+','.join('?' for _ in wanted)+') ORDER BY entry_id'
        cursor=conn.execute(sql,[key,*wanted])
        while block:=cursor.fetchmany(200):
            ai._check(cancelled);reads+=len(block)
            if reads>MAX_ENTRIES:raise PatternAuditLimit('Peer-pattern entry audit limit reached.')
            ids=[r[0] for r in block];slots=defaultdict(list);oversized=set()
            query='SELECT entry_id,ordinal,role,original_label,player_id,player_name,position,team,salary FROM opponent_entry_slots WHERE contest_key=? AND entry_id IN ('+','.join('?' for _ in ids)+') ORDER BY entry_id,ordinal'
            for eid,*row in conn.execute(query,[key,*ids]):
                slot_reads+=1
                if slot_reads>6*MAX_ENTRIES:raise PatternAuditLimit('Peer-pattern slot read limit reached.')
                if len(slots[eid])<7:slots[eid].append(tuple(row))
                else:oversized.add(eid)
            for eid,user,complete in block:
                item=totals[user];item['entries']+=1;raw=slots[eid]
                if complete!=1 or eid in oversized or [r[0] for r in raw]!=list(range(6)):continue
                identity=tuple(raw)
                value=cache.get(identity,missing)
                if value is missing:
                    resolutions+=1
                    if resolutions>MAX_RESOLUTIONS:raise PatternAuditLimit('Peer-pattern identity resolution limit reached; no partial audit published.')
                    parsed=[(label,role,dict(id=ident,name=name,position=pos,team=team,salary=salary)) for ordinal,role,label,ident,name,pos,team,salary in raw]
                    value=features(parsed) if builder._resolve(parsed) is not None else None
                    if value is not None and pairing_usage:
                        from pairing_usage import counts
                        value=(value,counts(parsed))
                    cache[identity]=value
                    if len(cache)>MAX_CACHE:cache.popitem(last=False)
                else:cache.move_to_end(identity)
                if value is None:continue
                item['known']+=1
                if pairing_usage:
                    value,usage=value
                    for i,n in enumerate(usage):item['usage'][i]+=n
                for i,n in enumerate(value):item['counts'][i]+=n
            if reads==len(block) or reads%5000<len(block):progress(f'Auditing peer constructions: {reads:,}/{sum(users.values()):,} entries; exact slot identities checked.')
    result={}
    for user,item in totals.items():
        if item['entries']!=users[user]:raise ValueError('Peer-pattern entry count differs from the indexed entrant receipt.')
        complete=item['known']==item['entries']
        result[user]=dict(entries=item['entries'],known=item['known'],unknown=item['entries']-item['known'],metrics={k:100*item['counts'][i]/item['entries'] if complete else None for i,k in enumerate(METRICS)})
        if pairing_usage:
            from pairing_usage import metrics,KEYS
            result[user]['pairing_usage']=metrics(item['usage']) if complete else dict.fromkeys(KEYS)
    ai._check(cancelled)
    return result
