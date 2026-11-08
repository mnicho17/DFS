"""RL-07B first slice: recorded local checks and exact output membership only."""
from collections import Counter

from hindsight_contract import check, signature, validate_roster

UNRECORDED = ('generation', 'screening', 'independent_validation', 'portfolio_selection')
LIMITATION = ('Final output absence does not identify a rejection stage. Stage membership is shown only from '
              'the original bound audit; absence from its captured bank is not a rejection reason. '
              'Independent validation and portfolio-selection rejection decisions are not recorded per witness. '
              'Snapshot checks cover supported local rules only; portfolio feasibility, submission and predictability are not established.')


def archive_receipt(matches, chosen, pool, kind, cancelled=lambda:False):
    """Use the same freshly captured archive bytes, not names-only signatures."""
    import review_build_evidence as be
    from portfolio_risk_evidence import _archive_rosters
    check(cancelled)
    if not matches or len({be.digest([a[0]['raw'],a[1],a[4]]) for a in matches}) != 1:
        raise ValueError('conflicting_or_missing_archive_occurrences')
    snap,meta,_,_,rows=matches[0]
    if be.digest(snap['raw']) != be.digest(chosen['raw']):
        raise ValueError('archive_snapshot_revision_differs')
    if meta.get('build_status')!='completed':
        raise ValueError('completed_archive_required')
    def stopped():
        check(cancelled)
        return False
    rosters=_archive_rosters(rows,chosen['players'],kind,stopped)
    counts=Counter()
    for roster in rosters:
        check(cancelled)
        valid=validate_roster(roster,pool,kind,require_scores=False)
        counts[tuple(valid['signature'])]+=1
    return dict(status='ready',outputs=len(rosters),distinct=len(counts),
                signatures=[dict(signature=list(k),occurrences=n) for k,n in sorted(counts.items())])


def local_check(roster, pool, kind, rules):
    """All matching supported restrictions, never an inferred production filter."""
    present={(s['key'],s['role']) for s in roster}
    def includes(item):
        key,role=item
        return any(k==key and (role=='ANY' or role==r) for k,r in present)
    issues=[]
    checked=validate_roster(roster,pool,kind,require_scores=False)
    if checked['salary']>rules['salary_cap']:issues.append(dict(code='recorded_salary_cap',keys=[]))
    for key,role in rules['require']:
        if not includes((key,role)):issues.append(dict(code='recorded_requirement_missing',keys=[key],role=role))
    for key,role in rules['exclude']:
        if includes((key,role)):issues.append(dict(code='recorded_exclusion_present',keys=[key],role=role))
    keys={k for k,_ in present}
    for group in rules['groups']:
        overlap=keys.intersection(group['keys'])
        if (group['type']=='at_least_one' and not overlap) or (group['type']=='never_together' and len(overlap)>1):
            issues.append(dict(code='recorded_group_'+group['type'],keys=sorted(group['keys'])))
    return dict(status='conflicts' if issues else 'passes_supported_checks',issues=issues)


def explain(capture, scopes, cancelled=lambda:False):
    evidence=capture.get('build_explanation_evidence') or {}
    archive=evidence.get('archive') or dict(status='not_requested')
    screening=evidence.get('screening') or dict(status='not_recorded')
    stage_counts={}
    if screening['status']=='ready':
        signatures=screening['candidate_signatures']
        for stage,order in screening['stage_orders'].items():
            counts_for_stage=Counter()
            for index in order:
                check(cancelled)
                counts_for_stage[tuple(signatures[index])]+=1
            stage_counts[stage]=counts_for_stage
    local=capture['gates']['snapshot']
    counts={tuple(v['signature']):v['occurrences'] for v in archive.get('signatures',[])}
    targets=[]
    for scope in ('supplied','snapshot'):
        for n,row in enumerate(scopes[scope]['lineups'],1):targets.append((scope,n,row))
    seen=set()
    for row in capture['observed'].get('highest_witnesses',[]):
        key=tuple(row['signature'])
        if key not in seen:targets.append(('observed_highest',len(seen)+1,row));seen.add(key)
    output=[]
    for source,number,row in targets:
        check(cancelled)
        roster=row['roster']
        try:
            verified=validate_roster(roster,capture['pool'],capture['format'],require_scores=False)
            if list(verified['signature'])!=row['signature']:raise ValueError('witness_signature_differs')
        except (ValueError,KeyError,TypeError):
            output.append(dict(source=source,number=number,status='unavailable_witness'));continue
        if local['status']=='ready':
            snapshot=local_check(roster,capture['pool'],capture['format'],capture['restricted']['rules'])
        else:snapshot=dict(status=local['status'],issues=[])
        membership=dict(status=archive['status'],occurrences=None)
        if archive['status']=='ready':
            n=counts.get(tuple(signature(roster,capture['format'])),0)
            membership=dict(status='present' if n else 'absent',occurrences=n)
        stages={stage:dict(status='observed' if counter[tuple(row['signature'])] else 'not_observed',
                           occurrences=counter[tuple(row['signature'])]) for stage,counter in stage_counts.items()}
        pipeline={k:'not_recorded' for k in UNRECORDED}
        if stage_counts:
            pipeline['generation']='recorded_membership_only'
            pipeline['screening']=('not_run' if screening['screening_status']=='not run' else 'recorded_membership_only')
        output.append(dict(source=source,number=number,status='checked',signature=row['signature'],
            snapshot=snapshot,generated_output=membership,recorded_stages=stages,pipeline=pipeline))
    return dict(version=2,targets=output,screening_evidence={k:v for k,v in screening.items() if k not in ('candidate_signatures','stage_orders')},archive={k:v for k,v in archive.items() if k!='signatures'},
                limitation=LIMITATION,scope='returned_witnesses_only; ties may be partial or display limited')


