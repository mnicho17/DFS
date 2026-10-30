"""Read-only winner and earlier-history portfolio reviews; never model inputs."""
from collections import Counter,defaultdict
from contextlib import closing
from datetime import date
import json,math,re,statistics
import analysis_imports as ai
from opponent_history import _connect,VERSION
from opponent_analysis import username_key
from controlled_construction import Builder
from showdown_group_review import flags,FEATURES
from portfolio_pattern_audit import audit_peers,PatternAuditLimit,METRICS as PATTERN_METRICS,LABELS as PATTERN_LABELS

LABELS=dict(player_pool='Players used',captain_pool='Captains used',repeated_pct='Repeated lineups (%)',mean_shared_players='Average shared players between lineups',mean_salary='Average salary ($)',double_qb_pct='Two-quarterback lineups (%)',double_k_pct='Two-kicker lineups (%)',double_dst_pct='Two-defense lineups (%)')

LABELS.update(PATTERN_LABELS)
from pairing_usage import KEYS as USAGE_KEYS,LABELS as USAGE_LABELS
LABELS.update(USAGE_LABELS)

MAX_WINNER_ENTRIES=300000
MAX_HISTORY_RECORDS=1000000
MAX_FOLLOWUP_USERS=100000

class ReviewLimitError(ValueError):
    pass

METRICS=('player_pool','captain_pool','repeated_pct','mean_shared_players','mean_salary','double_qb_pct','double_k_pct','double_dst_pct')+PATTERN_METRICS


def _rate(table,label):
    rows=table or []
    n=sum(r['entries'] for r in rows)
    return 100*sum(r['entries'] for r in rows if label(r['label']))/n if n else None


def _peer(p):
    c=p.get('constructions') or {};n=p.get('entries')
    if type(n) is not int or n<=0 or p.get('readable_rosters')!=n or p.get('ranked_entries')!=n or c.get('known_entries')!=n or c.get('unknown_entries')!=0:return None
    tables=c.get('tables') or {}
    r={k:p.get(k) for k in METRICS};r['mean_salary']=c.get('mean_salary')
    for target,source in (('double_qb_pct','Quarterbacks'),('double_k_pct','Kickers'),('double_dst_pct','Defenses')):
        r[target]=_rate(tables.get(source),lambda label:label.isdigit() and int(label)>=2)
    return {k:float(v) if type(v) in (int,float) and math.isfinite(v) else None for k,v in r.items()}


def _resolve(slots,lookup,kind,builder):
    if kind=='showdown':return slots if builder._resolve(slots) is not None else None
    expected=Counter(QB=1,RB=2,WR=3,TE=1,FLEX=1,DST=1)
    if len(slots)!=9 or Counter(role for _,role,_ in slots)!=expected:return None
    athletes=set();teams=set();salary=0
    for label,role,p in slots:
        matches=lookup.get((role,p['id']),[])
        if len(matches)!=1:return None
        exact=matches[0];explicit=re.search(r'\((\d+)\)\s*$',label)
        if any(p.get(k)!=exact.get(k) for k in ('name','position','team','salary')) or ai._name(label)!=ai._name(exact['name']) or (explicit and explicit[1]!=p['id']):return None
        if type(p['salary']) is not int or p['salary']<0:return None
        athletes.add(ai._name(p['name']));teams.add(p['team']);salary+=p['salary']
    return slots if len(athletes)==9 and len(teams)>=2 and '' not in teams and salary<=50000 else None


