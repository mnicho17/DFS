"""Post-ranking evaluation of immutable original Showdown results; never training."""
from collections import Counter
from bisect import bisect_left, bisect_right
from decimal import Decimal
from pathlib import Path
import statistics
import analysis_imports as ai
import historical_identity as hi
import hindsight_evidence as he
from hindsight_contract import decimal
from optimizers import _pkey
from ownership_strategy import exposures


def _rows(source,cancelled):
    with Path(source['snapshot']).open(encoding='utf-8-sig',newline='') as handle:
        reader=ai._reader(handle);header=ai._header(next(reader,[]))
        for index,cells in enumerate(reader):
            ai._check(cancelled)
            if index>=hi.MAX_RESULTS:raise ValueError('Outcome CSV row limit exceeded; no partial evaluation.')
            yield index+2,dict(zip(header,cells))


def _scores(source,manifest,cancelled):
    universe=he._universe(manifest)
    values,_,errors,_=he._score_observations(_rows(source,cancelled),universe,'showdown',cancelled)
    if errors:raise ValueError('Exact actual-score evidence invalid: '+', '.join(sorted(errors)))
    base={};observations={}
    for p in universe:
        b=values.get((p['key'],'BASE'),set());c=values.get((p['key'],'CPT'),set())
        if len(b)>1 or len(c)>1 or any(cv!=bv*Decimal('1.5') for cv in c for bv in b):
            raise ValueError('Conflicting exact player/role actual scores.')
        key=(ai._name(p['name']),p['team'],p['position'],p['game'])
        if len(b)==1:base[key]=next(iter(b));observations[key]=next(iter(b))
        elif len(c)==1:observations[key]=next(iter(c))/Decimal('1.5')
    return universe,base,observations


def capture_outcomes(source,manifest,related,cancelled=lambda:False):
    """Require selected-source scores; other exports detect conflicts, never fill gaps."""
    ai._verify(source,cancelled);universe,base,observations=_scores(source,manifest,cancelled)
    for other,other_manifest in related:
        if other['hash']==source['hash']:continue
        ai._verify(other,cancelled);_,_,comparison=_scores(other,other_manifest,cancelled)
        if any(observations[k]!=v for k,v in comparison.items() if k in observations):
            raise ValueError('Cross-contest exact actual-score conflict; outcome evaluation withheld.')
        observations.update(comparison)
        ai._verify(other,cancelled)
    entries={};conflicts=set();sizes=set();invalid_size=False
    for _,row in _rows(source,cancelled):
        if not (row.get('entryid') or row.get('entryname')):continue
        ident=row.get('entryid','').strip()
        if not ident:raise ValueError('Unidentified outcome entry; coverage cannot be established.')
        raw=row.get('points',row.get('actualpoints',''));text=row.get('lineup',row.get('roster',''))
        try:value=decimal(raw)
        except ValueError:value=None
        receipt=(row.get('entryname',''),row.get('rank',''),value if value is not None else raw,he._entry_roster_receipt(text,'showdown'))
        for key in ('fieldsize','contestentries','entries'):
            if row.get(key):
                try:
                    n=int(row[key]);assert n>0 and str(n)==row[key].strip();sizes.add(n)
                except (ValueError,AssertionError):invalid_size=True
        if ident in entries:
            if entries[ident][0]!=receipt:conflicts.add(ident)
            continue
        try:witness=he._roster(text,universe,'showdown')
        except ValueError:witness=None
        entries[ident]=(receipt,value,witness)
    points=[];counts=Counter();duplicates=Counter();valid=0;mismatches=0
    by_key={p['key']:p for p in universe}
    for ident,(_,value,witness) in entries.items():
        ai._check(cancelled)
        if ident in conflicts:continue
        if value is not None:points.append(value)
        if witness is None:continue
        slots=witness['roster'];valid+=1
        signature=tuple(sorted((s['role'],by_key[s['key']]['roles'][s['role']]['id']) for s in slots))
        duplicates[signature]+=1
        counts.update(signature)
        # Compare exact reconstructed totals wherever coverage permits. A bad
        # total blocks outcome scoring rather than calibrating to bad results.
        total=Decimal(0);known=True
        for slot in slots:
            p=by_key[slot['key']];score=base.get((ai._name(p['name']),p['team'],p['position'],p['game']))
            if score is None:known=False;break
            total+=score*(Decimal('1.5') if slot['role']=='CPT' else 1)
        if known and value is not None and total!=value:mismatches+=1
    if mismatches:raise ValueError('Reported entry totals disagree with exact reconstructed scores.')
    accepted=len(entries)-len(conflicts)
    complete=(len(sizes)==1 and next(iter(sizes))==accepted and not invalid_size and not conflicts and valid==accepted)
    ai._verify(source,cancelled)
    return dict(base=base,universe=universe,points=sorted(points),counts=counts,duplicates=duplicates,
                accepted=accepted,valid_rosters=valid,conflicting_entries=len(conflicts),complete=complete,
                score_basis='selected original player-result table; cross-export conflicts checked',
                source_hash=source['hash'])