def lines(review, pool=(), private=False):
    if not review:return ['Build explanation unavailable in this older report.']
    rows=review['targets'];local=Counter(r.get('snapshot',{}).get('status','unavailable') for r in rows);counts=Counter(r.get('generated_output',{}).get('status','unavailable') for r in rows)
    result=['Recorded build explanation — returned witnesses only',
        f"Witness examples checked: {len(rows)}; present in selected archive: {counts['present']}; absent: {counts['absent']}; "
        f"membership unavailable/not requested: {len(rows)-counts['present']-counts['absent']}.",
        f"Snapshot checks: {local['passes_supported_checks']} pass supported checks; {local['conflicts']} conflict; {len(rows)-local['passes_supported_checks']-local['conflicts']} unavailable/not requested.",
        'Generated archive: '+review['archive']['status'].replace('_',' ')+'.',review['limitation'],
        'These are returned witness examples, not an exhaustive census of tied optima or observed entries.']
    screening=review.get('screening_evidence') or dict(status='not_recorded')
    result.append('Original screening evidence: '+screening['status'].replace('_',' ')+'.')
    if screening['status']=='ready':
        result.append('Captured coarse scoring: '+screening['screening_status']+'. Membership does not identify rejection causes.')
        from screening_explanation_evidence import STAGES
        for stage in STAGES:
            observed=sum(r.get('recorded_stages',{}).get(stage,{}).get('status')=='observed' for r in rows)
            result.append('Recorded '+stage.replace('_',' ')+': '+str(observed)+' witness example(s) observed.')
    def state(value):
        return {'not_requested':'not requested','unavailable_evidence':'unavailable evidence','unsupported_rules':'unsupported recorded rules','passes_supported_checks':'no conflicts with supported snapshot rules'}.get(value,value.replace('_',' '))
    if review['archive'].get('reason'):result.append('Archive membership could not be validated against the exact qualified snapshot and historical salary identities.')
    if private:
        names={p['key']:p['name'] for p in pool}
        for row in rows:
            label={'supplied':'Supplied-pool optimum','snapshot':'Snapshot-local optimum','observed_highest':'Highest reported entry'}[row['source']]
            result.append(f"{label} witness {row['number']}: {row['status']}")
            if row['status']!='checked':continue
            result.append('Roster: '+' | '.join(('CPT ' if k.startswith('@cpt:') else '')+names.get(k.removeprefix('@cpt:'),'Unknown recorded identity') for k in row['signature']))
            result += ['Snapshot local checks: '+state(row['snapshot']['status'])+'.',
                       'Selected generated output: '+state(row['generated_output']['status'])+
                       ('; occurrences '+str(row['generated_output']['occurrences']) if row['generated_output']['occurrences'] is not None else '')+'.']
            for stage,value in row.get('recorded_stages',{}).items():
                result.append(stage.replace('_',' ').capitalize()+': '+
                    ('recorded '+str(value['occurrences'])+' occurrence(s)' if value['status']=='observed'
                     else 'not observed in captured bank; not rejection evidence')+'.')
            for issue in row['snapshot']['issues']:
                label={'recorded_salary_cap':'Exceeds recorded salary cap','recorded_requirement_missing':'Missing recorded required player','recorded_exclusion_present':'Contains recorded excluded player','recorded_group_at_least_one':'Missing every player from a recorded required group','recorded_group_never_together':'Contains players from a recorded never-together group'}[issue['code']]
                result.append(label+(' ('+issue['role']+')' if issue.get('role') else '')+': '+
                              ', '.join(names.get(k,'Unknown recorded identity') for k in issue['keys']))
    return result
