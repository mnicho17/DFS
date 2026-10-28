"""Read-only inventory of frozen inputs; never historical qualification or a trial."""
from collections import Counter
from pathlib import Path
import math
import review_build_evidence as be
from portfolio_risk_evidence import CaptureReader,_archive_rosters
from showdown_common_groups import catalog,selection


def inspect_archives(root,cancelled=lambda:False,progress=lambda text:None,*,preset=''):
    reader=CaptureReader(cancelled);rows=[];excluded=[];skipped=Counter()
    paths=reader.paths(Path(root)/'build-archives','.zip')
    if reader.issues:raise ValueError('Build archive inventory is incomplete; no readiness report published.')
    for i,path in enumerate(paths,1):
        reader.check();progress(f'Checking frozen build inputs {i}/{len(paths)}: {path.name}')
        try:
            snap,meta,sigs,cpts=reader.archive(path)
            if snap['kind']!='showdown':skipped['other_format']+=1;continue
            if preset and snap['recipe'].get('nfl_field_preset')!=preset:skipped['other_or_unknown_preset']+=1;continue
            detail=reader.archives[be.archive_identity(meta,sigs)][-1]
            players=snap['players'];reasons=[]
            if not snap['pregame']:reasons.append('Input timestamp is missing, ambiguous or not before kickoff.')
            created=be.timestamp(meta.get('created_at'))
            if not created or not snap['earliest'] or not snap['created'] or not snap['created']<=created<snap['earliest']:
                reasons.append('Generated output is not recorded between input capture and kickoff.')
            if meta.get('build_status')!='completed':reasons.append('Build was not recorded as completed.')
            catalog(players);_archive_rosters(detail[4],players,'showdown',cancelled)
            pregame_completed=not reasons;known=missing_excluded=0
            for p in players:
                values=[p.get(k) for k in ('FlexProjection','CptProjection')]
                if p.get('ProjectionSource')=='Missing forecast' or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                    missing_excluded+=int(p.get('NFLQBEligible') is False or p.get('FadeFlex') is True or p.get('FadeCpt') is True)
                else:known+=1
            if known!=len(players):reasons.append('Full-pool frozen role forecasts are incomplete; missing values are not imputed or filtered away.')
            groups=snap['raw']['inputs']['rules'].get('groups',[])
            if not isinstance(groups,list) or any(not isinstance(g,dict) for g in groups):raise ValueError('Invalid frozen grouping rules.')
            selected=sorted(selection(players,groups))
            rows.append(dict(archive=path.name,input_id=snap['raw']['input_id'],date=snap['day'],preset=snap['recipe'].get('nfl_field_preset') or 'Unknown',
                input_recorded_at=snap['raw']['created_at'],build_recorded_at=meta.get('created_at'),outputs=meta['output_count'],
                recorded_common_groups=selected,pregame_completed=pregame_completed,forecast_coverage=dict(players=len(players),known_players=known,missing_players=len(players)-known,missing_with_recorded_exclusions=missing_excluded),frozen_inputs_available=not reasons,input_issues=reasons,
                paired_replay_ready=False,replay_blockers=['This archive format does not verify a shared candidate universe and paired scenario/seed provenance.',
                    'Contest/salary/outcome associations still require fresh historical qualification.'],
                implementation_recorded=bool(be.HASH.fullmatch(str(meta.get('app_code_id',''))))))
        except (be.Cancelled,OverflowError):raise
        except (ValueError,TypeError,KeyError,OSError) as exc:excluded.append(dict(archive=path.name,reason=str(exc)))
    reader.check();reader.revalidate()
    if reader.issues:raise ValueError('Build archive inventory changed or exceeded its bounds; rerun.')
    reader.check()
    return dict(rows=rows,excluded=excluded,skipped=dict(skipped),scanned=len(paths),
        pregame_completed=sum(r['pregame_completed'] for r in rows),frozen_inputs_available=sum(r['frozen_inputs_available'] for r in rows),paired_replay_ready=0,
        read_bytes=reader.bytes,qualification='Inventory only; not a historical qualification, submission receipt or generation backtest.')


def render(report):
    lines=['SAVED INPUTS FOR A GROUPING COMPARISON',report['qualification'],
        f"Scanned {report['scanned']} archives; {len(report['rows'])} selected Showdown builds; {len(report['excluded'])} unreadable/invalid; skipped {report['skipped']}.",
        f"Pre-game completed archives: {report['pregame_completed']}; full-pool frozen inputs available: {report['frozen_inputs_available']}; verified paired replays: {report['paired_replay_ready']}.",
        'Saved outputs alone do not establish a shared candidate pool, identical scenario draws or a fair with/without comparison.',
        'No trial is run, outcomes are not used to reconstruct forecasts, and no rule or setting is changed.']
    for r in report['rows']:
        lines+=['',f"{r['date'] or 'Unknown date'} / {r['preset']} / {r['archive']}",
            f"  Frozen inputs: {'available' if r['frozen_inputs_available'] else 'unavailable'}; output lineups {r['outputs']}; recorded common groups {', '.join(r['recorded_common_groups']) or 'none detected'}."]
        coverage=r['forecast_coverage'];lines.append(f"  Recorded forecasts: {coverage['known_players']}/{coverage['players']} players; missing {coverage['missing_players']} (recorded fade/ineligibility flag on {coverage['missing_with_recorded_exclusions']}). The full pool is preserved.")
        lines+=['  '+v for v in r['input_issues']+r['replay_blockers']]
    lines+=['Excluded '+r['archive']+': '+r['reason'] for r in report['excluded']]
    return '\n'.join(lines)