def _signature(lineup):
    return tuple(sorted([('CPT',str(lineup['Captain']['CptID']))]+[('FLEX',str(p['FlexID'])) for p in lineup['Flex']]))


def ownership_accuracy(evidence,pool,fields):
    n=evidence['valid_rosters']
    if not n:return dict(status='unavailable',reason='No validated observed rosters.')
    supplied={str(r['id']) for p in evidence['universe'] for r in p['roles'].values()}
    if any(str(p[key]) not in supplied for p in pool for key in ('CptID','FlexID')):
        raise ValueError('Forecast ownership identity is outside the exact salary universe.')
    rows=[];sampled={name:exposures(field,True) for name,field in fields.items()}
    for p in pool:
        for slot,role,idkey,forecastkey in (('Captain','CPT','CptID','ProjCptOwnPct'),('FLEX','FLEX','FlexID','ProjFlexOwnPct')):
            observed=100*evidence['counts'].get((role,str(p[idkey])),0)/n
            rows.append(dict(player=p['Name'],position=p['Position'],slot=slot,player_id=str(p[idkey]),forecast=p[forecastkey],observed=observed,
                sampled={name:values.get((slot,_pkey(p)),0) for name,values in sampled.items()}))
    summaries={}
    for name in ['forecast']+list(fields):
        summaries[name]={}
        for slot in ('Captain','FLEX'):
            errors=[(r['forecast'] if name=='forecast' else r['sampled'][name])-r['observed'] for r in rows if r['slot']==slot]
            summaries[name][slot]=dict(mae_pp=statistics.mean(map(abs,errors)),bias_pp=statistics.mean(errors),players=len(errors))
    return dict(status='complete',basis='certified complete supplied field' if evidence['complete'] else 'validated readable subset only; full-field accuracy unavailable',
        denominator=n,accepted_entries=evidence['accepted'],unknown_rosters=evidence['accepted']-n,
        full_field_verified=evidence['complete'],summaries=summaries,rows=rows,
        note='Zeros apply only to validated subset appearances. Players outside the frozen active pool are omitted from forecast errors; listed CSV percentages are not used.')


def evaluate_candidates(evidence,bank,ordered):
    scores={};missing=set();game=evidence['universe'][0]['game']
    for lineup in bank:
        total=Decimal(0)
        for p,multiplier in [(lineup['Captain'],Decimal('1.5'))]+[(p,Decimal(1)) for p in lineup['Flex']]:
            value=evidence['base'].get((ai._name(p['Name']),p['Team'],p['Position'],game))
            if value is None:missing.add(p['Name']);break
            total+=value*multiplier
        else:scores[_signature(lineup)]=total
    if missing:return dict(status='unavailable',reason='Missing selected-source actual player scores; no zeros or cross-export filling.',missing_players=sorted(missing),scored_candidates=len(scores),candidates=len(bank))
    points=evidence['points'];models={}
    if not points:return dict(status='unavailable',reason='No exact supplied entry scores.')
    for name,lineups in ordered.items():
        selected=lineups[:min(20,len(bank))];rows=[]
        for lineup in selected:
            signature=_signature(lineup);value=scores[signature]
            higher=len(points)-bisect_right(points,value);equal=bisect_right(points,value)-bisect_left(points,value)
            rows.append(dict(signature=signature,points=str(value),supplied_rank_if_added=higher+1,
                supplied_equal_scores=equal,supplied_entries_beaten_pct=100*bisect_left(points,value)/len(points),
                observed_duplicates_lower_bound=evidence['duplicates'].get(signature,0)))
        models[name]=dict(top_n=len(rows),mean_points=statistics.mean(float(r['points']) for r in rows),
            mean_supplied_entries_beaten_pct=statistics.mean(r['supplied_entries_beaten_pct'] for r in rows),
            mean_observed_duplicates_lower_bound=statistics.mean(r['observed_duplicates_lower_bound'] for r in rows),rows=rows)
    return dict(status='complete',scored_candidates=len(scores),supplied_scored_entries=len(points),accepted_entries=evidence['accepted'],models=models,
        payout_status='unavailable: no verified payout schedule or hypothetical-entry fee; no ROI inferred',
        note='Frozen diagnostic bank ranked before outcome evaluation. Supplied-entry comparisons are not official ranks; ties are explicit. Duplicates are observed lower bounds. Candidate entries are evaluated independently, not as an inserted portfolio.')
