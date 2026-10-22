"""Pure presentation of recorded timings; no artifact qualification or source reads."""
import math

LABELS = dict(parse='CSV parsing', nfl_context='NFL live context', injuries='Injuries',
              mlb_context='MLB context', preparation='Source preparation', gui_commit='Display commit',
              wait='Background wait', total='CSV load total', ownership='Ownership',
              candidate_search='Candidate search (accumulated)')

def timing_text(timings):
    lines=[]
    for key,value in sorted((timings or {}).items()):
        if type(value) in (int,float) and math.isfinite(value) and value>=0:
            lines.append(f"{LABELS.get(key,key)}: {value:.2f} s")
    return '\n'.join(lines) or 'Timings not recorded for this work.'

def ownership_text(report):
    if not report:return 'No recorded ownership reuse decision. Load a CSV or recalculate ownership.'
    state='Reused' if report.get('status')=='reused' else 'Refreshed' if report.get('status')=='refreshed' else 'Not applied'
    return state+': '+str(report.get('reason') or 'Reason not recorded.')+'\n'+timing_text({'ownership':report.get('seconds')})


def clean_build_audit(value):
    """Bounded, path-free recorded metadata; never reuse qualification."""
    if not isinstance(value,dict) or not value:return {}
    timings=value.get('csv_timings')
    timings=timings if isinstance(timings,dict) else {}
    clean={key:val for key,val in timings.items() if key in LABELS and
           type(val) in (int,float) and math.isfinite(val) and val>=0}
    report=value.get('ownership')
    report=report if isinstance(report,dict) else {}
    ownership={}
    if report.get('status') in ('reused','refreshed'):
        ownership['status']=report['status']
        # These reasons are generated locally by ownership_cache, never source errors.
        reason=str(report.get('reason') or '')
        if reason and not any(token in reason for token in ('\\','/',':')):
            ownership['reason']=reason[:500]
        duration=report.get('seconds')
        if type(duration) in (int,float) and math.isfinite(duration) and duration>=0:
            ownership['seconds']=duration
    return dict(captured_at=str(value.get('captured_at') or '')[:64],
                snapshot_replay=bool(value.get('snapshot_replay')),csv_timings=clean,ownership=ownership,
                automatic_compute=clean_compute(value.get('automatic_compute')))


def clean_compute(value):
    if not isinstance(value, dict):return {}
    from automatic_compute import select_profile
    try:
        expected=select_profile(value.get('requested'), value.get('cap'), value.get('prepared') is True)
    except ValueError:return {}
    return expected if value == expected else {}


def capture_build_audit(window, automatic_compute=None):
    from datetime import datetime
    replay=bool(getattr(window,'_snapshot_replay',False))
    return clean_build_audit(dict(captured_at=datetime.now().astimezone().isoformat(),
        snapshot_replay=replay,automatic_compute=automatic_compute,
        csv_timings=None if replay else getattr(window,'last_csv_load_timing',None),
        ownership=None if replay else getattr(window,'last_ownership_reuse',None)))


def build_audit_lines(value):
    audit=clean_build_audit(value)
    if not audit:return ['Preparation reuse and timings: not recorded for this build.']
    lines=['Preparation reuse and timings (captured at build start)',
           'Captured: '+(audit['captured_at'] or 'not recorded')]
    if audit['snapshot_replay']:
        return lines+['Snapshot replay: current-session CSV/ownership timings were not attributed to this build.']
    from automatic_compute import summary
    if audit['automatic_compute']:lines.append(summary(audit['automatic_compute']))
    ownership=ownership_text(audit['ownership']) if audit['ownership'] else 'Ownership reuse decision not recorded for this build.'
    lines+=['Last successful CSV load:',timing_text(audit['csv_timings']),
            'Last applied ownership decision:',ownership,
            'Recorded prior work; later edits are validated separately. Source subphases overlap source preparation; ownership runs separately.']
    return lines
