"""Bounded source-header availability; never cash qualification or returns."""
import csv,io
import analysis_imports as ai

MAX_HEADER_CHARS=65536
COLUMNS={'entry_fee':('entryfee','buyin'),'winnings':('winnings','actualwinnings'),
    'currency':('currency',),'entry_fee_currency':('entryfeecurrency',),
    'winnings_currency':('winningscurrency',),'prize_type':('prizetype',)}
LABELS={'entry_fee':'Entry fee','winnings':'Actual winnings','currency':'Explicit currency'}


def inspect_source(path,cancelled=lambda:False):
    """Caller verifies the original source at both review boundaries."""
    ai._check(cancelled)
    try:
        with open(path,newline='',encoding='utf-8-sig') as handle:text=handle.read(MAX_HEADER_CHARS+1)
        ai._check(cancelled)
        stream=io.StringIO(text,newline='');dialect=ai._reader(stream).dialect;stream.seek(0)
        cells=next(csv.reader(stream,dialect,strict=True),[])
        if not cells or stream.tell()>MAX_HEADER_CHARS:raise ValueError('Header bound reached.')
        keys=ai._header(cells)
        columns={name:[k for k in aliases if k in keys] for name,aliases in COLUMNS.items()}
        missing=[k for k in ('entry_fee','winnings') if not columns[k]]
        if not columns['currency'] and not (columns['entry_fee_currency'] and columns['winnings_currency']):missing.append('currency')
        ambiguous=[k for k in ('entry_fee','winnings') if len(columns[k])>1]
        state='ambiguous_columns' if ambiguous else 'missing_columns' if missing else 'columns_present_values_unverified'
        result=dict(version=1,state=state,columns=columns,missing=missing,ambiguous=ambiguous)
    except (OSError,UnicodeError,csv.Error,ValueError):
        result=dict(version=1,state='unavailable',reason='Source header could not be inspected within the bounded read.',columns={},missing=[],ambiguous=[])
    ai._check(cancelled)
    return result


def render(capture):
    if capture is None:return '  Payout evidence: unavailable in this older capture; rerun the verified review to inspect the source header.'
    state=capture['state']
    if state=='unavailable':return '  Payout evidence: unavailable. '+capture['reason']
    parts=[]
    if capture['missing']:parts.append('missing columns: '+', '.join(LABELS[k] for k in capture['missing']))
    if capture['ambiguous']:parts.append('ambiguous columns: '+', '.join(LABELS[k] for k in capture['ambiguous']))
    if not parts:parts.append('recognized fee/winnings/currency columns present; VALUES UNVERIFIED')
    return '  Payout evidence: '+('; '.join(parts))+'. No cash totals or ROI calculated.'
