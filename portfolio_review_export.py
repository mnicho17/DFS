"""Atomic text publication of completed portfolio reviews; never an import cache."""
from pathlib import Path
from PyQt5 import QtCore

MAX_TEXT_BYTES=8*1024*1024


def snapshot_text(overview,displayed,captured,receipts=()):
    if not overview:raise ValueError('Complete a review before saving.')
    text='HISTORICAL PORTFOLIO REVIEW - SAVED SNAPSHOT\nCaptured: '+captured+'\nSource checks and coverage are those recorded in this completed review; this export does not recheck current files.\nContains the completed overview and selected view, not every entrant detail. This text is not an app input.\n\nCOMPLETED OVERVIEW\n'+overview
    text+='\n\nCAPTURED SOURCE RECEIPTS\n'
    text+='\n'.join(' | '.join(row) for row in receipts) if receipts else 'Source receipts unavailable in this review.'
    if displayed and displayed!=overview:text+='\n\nSELECTED VIEW AT SAVE\n'+displayed
    return text+'\n'


def save_text(path,text,db_path):
    candidate=Path(path)
    if ':' in candidate.name:raise ValueError('Choose a regular .txt file.')
    target=candidate.resolve()
    history=Path(db_path).resolve().parent
    checkout=Path(__file__).resolve().parent
    if target.suffix.lower()!='.txt' or target.is_relative_to(history) or target.is_relative_to(checkout):
        raise ValueError('Choose a .txt file outside the history and application folders.')
    data=text.encode('utf-8')
    if len(data)>MAX_TEXT_BYTES:raise ValueError('This text export is too large; select a smaller review range.')
    output=QtCore.QSaveFile(str(target));output.setDirectWriteFallback(False)
    if not output.open(QtCore.QIODevice.WriteOnly):raise OSError(output.errorString())
    try:
        if output.write(data)!=len(data):raise OSError(output.errorString())
        if not output.commit():raise OSError(output.errorString())
    except BaseException:
        output.cancelWriting();raise
    return str(target)
