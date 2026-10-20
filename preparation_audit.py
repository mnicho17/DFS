"""Pure presentation of recorded timings; no artifact qualification or source reads."""
import math

LABELS = dict(parse='CSV parsing', nfl_context='NFL live context', injuries='Injuries',
              mlb_context='MLB context', preparation='Source preparation', gui_commit='Display commit',
              wait='Background wait', total='CSV load total', ownership='Ownership',
              candidate_search='Candidate search (accumulated)')

def timing_text(timings):
    lines=[]
    for key,value in (timings or {}).items():
        if type(value) in (int,float) and math.isfinite(value) and value>=0:
            lines.append(f"{LABELS.get(key,key)}: {value:.2f} s")
    return '\n'.join(lines) or 'Timings not recorded for this work.'

def ownership_text(report):
    if not report:return 'No recorded ownership reuse decision. Load a CSV or recalculate ownership.'
    state='Reused' if report.get('status')=='reused' else 'Refreshed' if report.get('status')=='refreshed' else 'Not applied'
    return state+': '+str(report.get('reason') or 'Reason not recorded.')+'\n'+timing_text({'ownership':report.get('seconds')})
