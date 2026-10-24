"""Report-only matching of indexed Showdown entries; never a model input."""
from collections import Counter,OrderedDict,defaultdict
import json,re,statistics

VERSION=1
TABLE='opponent_construction_receipts'
COHORTS=('field','top1','top5','rank1')
POSITIONS=('QB','RB','WR','TE','K','DST')
BANDS=(40000,45000,47500,49000,50001)
METRICS={'opposing_dst':1,'kd2':2,'kd3':3}
MAX_ENTRIES=1000000
MAX_SIGNATURES=200000
MAX_CELLS=8000
MAX_BYTES=1000000


def count(v):return type(v) is int and v>=0


class Builder:
    def __init__(self,players,accepted,field_size=None):
        self.accepted=accepted
        self.cutoff=field_size if type(field_size) is int and field_size>0 else (accepted if count(accepted) and accepted else 1)
        self.basis='supplied field-size rank cutoff; completeness unverified' if type(field_size) is int and field_size>0 else 'observed accepted-entry rank cutoff; completeness unverified'
        self.lookup=defaultdict(list)
        for p in players:
            for role in p['role'].split('/'):self.lookup[(role,p['id'])].append(p)
        self.groups={k:{} for k in COHORTS};self.seen={k:set() for k in COHORTS}
        self.captains={};self.cache=OrderedDict();self.rows=0;self.missing_rank=0;self.reason=('Supplied field size is below accepted-entry count; controlled rank cutoffs unavailable.' if type(field_size) is int and count(accepted) and field_size<accepted else ('Invalid accepted-entry receipt.' if not count(accepted) or not accepted else None));self.cells=0
    def _resolve(self,slots):
        from analysis_imports import _name
        if len(slots)!=6 or Counter(role for _,role,_ in slots)!=Counter(CPT=1,FLEX=5):return None
        players=[]
        for label,role,p in slots:
            if not p:return None
            matches=self.lookup.get((role,p.get('id')),())
            if len(matches)!=1:return None
            exact=matches[0]
            if any(p.get(k)!=exact.get(k) for k in ('name','position','team','salary')):return None
            explicit=re.search(r'\((\d+)\)\s*$',label)
            if _name(label)!=_name(exact['name']) or (explicit and explicit[1]!=p['id']):return None
            if (not isinstance(p['id'],str) or not p['id'] or len(p['id'])>64 or '|' in p['id'] or
                    p['position'] not in POSITIONS or not p['team'] or not count(p['salary'])):return None
            players.append((role,exact))
        if len({_name(p['name']) for _,p in players})!=6:return None
        teams=Counter(p['team'] for _,p in players);salary=sum(p['salary'] for _,p in players)
        if len(teams)!=2 or salary>50000:return None
        captain=next(p for role,p in players if role=='CPT');ident=captain['id']
        band=next(i for i,ceiling in enumerate(BANDS) if salary<ceiling)
        key=f"{ident}|{band}|{teams[captain['team']]}"
        signature=tuple(sorted((role,p['id']) for role,p in players))
        kd=sum(p['position'] in ('K','DST') for _,p in players)
        opposing=any(role!='CPT' and p['position']=='DST' and p['team']!=captain['team'] for role,p in players)
        return key,signature,(1,int(opposing),int(kd>=2),int(kd>=3)),[captain['name'],captain['team'],captain['position']]
    def add(self,slots,rank,readable=True):
        if not (type(rank) is int and 0<rank<=2**53-1):rank=None
        counts=dict(field=1,top1=0,top5=0,rank1=0)
        if rank is not None:
            if rank==1:counts['rank1']=1
            if rank<=self.cutoff:
                if rank<=(self.cutoff+99)//100:counts['top1']=1
                if rank<=(self.cutoff+19)//20:counts['top5']=1
        self.add_group(slots,counts,int(rank is None),readable)
    def add_group(self,slots,counts,missing_rank,readable=True):
        self.rows+=counts['field'];self.missing_rank+=missing_rank
        if self.reason:return
        if self.rows>MAX_ENTRIES:self.reason='Indexed entry read limit reached; no partial controls published.';return
        key=(bool(readable),)+tuple((label,role,p.get('id') if p else None) for label,role,p in slots)
        key+=(tuple(tuple(p.get(k) for k in ('name','position','team','salary')) if p else () for _,_,p in slots),)
        if key in self.cache:resolved=self.cache.pop(key)
        else:resolved=self._resolve(slots) if readable else None
        self.cache[key]=resolved
        if len(self.cache)>4096:self.cache.popitem(last=False)
        if resolved is None:return
        cell,signature,values,captain=resolved;self.captains[cell.split('|')[0]]=captain
        for cohort,n in counts.items():
            if not n:continue
            if cell not in self.groups[cohort]:self.groups[cohort][cell]=[0]*5;self.cells+=1
            v=self.groups[cohort][cell]
            for i,value in enumerate(values):v[i]+=n*value
            if signature not in self.seen[cohort]:self.seen[cohort].add(signature);v[4]+=1
        if self.cells>MAX_CELLS or len(self.seen['field'])>MAX_SIGNATURES:
            self.reason='Construction receipt memory limit reached; no partial controls published.'
    def payload(self):
        if self.reason or self.rows!=self.accepted or not count(self.accepted) or not self.accepted:
            return dict(version=VERSION,complete=False,reason=self.reason or 'Indexed entry count differs from validated receipt.')
        r=dict(version=VERSION,complete=True,accepted=self.accepted,missing_rank=self.missing_rank,
               cutoff=self.cutoff,rank_basis=self.basis,captains=self.captains,groups=self.groups)
        if len(json.dumps(r).encode())>MAX_BYTES:return dict(version=VERSION,complete=False,reason='Construction receipt storage limit reached.')
        return r