def _portfolio(conn,key,user,players,kind,cancelled,*,pairing_usage=False):
    entries=conn.execute('SELECT entry_id,rank,points,metadata_complete FROM opponent_entries WHERE contest_key=? AND user_key=? ORDER BY entry_id',(key,user)).fetchall()
    lookup=defaultdict(list)
    for p in players:
        for role in p['role'].split('/'):lookup[role,p['id']].append(p)
    builder=Builder(players,len(entries)) if kind=='showdown' else None
    usage=[0]*6
    total=Counter();captains=Counter();flex_counts=Counter();display={};shapes=defaultdict(Counter);signatures=Counter();winning={};known=0;salary_sum=0;patterns=Counter();other_patterns=Counter();other_known=0;ranked=0;wins=0
    for start in range(0,len(entries),200):
        ai._check(cancelled);block=entries[start:start+200];ids=[r[0] for r in block];slots=defaultdict(list)
        sql='SELECT entry_id,role,original_label,player_id,player_name,position,team,salary FROM opponent_entry_slots WHERE contest_key=? AND entry_id IN ('+','.join('?' for _ in ids)+') AND ordinal BETWEEN 0 AND 9 ORDER BY entry_id,ordinal'
        for eid,role,label,ident,name,pos,team,salary in conn.execute(sql,[key,*ids]):slots[eid].append((label,role,dict(id=ident,name=name,position=pos,team=team,salary=salary)))
        for eid,rank,points,complete in block:
            ai._check(cancelled);is_rank=type(rank) is int and 0<rank<=2**53-1;ranked+=is_rank;wins+=is_rank and rank==1
            row=slots[eid]
            if complete!=1 or _resolve(row,lookup,kind,builder) is None:continue
            known+=1;cost=sum(p['salary'] for _,_,p in row);salary_sum+=cost
            if pairing_usage:
                from pairing_usage import counts
                for i,n in enumerate(counts(row)):usage[i]+=n
            keys=[];captain=None
            for label,role,p in row:
                athlete=(ai._name(p['name']),p['team'],p['position']);keys.append(athlete);display[athlete]=p['name']
                if role=='CPT':captain=athlete;captains[athlete]+=1
                if role=='FLEX':flex_counts[athlete]+=1
            total.update(keys);signature=(captain,tuple(sorted(k for k in keys if k!=captain))) if kind=='showdown' else tuple(sorted(keys));signatures[signature]+=1
            teams=Counter(p['team'] for _,_,p in row);positions=Counter(p['position'] for _,_,p in row)
            shapes['Team split']['–'.join(map(str,sorted(teams.values(),reverse=True)))]+=1
            for category,pos in (('Quarterbacks','QB'),('Kickers','K'),('Defenses','DST')):shapes[category][str(positions[pos])]+=1
            if captain:
                shapes['Captain position'][captain[2]]+=1
                shapes['Captain team split'][str(teams[captain[1]])+'–'+str(6-teams[captain[1]])]+=1
            if kind=='showdown':
                value=flags(row);patterns.update({k:int(v) for k,v in value.items()})
                if is_rank and rank!=1:other_patterns.update({k:int(v) for k,v in value.items()});other_known+=1
            if is_rank and rank==1:
                if signature not in winning:winning[signature]=dict(copies=0,points=points,salary=cost,slots=[dict(role=role,name=p['name'],team=p['team'],position=p['position']) for _,role,p in row])
                winning[signature]['copies']+=1
    metrics=dict(player_pool=len(total) if known else None,captain_pool=len(captains) if kind=='showdown' and known else None,repeated_pct=100*(known-len(signatures))/known if known else None,
        mean_shared_players=sum(v*(v-1)//2 for v in total.values())/(known*(known-1)//2) if known>1 else None,mean_salary=salary_sum/known if known else None,
        double_qb_pct=_rate([dict(label=k,entries=v) for k,v in shapes['Quarterbacks'].items()],lambda k:int(k)>=2),double_k_pct=_rate([dict(label=k,entries=v) for k,v in shapes['Kickers'].items()],lambda k:int(k)>=2),double_dst_pct=_rate([dict(label=k,entries=v) for k,v in shapes['Defenses'].items()],lambda k:int(k)>=2))
    metrics.update({metric:100*value/known if kind=='showdown' and known else None for metric,value in zip(PATTERN_METRICS,(shapes['Team split'].get('5–1',0),shapes['Team split'].get('4–2',0),shapes['Team split'].get('3–3',0),patterns.get('qb_dst',0),patterns.get('all_four',0)))})
    if pairing_usage:
        from pairing_usage import metrics as usage_metrics
        metrics.update(usage_metrics(usage) if known==len(entries) else dict.fromkeys(USAGE_KEYS))
    return dict(best_rank=min((r[1] for r in entries if type(r[1]) is int and 0<r[1]<=2**53-1),default=None),entries=len(entries),known=known,unknown=len(entries)-known,ranked=ranked,winning_entries=wins,winning_lineups=list(winning.values()),unique_lineups=len(signatures),metrics=metrics,
        exposures=[dict(name=display[k],team=k[1],position=k[2],total=n,captain=captains[k],flex=flex_counts[k],denominator=known) for k,n in sorted(total.items(),key=lambda item:(-item[1],item[0]))],
        constructions={k:dict(sorted(v.items())) for k,v in shapes.items()},patterns=dict(patterns),other_patterns=dict(other_patterns),other_known=other_known)


def review_winners(db_path,through,cancelled=lambda:False,progress=lambda text:None,*,contest_format='showdown',since='',review_mode='winners',followup_from='',pairing_usage=False):
    ai._check(cancelled)
    if pairing_usage and contest_format!='showdown':raise ValueError('Pairing history review requires Showdown.')
    metric_names=METRICS+(USAGE_KEYS if pairing_usage else ())
    through=date.fromisoformat(through).isoformat();since=date.fromisoformat(since).isoformat() if since else '0001-01-01'
    followup=review_mode=='prior_qualified'
    if since>through or contest_format not in ('','showdown','classic') or review_mode not in ('winners','prior_qualified'):raise ValueError('Choose a valid date range, format and review mode.')
    if followup:
        followup_from=date.fromisoformat(followup_from).isoformat()
        if not since<followup_from<=through:raise ValueError('Evaluation must start after the history start date and on or before the through date.')
    results=[];excluded=[];verified={};manifests={};histories=defaultdict(list);used_entries=0;history_rows=0;evidence=[]
    from learning_db import _infer_field_preset
    with closing(_connect(db_path,True)) as conn:
        conn.execute('BEGIN')
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'opponent_contests','opponent_entries','opponent_user_contest_stats','analysis_sources','analysis_salary_pairs'}<=tables:raise ValueError('Index mapped results in Data and Learning first.')
        conn.set_progress_handler(lambda:int(cancelled()),10000)
        sources={s['hash']:s for s in ai._sources(conn)};pairs=dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        rows=conn.execute('SELECT contest_key,result_hash,salary_hash,name,format,start_date,end_date,field_size,complete,audit_json FROM opponent_contests WHERE version=? AND end_date BETWEEN ? AND ? ORDER BY end_date,contest_key',(VERSION,since,through)).fetchall()
        rows=[r for r in rows if not contest_format or r[4]==contest_format]
        if len(rows)>100:raise ValueError('Choose a narrower date range; this review supports up to 100 contests.')
        candidates=set()
        for row in rows:
            ai._check(cancelled)
            if followup and row[6]<followup_from:continue
            sql='SELECT user_key FROM opponent_user_contest_stats WHERE contest_key=?' if followup else 'SELECT DISTINCT user_key FROM opponent_entries WHERE contest_key=? AND rank=1'
            for user, in conn.execute(sql,(row[0],)):
                candidates.add(user)
                if len(candidates)>(MAX_FOLLOWUP_USERS if followup else 3000):raise ReviewLimitError('User review bound reached; narrow the evaluation date range.')
        for index,(key,r,s,name,kind,start,end,size,complete,audit_json) in enumerate(rows):
            ai._check(cancelled);progress(f'Verifying historical portfolios {index+1}/{len(rows)}: {name}')
            try:
                if pairs.get(r)!=s or r not in sources or s not in sources:raise ValueError('Saved source/salary association is unresolved.')
                for digest in (r,s):
                    if digest not in verified:ai._verify(sources[digest],cancelled);verified[digest]=sources[digest]
                if not ai.qualify_pair(sources[r],sources[s])['compatible']:raise ValueError('Saved salary association does not qualify.')
                if s not in manifests:manifests[s]=ai._salary_manifest(sources[s]['snapshot'],cancelled)
                manifest=manifests[s]
                if kind not in ('showdown','classic') or manifest['format']!=kind or start!=end or manifest['dates']!=[end]:raise ValueError('Exact single-date salary slate is unresolved.')
                if kind=='showdown' and len(manifest['games'])!=1:raise ValueError('Showdown game identity is unresolved.')
                audit=json.loads(audit_json);accepted=audit.get('accepted_entries')
                if type(size) is int and (size<=0 or (type(accepted) is int and size<accepted)):raise ValueError('Supplied field size conflicts with indexed entries.')
                if type(accepted) is not int or not 0<accepted<=1000000 or audit.get('conflicting_entry_ids_excluded')!=0:raise ValueError('Entry identity conflicts or invalid indexing receipt.')
                if conn.execute('SELECT COUNT(*) FROM opponent_entries WHERE contest_key=?',(key,)).fetchone()[0]!=accepted:raise ValueError('Indexed entry count differs from its receipt.')
                winners=dict(conn.execute('SELECT user_key,COUNT(*) FROM opponent_entries WHERE contest_key=? AND rank=1 GROUP BY user_key',(key,)))
                peers=defaultdict(list);stats={};prior_stats={};pending_history=[];observed=0
                slate=(kind,end,tuple(sorted(manifest['games'])));preset=_infer_field_preset(name)
                cutoff_size=size if type(size) is int and size>0 else accepted
                evaluate=not followup or end>=followup_from
                for user,n,readable,best,encoded in conn.execute('SELECT user_key,entries,readable,best_rank,stats_json FROM opponent_user_contest_stats WHERE contest_key=? ORDER BY user_key',(key,)):
                    ai._check(cancelled)
                    if type(n) is not int or n<=0:raise ValueError('Entrant count is invalid.')
                    observed+=n
                    if len(encoded)>262144:raise ValueError('Entrant receipt exceeds the review bound.')
                    p=json.loads(encoded)
                    if p.get('entries')!=n or p.get('readable_rosters')!=readable or p.get('best_rank')!=best:raise ValueError('Entrant receipt disagrees with indexed counts.')
                    history_key=(user,kind,preset,n) if followup else user
                    if user in candidates:
                        prior_rank=best if not followup or p.get('ranked_entries')==n else None
                        pending_history.append((history_key,end,slate,prior_rank,cutoff_size))
                    prior=prior_record(histories.get(history_key,()),end,kind) if followup else None
                    selected=(evaluate and preset!='Unclassified' and prior['repeat_observed_top_cohort']) if followup else user in winners
                    if selected:
                        if (not followup and best!=1) or 'dk:'+username_key(p['username'])!=user:raise ValueError('Selected identity or rank receipt disagrees.')
                        stats[user]=p
                        if followup:prior_stats[user]=prior
                    elif evaluate:
                        metric=_peer(p)
                        if metric is not None and (not followup or (type(best) is int and 0<best<=2**53-1 and preset!='Unclassified' and prior['ranked_slates']>=3)):
                            peer_key=(n,prior['ranked_slates']) if followup else n
                            peers[peer_key].append((metric,bool(type(best) is int and 0<best<=(cutoff_size+99)//100),user))
                if observed!=accepted or (not followup and set(stats)!=set(winners)):raise ValueError('Entrant coverage disagrees with the accepted-entry receipt.')
                audit_summary=None
                if kind=='showdown' and stats:
                    needed={(p['entries'],prior_stats[u]['ranked_slates']) if followup else p['entries'] for u,p in stats.items()}
                    wanted={v[2]:(k[0] if followup else k) for k in needed for v in peers[k]}
                    audited=audit_peers(conn,key,manifest['players'],wanted,cancelled,progress,**({'pairing_usage':True} if pairing_usage else {}))
                    audit_summary=dict(users=len(audited),entries=sum(a['entries'] for a in audited.values()),unknown_entries=sum(a['unknown'] for a in audited.values()),excluded_users=sum(a['unknown']>0 for a in audited.values()))
                    for k in needed:
                        checked=[]
                        for metric,top,user in peers[k]:
                            if audited[user]['unknown']:continue
                            metric.update(audited[user]['metrics'])
                            if pairing_usage:metric.update(audited[user]['pairing_usage'])
                            checked.append((metric,top,user))
                        peers[k]=checked
                profiles=[]
                for portfolio_index,user in enumerate(sorted(stats),1):
                    if portfolio_index==1 or portfolio_index%25==0:progress(f"Reviewing {portfolio_index}/{len(stats)} portfolios: {name}; {used_entries:,} entries reviewed across this attempt.")
                    used_entries+=stats[user]['entries']
                    if used_entries>MAX_WINNER_ENTRIES:raise ReviewLimitError('Portfolio entry review bound reached; narrow the evaluation date range.')
                    profile=_portfolio(conn,key,user,manifest['players'],kind,cancelled,**({'pairing_usage':True} if pairing_usage else {}));p=stats[user]
                    if profile['entries']!=p['entries'] or profile['winning_entries']!=winners.get(user,0) or profile['best_rank']!=p['best_rank']:raise ValueError('Selected portfolio coverage changed.')
                    peer_key=(profile['entries'],prior_stats[user]['ranked_slates']) if followup else profile['entries']
                    eligible_peers=peers[peer_key] if profile['known']==profile['entries']==profile['ranked'] else []
                    medians={metric:statistics.median(v[0][metric] for v in eligible_peers if v[0][metric] is not None) if any(v[0][metric] is not None for v in eligible_peers) else None for metric in metric_names}
                    extra=dict(prior_record=prior_stats[user],prior_qualified=True,current_top_cutoff=bool(type(p['best_rank']) is int and 0<p['best_rank']<=(cutoff_size+99)//100) if profile['ranked']==profile['entries'] else None,peer_top_rate=statistics.mean(v[1] for v in eligible_peers) if eligible_peers else None) if followup else {}
                    profiles.append(dict(username=p['username'],user_key=user,peer_users=len(eligible_peers),metric_peer_users={m:sum(v[0][m] is not None for v in eligible_peers) for m in metric_names},peer_medians=medians,**profile,**extra))
                history_rows+=len(pending_history)
                if history_rows>MAX_HISTORY_RECORDS:raise ReviewLimitError('History row bound reached; narrow the history or evaluation range.')
                for user,d,sl,best,n in pending_history:histories[user].append((d,sl,best,n))
                receipt=dict(contest_key=key,name=name,date=end,format=kind,preset=preset,slate=list(slate),accepted=accepted,complete_field=bool(complete),rank_basis='supplied field size' if type(size) is int and size>0 else 'observed accepted-entry count; completeness unverified',source=r,salary_source=s)
                evidence.append(receipt)
                if evaluate:results.append(dict(**receipt,winners=profiles,peer_pattern_audit=audit_summary))
            except (ai.ImportCancelled,ReviewLimitError,PatternAuditLimit):raise
            except Exception as exc:
                ai._check(cancelled);excluded.append(dict(name=name,reason=str(exc)))
        conn.set_progress_handler(None,0)
    with closing(_connect(db_path,True)) as conn:
        current=dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        for c in evidence:
            if current.get(c['source'])!=c['salary_source']:raise ValueError('Salary association changed during review; rerun.')
    for source_index,source in enumerate(verified.values(),1):
        ai._check(cancelled);progress(f"Final source verification {source_index}/{len(verified)}: {source['name']}");ai._verify(source,cancelled)
    if not followup:
        for contest in results:
            for profile in contest['winners']:
                profile['prior_record']=prior_record(histories[profile['user_key']],contest['date'],contest['format'])
    ai._check(cancelled)
    return dict(through=through,since=since,format=contest_format,contests=results,excluded=excluded,review_mode=review_mode,followup_from=followup_from if followup else '',evidence=evidence,**({'pairing_usage':True} if pairing_usage else {}))


def prior_record(records,cutoff,kind):
    prior=set();ranked=set();wins=set();top=set()
    for d,sl,best,n in records:
        if d>=cutoff or sl[0]!=kind:continue
        prior.add(sl)
        if type(best) is int and best>0:
            ranked.add(sl)
            if best==1:wins.add(sl)
            if best<=(n+99)//100:top.add(sl)
    return dict(observed_slates=len(prior),ranked_slates=len(ranked),rank1_slates=len(wins),top_cutoff_slates=len(top),repeat_observed_top_cohort=len(ranked)>=3 and len(top)>=2)


def comparisons(report,minimum_peers=1):
    buckets=defaultdict(list)
    for c in report['contests']:buckets[(c['format'],c['preset'])].append(c)
    output={}
    for (kind,preset),contests in sorted(buckets.items()):
        metrics={}
        for metric in METRICS+(USAGE_KEYS if report.get('pairing_usage') else ()):
            slates=defaultdict(list);pairs=0
            for c in contests:
                values=[(p['metrics'][metric],p['peer_medians'][metric]) for p in c['winners'] if p['metrics'].get(metric) is not None and p['peer_medians'].get(metric) is not None and p.get('metric_peer_users',{}).get(metric,p['peer_users'])>=minimum_peers]
                if values:
                    slates[json.dumps(c['slate'])].append((statistics.mean(v[0] for v in values),statistics.mean(v[1] for v in values)));pairs+=len(values)
            rows=[(statistics.mean(v[0] for v in values),statistics.mean(v[1] for v in values)) for values in slates.values()]
            metrics[metric]=dict(winner=statistics.mean(v[0] for v in rows) if rows else None,peer=statistics.mean(v[1] for v in rows) if rows else None,slates=len(rows),matched_profiles=pairs)
        output[kind+' / '+preset]=dict(contests=len(contests),winner_profiles=sum(len(c['winners']) for c in contests),metrics=metrics)
    return output


def followup_outcomes(report):
    grouped=defaultdict(lambda:defaultdict(list))
    for c in report['contests']:
        values=[(float(p['current_top_cutoff']),p['peer_top_rate']) for p in c['winners'] if p.get('current_top_cutoff') is not None and p.get('peer_top_rate') is not None]
        if values:grouped[c['format']+' / '+c['preset']][json.dumps(c['slate'])].append((statistics.mean(v[0] for v in values),statistics.mean(v[1] for v in values),len(values)))
    output={}
    for scope,slates in grouped.items():
        rows=[(statistics.mean(v[0] for v in values),statistics.mean(v[1] for v in values)) for values in slates.values()]
        output[scope]=dict(cohort_rate=statistics.mean(v[0] for v in rows),peer_rate=statistics.mean(v[1] for v in rows),slates=len(rows),matched_profiles=sum(v[2] for values in slates.values() for v in values))
    return output



def peer_support_check(report,minimum):
    if type(minimum) is not int or minimum<1:raise ValueError('Minimum peer support must be a positive integer.')
    view=dict(report,contests=[dict(c,winners=[p for p in c['winners'] if p['peer_users']>=minimum]) for c in report['contests']])
    return dict(profiles=sum(len(c['winners']) for c in view['contests']),comparisons=comparisons(view,minimum),outcomes=followup_outcomes(view))


def render_overview(report):
    contests=report['contests'];profiles=[p for c in contests for p in c['winners']];followup=report.get('review_mode')=='prior_qualified'
    cohort='earlier-history cohort' if followup else 'winner'
    lines=['EARLIER REPEAT FINISHES — LATER OBSERVED PORTFOLIOS' if followup else 'CONTEST WINNERS — ENTIRE OBSERVED PORTFOLIOS',f"History scope: {report['since']} through {report['through']}; format {report['format'] or 'all'}.",f"Verified evaluation contests: {len(contests)}; {cohort} entrant/contest profiles: {len(profiles)}; distinct usernames: {len({p['user_key'] for p in profiles})}.",
        f"Observed entries in those portfolios: {sum(p['entries'] for p in profiles):,}; resolved: {sum(p['known'] for p in profiles):,}; unknown: {sum(p['unknown'] for p in profiles):,}.",
        f"Complete-field receipts: {sum(c['complete_field'] for c in contests)}/{len(contests)}. All available entries are retained; unseen entries are unknown."]
    audits=[c['peer_pattern_audit'] for c in contests if c.get('peer_pattern_audit') is not None]
    if audits:lines += [f"Peer construction audit: {sum(a['entries'] for a in audits):,} peer entries read; {sum(a['unknown_entries'] for a in audits):,} unresolved rows; {sum(a['excluded_users'] for a in audits):,} peer portfolios withheld in full. Team splits use majority-team first; QB/DST checks include all Captain and FLEX slots."]
    lines += ['Peer medians are calculated separately for each metric; team-split peer columns need not add to 100% and are not a joint field distribution.']
    if report.get('pairing_usage'):
        lines += ['Pairing usage covers complete resolved portfolios. WR/TE Captain with own QB uses only WR/TE Captain entries as its denominator; K/DST counts include Captain and FLEX. These are usage differences, not estimated performance gains.']
    if followup:
        lines += [f"Evaluation starts {report['followup_from']}. For each contest, selection uses only strictly earlier calendar dates: at least 3 ranked salary slates and a top-rank-cutoff finish on at least 2. Format, known entry-limit preset and exact observed entry count must match. Same-slate contests count once; unknown ranks do not qualify.",
            'The cutoff is ceil(supplied field size / 100), otherwise ceil(observed accepted entries / 100); this is not a verified full-field percentile. Every later observed entry is retained regardless of winning. Unclassified entry-limit presets do not qualify.',
            'Peers are noncohort users with the same observed entry count and exact number of earlier ranked slates (at least 3); current winners are not excluded. Missing roster or rank metadata withholds comparisons. Dates describe event order, not proof that CSVs were available before a live contest.']
    else:lines += ['Rank-1 ties are included, with each user counted once per contest. Winning once is not proof of repeatable skill.',
        'Peer medians use nonwinning users with exactly the same observed entry count and complete roster/rank metadata. Winner portfolios with any unresolved roster or missing rank have no peer comparison.']
    lines += ['Aggregate comparisons weight users equally within a contest, contests equally within a salary slate, and slates equally. Classic, Showdown and entry-limit presets stay separate. No ROI or causal inference; no rules or model changes.']
    for scope,data in comparisons(report).items():
        lines+=['',scope+f" — {data['contests']} contests, {data['winner_profiles']} {cohort} profiles"]
        for metric,values in data['metrics'].items():
            if values['winner'] is not None:lines.append(f"{LABELS[metric]}: {cohort} {values['winner']:.2f}; matched-peer median {values['peer']:.2f}; {values['slates']} slates / {values['matched_profiles']} profiles")
    if followup:
        lines += ['','Later rank-cutoff outcomes (descriptive, matched profiles only):']
        outcomes=followup_outcomes(report)
        for scope,v in outcomes.items():lines.append(f"{scope}: cohort {v['cohort_rate']:.2%}; matched peers {v['peer_rate']:.2%}; {v['slates']} slates / {v['matched_profiles']} profiles.")
        if not outcomes:lines.append('No complete exact-match peer comparisons available; outcomes are unknown.')
    if followup:
        lines += ['','Peer support and sensitivity (fixed checks, not automatic cohort/rule changes):',
            'Peer support is metric-specific; new construction comparisons require every peer entry to resolve against the exact historical salary catalog. Peer users can be reused across profiles. Profile counts are not independent games. Matching prior slate counts does not match the exact earlier contests, field strength or entry fees.']
        for c in contests:
            pools=[p['peer_users'] for p in c['winners'] if p['peer_users']]
            lines.append(c['name']+f": {len(pools)}/{len(c['winners'])} profiles matched; median peer pool {statistics.median(pools) if pools else 'unavailable'}.")
        for minimum in (5,10):
            check=peer_support_check(report,minimum)
            lines.append(f"Requiring at least {minimum} peers: {check['profiles']} profiles.")
            for scope,data in check['comparisons'].items():
                for metric in ('player_pool','captain_pool','repeated_pct','double_qb_pct','double_k_pct','double_dst_pct')+PATTERN_METRICS+(USAGE_KEYS if report.get('pairing_usage') else ()):
                    v=data['metrics'][metric]
                    if v['winner'] is not None:lines.append(f"{scope} / {LABELS[metric]}: cohort {v['winner']:.2f}; peer median {v['peer']:.2f}; {v['slates']} slates.")
            for scope,v in check['outcomes'].items():lines.append(f"{scope} later rank-cutoff outcome: cohort {v['cohort_rate']:.2%}; matched peers {v['peer_rate']:.2%}; {v['slates']} slates.")
            if not check['outcomes']:lines.append('Insufficient matching peer support; later outcome comparison unavailable.')
    if followup:
        if report.get('evaluation_windows'):lines += ['', 'Evaluation windows (gaps are not evaluated): '+ '; '.join(a+' through '+b for a,b in report['evaluation_windows'])]
        from historical_pattern_checks import render_checks
        lines += ['',render_checks(report)]
    if report['excluded']:lines+=['','Excluded contests:']+[r['name']+': '+r['reason'] for r in report['excluded']]
    return '\n'.join(lines)


def render_winner(contest,profile):
    n=profile['known'];prior=profile['prior_record'];followup=profile.get('prior_qualified',False)
    lines=[contest['name'],profile['username']+' — entire observed portfolio',f"Entries: {profile['entries']}; resolved: {n}; unknown: {profile['unknown']}; ranked: {profile['ranked']}; rank-1 entries: {profile['winning_entries']}; unique resolved lineups: {profile['unique_lineups']}.",
        'All observed entries are counted, including losing entries. Missing entries in an incomplete export are unknown.',
        f"Earlier observed slates (strictly before {contest['date']}): {prior['observed_slates']} ({prior['ranked_slates']} with ranks); rank-1 slates {prior['rank1_slates']}; top-rank-cutoff slates {prior['top_cutoff_slates']}. This is not proof of skill or profit.",
        'Earlier top-rank cutoff means ceil(field size / 100) when supplied, otherwise ceil(observed accepted entries / 100). Missing prior contests are unknown; this is not a verified field percentile.',
        f"Matched {'noncohort' if followup else 'nonwinner'} peers: {profile['peer_users']} users with exactly {profile['entries']} observed entries and complete construction metadata."]
    lines.append('Current contest rank denominator: '+contest['rank_basis'])
    if followup:
        lines += [f"Selected before this contest: {prior['ranked_slates']} earlier ranked salary slates, {prior['top_cutoff_slates']} top-cutoff slates, same format / {contest['preset']} / exactly {profile['entries']} entries. Peers also match the number of earlier ranked slates.",f"Later observed top-cutoff finish: {profile['current_top_cutoff']}; peer rate: {profile['peer_top_rate'] if profile['peer_top_rate'] is not None else 'unavailable'}."]
    for metric,value in profile['metrics'].items():
        peer=profile['peer_medians'][metric]
        support=profile.get('metric_peer_users',{}).get(metric,profile['peer_users'])
        lines.append(f"{LABELS[metric]}: {format(value,'.2f') if value is not None else 'unavailable'}; peer median: {format(peer,'.2f') if peer is not None else 'unavailable'}; matched peers {support}")
    lines+=['','Construction across the entire resolved portfolio:']
    for category,values in profile['constructions'].items():lines.append(category+': '+'; '.join(f'{k}: {v}/{n}' for k,v in values.items()))
    if contest['format']=='showdown':
        lines.append('Grouping-pattern counts (all / nonwinning resolved entries):')
        for key,value in profile['patterns'].items():lines.append(f"{FEATURES[key]}: {value}/{n}; excluding rank-1 entries {profile['other_patterns'].get(key,0)}/{profile['other_known']}")
    lines+=['','Player exposure — total / Captain / FLEX (denominator: resolved entries):']
    for p in profile['exposures']:lines.append(f"{p['name']} ({p['team']}, {p['position']}): {p['total']}/{n} ({100*p['total']/n:.1f}%) / CPT {p['captain']} / FLEX {p['flex']}")
    lines+=['','Rank-1 lineup(s), including duplicate winning copies:']
    for row in profile['winning_lineups']:lines.append(f"{row['copies']} copies; {row['points']} points; ${row['salary']:,}: "+'; '.join(p['role']+' '+p['name'] for p in row['slots']))
    if not profile['winning_lineups']:lines.append('No resolved rank-1 lineup is available in this observed portfolio.')
    lines+=['','Historical cohort selection and retrospective winner selection have selection bias. Tied first-place entrants appear once per contest. Missing prior contests are unknown; earlier records cover only the selected review date range. No payouts, ROI, causality, forecasts or rule selections are inferred. No model or defaults change.']
    return '\n'.join(lines)