def from_index(conn,key,players,accepted,field_size,cancelled=lambda:False):
    """Caller qualifies the source/ID mapping first. Preserve every entry weight.

    Index v2 rejects same-name/ID collapse and contradictory EntryId copies.
    Group only its canonical roster signatures AND metadata-complete flag; resolve
    a deterministic representative against the exact historical salary catalog.
    Missing metadata never borrows resolution from a complete group. Aliased
    signatures still share the exact role/ID signature for distinct-lineup counts.
    """
    from analysis_imports import _check
    _check(cancelled);b=Builder(players,accepted,field_size)
    total=conn.execute('SELECT COUNT(*) FROM opponent_entries WHERE contest_key=?',(key,)).fetchone()[0]
    if total!=accepted or total>MAX_ENTRIES:
        return dict(version=VERSION,complete=False,reason='Indexed entry count differs or exceeds the read limit; no partial controls published.')
    valid="typeof(rank)='integer' AND rank BETWEEN 1 AND 9007199254740991"
    sql=f'''SELECT signature_json,metadata_complete,MIN(entry_id),COUNT(*),
        SUM(CASE WHEN {valid} AND rank<=? THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND rank<=? THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND rank=1 THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} THEN 0 ELSE 1 END)
        FROM opponent_entries WHERE contest_key=? GROUP BY signature_json,metadata_complete
        ORDER BY signature_json,metadata_complete'''
    for i,(sig,known,eid,n,top1,top5,rank1,missing) in enumerate(conn.execute(sql,((b.cutoff+99)//100,(b.cutoff+19)//20,key))):
        if i%250==0:_check(cancelled)
        try:
            parsed=json.loads(sig);readable=known==1 and isinstance(parsed,list) and len(parsed)==6
        except (ValueError,TypeError):readable=False
        slots=[]
        if readable:
            for role,label,ident,name,position,team,salary in conn.execute('SELECT role,original_label,player_id,player_name,position,team,salary FROM opponent_entry_slots WHERE contest_key=? AND entry_id=? ORDER BY ordinal LIMIT 10',(key,eid)):
                slots.append((label,role,dict(id=ident,name=name,position=position,team=team,salary=salary)))
        b.add_group(slots,dict(field=n,top1=top1,top5=top5,rank1=rank1),missing,readable)
        if b.reason:break
    _check(cancelled)
    return b.payload()


def validate(r):
    if not isinstance(r,dict) or type(r.get('version')) is not int or r['version']!=VERSION or r.get('complete') is not True:
        raise ValueError((r.get('reason') if isinstance(r,dict) else None) or 'Controlled receipt unavailable; normal field-history indexing prepares it once.')
    if not all(count(r.get(k)) for k in ('accepted','missing_rank','cutoff')) or not 0<r['accepted']<=MAX_ENTRIES or not 0<r['cutoff'] or r['missing_rank']>r['accepted']:
        raise ValueError('Invalid controlled receipt counts.')
    caps=r.get('captains');groups=r.get('groups')
    if not isinstance(caps,dict) or len(caps)>256 or not isinstance(groups,dict) or set(groups)!=set(COHORTS):raise ValueError('Invalid controlled receipt catalog.')
    for ident,p in caps.items():
        if not isinstance(ident,str) or not ident or '|' in ident or not isinstance(p,list) or len(p)!=3 or not all(isinstance(x,str) and x and len(x)<=256 for x in p) or p[2] not in POSITIONS:
            raise ValueError('Invalid Captain identity catalog.')
    cells=0
    for cohort,g in groups.items():
        if not isinstance(g,dict):raise ValueError('Invalid controlled strata.')
        cells+=len(g)
        for k,v in g.items():
            parts=k.split('|')
            if len(parts)!=3 or parts[0] not in caps or parts[1] not in ('0','1','2','3','4') or parts[2] not in ('1','2','3','4','5'):
                raise ValueError('Invalid controlled stratum identity.')
            if not isinstance(v,list) or len(v)!=5 or not all(count(x) for x in v) or not 0<v[0]<=r['accepted'] or any(x>v[0] for x in v[1:]) or not 0<v[4]<=v[0] or v[3]>v[2]:
                raise ValueError('Invalid controlled stratum counts.')
            if cohort!='field':
                f=groups['field'].get(k)
                if not isinstance(f,list) or len(f)!=5 or any(x>y for x,y in zip(v,f)):raise ValueError('Cohort exceeds matched field stratum.')
        if sum(v[0] for v in g.values())>r['accepted']:raise ValueError('Controlled entries exceed validated count.')
    for child,parent in (('rank1','top1'),('top1','top5')):
        for k,v in groups[child].items():
            p=groups[parent].get(k)
            if not isinstance(p,list) or len(p)!=5 or any(x>y for x,y in zip(v,p)):raise ValueError('Nested rank cohorts are inconsistent.')
    if sum(v[4] for v in groups['field'].values())>MAX_SIGNATURES:raise ValueError('Controlled signature limit exceeded.')
    if cells>MAX_CELLS:raise ValueError('Controlled stratum limit exceeded.')
    return r


def observations(conn,*,limit=512,byte_limit=16000000):
    from opponent_history import VERSION as INDEX_VERSION
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone():return dict(complete=True,receipts={},reason='Controlled receipts unavailable; normal field-history indexing prepares them once.')
    receipts=defaultdict(list);size=0
    sql=f'SELECT c.result_hash,c.salary_hash,c.format,c.audit_json,c.field_size,r.version,r.payload_json FROM {TABLE} r JOIN opponent_contests c ON c.contest_key=r.contest_key WHERE c.version=? ORDER BY c.contest_key LIMIT ?'
    for i,(source,salary,kind,audit,field_size,version,encoded) in enumerate(conn.execute(sql,(INDEX_VERSION,limit+1))):
        size+=len(encoded.encode()) if isinstance(encoded,str) else byte_limit+1
        if i>=limit or size>byte_limit:return dict(complete=False,receipts={},reason='Controlled report read limit reached; no partial totals shown.')
        try:
            if version!=VERSION or kind!='showdown' or len(encoded.encode())>MAX_BYTES:raise ValueError('Unsupported controlled receipt.')
            r=validate(json.loads(encoded));a=json.loads(audit)
            if r.get('source_hash')!=source or r.get('salary_hash')!=salary:raise ValueError('Controlled receipt source/salary revision differs.')
            if r['cutoff']!=(field_size if type(field_size) is int and field_size>0 else r['accepted']):raise ValueError('Controlled rank-cutoff receipt differs.')
            if type(a.get('accepted_entries')) is not int or r['accepted']!=a['accepted_entries']:raise ValueError('Controlled receipt no longer matches indexed entry totals.')
        except (ValueError,TypeError,AttributeError) as error:r=dict(reason=str(error))
        receipts[(source,salary)].append(r)
    return dict(complete=True,reason=None,receipts={k:(v[0] if len(v)==1 else dict(reason='Ambiguous controlled source receipt.')) for k,v in receipts.items()})


def attach(contest,data,observation):
    key=(data['results_evidence']['source_hash'],data['salary_evidence']['revision_hash'])
    contest['controls']=observation.get('receipts',{}).get(key) if observation.get('complete') else None
    if not contest['controls']:contest['controls']=dict(reason=observation.get('reason') or 'No controlled receipt for this current result/salary revision.')


def matched(r,position,metric,cohort,*,captain_id=None,_validated=False):
    if not _validated:r=validate(r)
    column=METRICS[metric];groups=r['groups'];f=groups['field'];c=groups[cohort]
    eligible={k:v for k,v in f.items() if r['captains'][k.split('|')[0]][2]==position and (captain_id is None or k.split('|')[0]==captain_id)}
    keys=sorted(set(eligible)&set(c));n=sum(f[k][0] for k in keys)
    if not n:return None
    other_n=sum(c[k][0] for k in keys)
    return dict(field=100*sum(f[k][column] for k in keys)/n,
        comparator=100*sum(f[k][0]*c[k][column]/c[k][0] for k in keys)/n,
        field_rows=n,cohort_rows=other_n,field_total=sum(v[0] for v in eligible.values()),
        cohort_total=sum(v[0] for k,v in c.items() if r['captains'][k.split('|')[0]][2]==position and (captain_id is None or k.split('|')[0]==captain_id)),
        field_repeats=sum(f[k][0]-f[k][4] for k in keys),cohort_repeats=sum(c[k][0]-c[k][4] for k in keys))


def prepare(contests):
    sources=defaultdict(dict);missing=0
    for c in contests:
        if not c.get('source'):missing+=1;continue
        encoded=json.dumps({k:v for k,v in c.items() if k not in ('cohorts','concentration')},sort_keys=True)
        sources[c['source']][encoded]=c
    conflicts=sum(len(v)>1 for v in sources.values());valid=[];unavailable=0
    for _,copies in sorted(sources.items()):
        if len(copies)!=1:continue
        c=next(iter(copies.values()))
        try:
            validate(c.get('controls'))
            if c['controls'].get('source_hash')!=c['source']:raise ValueError('Controlled source identity differs.')
        except (ValueError,TypeError,KeyError):unavailable+=1;continue
        valid.append(c)
    return dict(valid=valid,conflicts=conflicts,missing=missing,unavailable=unavailable)


def comparison(contests,position,metric,cohort,*,_prepared=None):
    from historical_contest_comparison import slate_blocks,slate_sensitivity
    if position not in POSITIONS or metric not in METRICS or cohort not in COHORTS[1:]:raise ValueError('Invalid controlled comparison.')
    p=_prepared if _prepared is not None else prepare(contests)
    unique=p['valid'];conflicts=p['conflicts'];missing=p['missing']
    included=[];unavailable=p['unavailable'];no_match=0
    for c in unique:
        r=matched(c['controls'],position,metric,cohort,_validated=True)
        if not r:no_match+=1;continue
        included.append(dict(c,matched=r))
    blocks,excluded=slate_blocks(included);used=[c for b in blocks for c in b['contests']]
    fields=[statistics.mean(c['matched']['field'] for c in b['contests']) for b in blocks]
    others=[statistics.mean(c['matched']['comparator'] for c in b['contests']) for b in blocks]
    gaps=[b-a for a,b in zip(fields,others)]
    sums={k:sum(c['matched'][k] for c in used) for k in ('field_rows','cohort_rows','field_total','cohort_total','field_repeats','cohort_repeats')}
    return dict(sums,slates=len(blocks),sources=len(used),field=statistics.mean(fields) if fields else None,
        comparator=statistics.mean(others) if others else None,gap=statistics.mean(gaps) if gaps else None,
        slate_sensitivity=slate_sensitivity(gaps),unavailable=unavailable,no_match=no_match,
        excluded_identity=excluded+missing,conflicts=conflicts)


def report_lines(scope):
    if scope['format']!='showdown' or not scope.get('contests'):return []
    contests=scope['contests'];prepared=prepare(contests);available=bool(prepared['valid'])
    lines=['','Controlled Showdown construction review — accepted unique EntryIds',
        '- Matches the same exact historical Captain ID, total-salary band and Captain-team split within each contest. Field stratum weights are applied to both groups; contest rates are averaged within game/date overlap blocks, then blocks equally. Captain position groups are shown separately.',
        '- Salary bands: below $40,000; $40,000–44,999; $45,000–47,499; $47,500–48,999; $49,000–50,000. Lower salaries remain included. These grouped controls do not establish causality or improved finish probabilities.']
    if not available:
        reasons=sorted({(c.get('controls') or {}).get('reason') or 'Unavailable controlled evidence.' for c in contests})
        return lines+['- Controlled comparisons unavailable: '+'; '.join(reasons),'- Existing field-history indexing prepares receipts once from saved entries/slots. Viewing this report does not scan CSVs or rebuild history; earlier source-row comparisons remain available.']
    accepted=sum(c['controls']['accepted'] for c in prepared['valid'])
    supported=sum(sum(v[0] for v in c['controls']['groups']['field'].values()) for c in prepared['valid'])
    missing_rank=sum(c['controls']['missing_rank'] for c in prepared['valid'])
    bases=Counter(c['controls']['rank_basis'] for c in prepared['valid'])
    lines += [f"- Prepared receipt coverage before date/game/stratum matching: {supported:,}/{accepted:,} supported/accepted entries across {len(prepared['valid'])} source receipts; {missing_rank:,} accepted entries lack a valid rank.",
        '- Rank-cutoff receipt bases: '+', '.join(f'{basis}: {n} sources' for basis,n in sorted(bases.items()))]
    lines += ['| Captain position | Construction | Rank cohort | Field / cohort % | Gap pp | Blocks / sources | Matched / eligible field entries | Matched / eligible cohort entries | Repeated matched entries field / cohort | Lower/equal/higher; omit-one pp | Excluded receipt / no common strata / identity / conflicting source |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|']
    from historical_contest_comparison import sensitivity_text
    for pos in POSITIONS:
        for metric,label in (('opposing_dst','Opposing FLEX DST'),('kd2','K/DST slots ≥2'),('kd3','K/DST slots ≥3')):
            for cohort,title in (('top1','Rank cutoff 1%'),('top5','Rank cutoff 5%')):
                r=comparison(contests,pos,metric,cohort,_prepared=prepared)
                rates=f"{r['field']:.1f} / {r['comparator']:.1f}" if r['slates'] else 'unavailable'
                gap=f"{r['gap']:+.1f}" if r['slates'] else 'unavailable'
                lines.append(f"| {pos} | {label} | {title} | {rates} | {gap} | {r['slates']}/{r['sources']} | {r['field_rows']:,}/{r['field_total']:,} | {r['cohort_rows']:,}/{r['cohort_total']:,} | {r['field_repeats']:,}/{r['cohort_repeats']:,} | {sensitivity_text(r)} | {r['unavailable']}/{r['no_match']}/{r['excluded_identity']}/{r['conflicts']} |")
    drill=[]
    for c in prepared['valid']:
        for ident,cap in sorted(c['controls']['captains'].items()):
            r=matched(c['controls'],cap[2],'opposing_dst','top1',captain_id=ident,_validated=True)
            if r:drill.append((c,ident,cap,r))
    lines += ['',f'Exact Captain detail — rank cutoff 1% ({min(100,len(drill))}/{len(drill)} matched source/Captain groups shown; aggregates above use all groups)',
        '| Source / Captain (historical Captain ID) | Opposing DST gap pp | K/DST ≥2 gap pp | Matched / eligible field | Matched / eligible cohort | Repeated matched entries field / cohort |',
        '|---|---:|---:|---:|---:|---:|']
    for c,ident,cap,r in sorted(drill,key=lambda x:(x[0].get('date') or '',x[0]['name'],x[1]))[:100]:
        other=matched(c['controls'],cap[2],'kd2','top1',captain_id=ident,_validated=True)
        label=f"{c['name']} / {cap[0]} ({cap[1]}; {ident})".replace('|','/').replace('\n',' ').replace('\r',' ')
        lines.append(f"| {label} | {r['comparator']-r['field']:+.1f} | {other['comparator']-other['field']:+.1f} | {r['field_rows']:,}/{r['field_total']:,} | {r['cohort_rows']:,}/{r['cohort_total']:,} | {r['field_repeats']:,}/{r['cohort_repeats']:,} |")
    lines.append('- Captain detail is sorted by recorded date, source name and historical ID, not by apparent success. The display cap does not select an analysis subset; these source-specific rows are not additional independent slates.')
    lines += ['- Supported entries require complete unambiguous historical role/ID metadata, legal two-team structure and salary at most $50,000. Unsupported entries remain outside controlled denominators. Each comparison can have different contributing sources.',
        '- Coverage and repeats use counts summed over contributing sources, not equal-block weights. Missing common strata are excluded, never zero-filled. Repeats are additional accepted entries with the same exact Captain/FLEX lineup in that matched stratum/cohort; they remain valid entries, not independent games.',
        '- Combined K/DST counts include the Captain slot when applicable; opposing FLEX DST excludes the Captain slot. Older source-row summaries can lack their own joint counts even when this separate indexed-entry review is available.',
        '- Rank cutoffs include ties. Receipts use supplied field size where available, otherwise observed accepted-entry counts; neither proves full-field percentiles, wins or payouts. Missing ranks are outside rank cohorts. These denominators differ from the earlier source-row tables, which retain duplicate EntryIds.',
        '- Omit-one ranges remove one whole game/date block and are unavailable below two blocks; they are not confidence intervals. Formats/presets can share games. Individual Captain support and unmeasured game/player differences can still drive results. No forecasts, rules, ranking, ownership, SIM or generation defaults change.']
    return lines
