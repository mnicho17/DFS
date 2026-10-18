"""Recorded preparation progress and pure comparisons; never authorizes reuse."""
import copy
import json
import math
import time
import uuid
from PyQt5 import QtCore, QtWidgets
from build_snapshots import fingerprint

SETTING='preparation/readiness_records_v1'
LIMIT=24
COUNTS=('checked','saved','structural_total','completed_partitions','partition_count',
        'per_captain','screened','screening_total','screening_reused','batch_candidates','batch_scenarios','batch_scenarios_total')
FLAGS=('complete','screening_complete')
TEXT=('pause_reason','screening_phase','preparation_check','history_verification','phase')


def signature(snapshot):
    from slate_preparation import screening_request
    from showdown_library import _structure
    from showdown_screening import scoring_players
    from showdown_simulation import active_showdown_players
    from portfolio_rules import player_key
    inputs=snapshot['inputs']
    if inputs['recipe'].get('sport','NFL')!='NFL' or inputs['recipe'].get('contest_kind')!='showdown':
        raise ValueError('Preparation summary currently supports NFL Showdown.')
    structure=_structure(inputs['players'],inputs['recipe'].get('salary_cap',50000))
    players,request=screening_request(snapshot)
    active=active_showdown_players(copy.deepcopy(players));locked={player_key(p) for p in active if p.get('LockCpt')}
    captains=sorted(player_key(p) for p in active if not p.get('FadeCpt') and not p.get('LockFlex') and (not locked or player_key(p) in locked))
    rules=copy.deepcopy(request['rules']);rules.pop('max_zero_qb_pct',None)
    context=dict(request,players=scoring_players(players),rules=rules)
    return dict(game=fingerprint(dict(game=structure['players'][0]['game'],teams=sorted({p['team'] for p in structure['players']}))),
        label=structure['players'][0]['game'],players=fingerprint(structure['players']),
        screening=fingerprint(context),cap=structure['salary_cap'],captains=captains,
        all_captains=structure['captains'],combinations=math.comb(len(structure['players'])-1,5))


def current_snapshot(window):
    """Read widgets and in-memory profiles only; no DB, source validation or scans."""
    calibration=copy.deepcopy(getattr(window,'_snapshot_calibration_data',{})) if getattr(window,'_snapshot_replay',False) else {}
    if window.chk_showdown_history.isChecked():
        calibration=dict(showdown_field_profile=(calibration.get('showdown_field_profile') if getattr(window,'_snapshot_replay',False)
            else copy.deepcopy(getattr(window,'showdown_field_profile',None))))
        if not calibration['showdown_field_profile']:raise ValueError('Historical field profile is missing.')
    else:calibration.pop('showdown_field_profile',None)
    return dict(inputs=dict(players=window.players,recipe=window._current_build_recipe(),rules=window._portfolio_rules(),calibration=calibration))


class ReadinessRecords(QtCore.QObject):
    changed=QtCore.pyqtSignal()

    def __init__(self,settings,parent=None,clock=time.monotonic):
        super().__init__(parent);self.settings,self.clock=settings,clock
        self.records=[];self.active=set();self.last_saved=clock()
        try:
            raw=str(settings.value(SETTING,'[]') or '[]')
            if len(raw)>256*1024:raise ValueError('Oversized progress metadata')
            parsed=json.loads(raw)
            if not isinstance(parsed,list):raise ValueError('Invalid progress metadata')
            for record in parsed[-LIMIT:]:
                if not isinstance(record,dict) or not all(isinstance(record.get(k),str) for k in ('id','game','players','screening','label')):continue
                if type(record.get('cap')) not in (int,float) or not math.isfinite(record['cap']) or record['cap']<=0:continue
                if not isinstance(record.get('captains'),list) or not all(isinstance(k,str) for k in record['captains']):continue
                clean={k:record[k] for k in ('id','game','players','screening','label','cap','captains')}
                clean.update(path=str(record.get('path',''))[:4096],state=record.get('state') if record.get('state') in ('Paused','Needs attention') else 'Recorded',mode=str(record.get('mode','bounded')),
                    updated=str(record.get('updated','')),reason='Previous session; no job was resumed. Use Prepare this slate to recheck files and resume. Last outcome: '+str(record.get('reason','Not recorded.'))[:1000])
                clean.update(self.progress_fields(record))
                self.records.append(clean)
        except (ValueError,TypeError):self.records=[]

    @staticmethod
    def progress_fields(data):
        clean={}
        for key in COUNTS:
            value=data.get(key)
            if type(value) is int and 0<=value<2**63:clean[key]=value
            elif key in data and value is None:clean[key]=None
        for key in FLAGS:
            if type(data.get(key)) is bool:clean[key]=data[key]
        for key in TEXT:
            if isinstance(data.get(key),str):clean[key]=data[key][:2000]
        return clean

    def save(self):
        retired=[r for r in self.records if r['id'] not in self.active]
        keep={r['id'] for r in retired[-LIMIT:]}|self.active
        self.records=[r for r in self.records if r['id'] in keep]
        self.settings.setValue(SETTING,json.dumps(self.records,separators=(',',':'),allow_nan=False))
        self.last_saved=self.clock()

    def get(self,key):return next((r for r in self.records if r['id']==key),None)

    def begin(self,snapshot,path='',scope=None,screen=True,full_screen=False):
        try:sig=signature(snapshot)
        except (ValueError,TypeError,KeyError):return None
        full=str(path).endswith('.sdfull') or not path
        covered=sig['all_captains'] if full or scope is None else list(scope)
        record=dict(sig,per_captain=sig['combinations'],id=uuid.uuid4().hex,path=str(path),captains=covered,
            structural_total=len(covered)*sig['combinations'],state='Queued',mode='full' if full_screen else 'bounded' if screen else 'off',
            reason='Waiting for the preparation worker.',updated=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.ISODate))
        record.pop('all_captains');record.pop('combinations')
        self.records.append(record);self.active.add(record['id']);self.save();self.changed.emit()
        return record['id']

    def state(self,key,state,reason):
        record=self.get(key)
        if record is None or key not in self.active:return
        record.update(state=state,reason=str(reason)[:2000])
        if state in ('Running','Queued'):record.pop('pause_reason',None)
        self.changed.emit()

    def update(self,key,data):
        record=self.get(key)
        if record is None or key not in self.active:return
        record.update(self.progress_fields(data))
        if 'screened' in data:record['phase']='Screening'
        elif 'checked' in data:record['phase']='Roster preparation'
        elif 'preparation_check' in data:record['phase']=str(data['preparation_check'])[:2000]
        elif 'history_verification' in data:record['phase']='Historical source validation'
        if data.get('screening_complete'):
            record['phase']='Screening complete'
            for key in ('screening_phase','batch_candidates','batch_scenarios','batch_scenarios_total'):record.pop(key,None)
        if data.get('screening_reused'):
            record.update(screened=data['screening_reused'],screening_total=data['screening_reused'],screening_complete=True,mode='bank')
        record['updated']=QtCore.QDateTime.currentDateTimeUtc().toString(QtCore.Qt.ISODate)
        if self.clock()-self.last_saved>=5:self.save()
        self.changed.emit()

    def planned(self,key,plan):
        record=self.get(key)
        if record is None:return
        record['path']=str(plan['path'])
        if plan.get('scope') is not None:
            record['captains']=list(plan['scope'])
            record['structural_total']=len(record['captains'])*record.get('per_captain',0)
        self.update(key,plan.get('coverage',plan.get('info',{})))
        self.save()

    def finish(self,key,state,reason,result=None):
        if key not in self.active:return
        if result:self.update(key,result)
        self.state(key,state,reason);self.active.discard(key);self.save()

    def view(self,sig,*,waiting=None):
        candidates=[r for r in self.records if r['game']==sig['game']]
        if not candidates:return empty('Not recorded','No preparation record for this slate. Use Prepare this slate to check coverage.')
        def matching(r):
            rosters=r['players']==sig['players'] and sig['cap']<=r['cap'] and set(sig['captains']).issubset(r['captains'])
            screen=rosters and sig['screening']==r['screening']
            return rosters,screen
        record=max(enumerate(candidates),key=lambda pair:(sum(matching(pair[1])),pair[1]['id'] in self.active,pair[0]))[1]
        rosters,screen=matching(record)
        match='Matches captured inputs' if screen else 'Roster inputs match; screening inputs changed' if rosters else 'Roster inputs / Captain coverage changed'
        checked,total=record.get('checked'),record.get('structural_total')
        coverage='Not inspected' if checked is None else f'{checked:,}/'+(f'{total:,} combinations checked' if total else 'unknown combinations checked')
        if checked is not None and total and checked<=total:coverage+=f' ({100*checked/total:.1f}%)'
        if record.get('complete'):coverage='Complete (recorded)'+(' · '+coverage if checked is not None else '')
        if record.get('saved') is not None:coverage+=f"; {record['saved']:,} saved rosters"
        if record.get('partition_count') is not None:coverage+=f"; {record.get('completed_partitions',0)}/{record['partition_count']} Captain partitions complete"
        done,bank=record.get('screened'),record.get('screening_total')
        screening=('Not requested' if record['mode']=='off' else 'Not started / not inspected')
        if done is not None:
            screening=f'{done:,}/'+(f'{bank:,} candidates' if bank is not None else 'unknown total candidates')
            if bank and done<=bank:screening+=f' ({100*done/bank:.1f}%)'
        if record.get('screening_complete'):screening='Complete (recorded) · '+screening
        scope='All currently legal rosters' if record['mode']=='full' else 'Reusable Deep bank'
        reason=(waiting(record) if waiting and record['id'] in self.active else '') or record.get('reason') or record.get('pause_reason') or 'No pause recorded.'
        if record['state']=='Recorded' and record.get('pause_reason'):reason+=' Last pause: '+record['pause_reason']
        if record['state']=='Running':reason='No pause; preparation worker is running.'
        phase=record.get('screening_phase') or record.get('phase') or record['state']
        batch=''
        if record.get('batch_scenarios_total'):batch=f"\nCurrent batch: {record.get('batch_candidates',0):,} candidates; {record.get('batch_scenarios',0):,}/{record['batch_scenarios_total']:,} scenarios (not committed screening progress)."
        detail=(f"Slate: {record['label']}\nCoverage: {coverage}\nLibrary / captured Captain scope: {len(record['captains'])}; currently required: {len(sig['captains'])}. Scope alone does not prove those Captains have saved candidates.\nScreening: {screening} ({scope})\nInputs: {match}\nPhase: {phase}\nPaused / waiting: {reason}"+batch+
            f"\nLibrary: {record['path'] or 'Not selected yet'}\nLast progress: {record.get('updated','unknown')}\nRecorded metadata only: files, model version, historical sources and final feasibility are rechecked by Prepare this slate / Generate. Matching inputs does not certify reusable files or a feasible portfolio.")
        state=record['state'] if screen else 'Working on different captured inputs' if record['id'] in self.active else 'Needs update'
        compact_coverage=(f'{100*checked/total:.0f}% checked' if checked is not None and total and checked<=total else 'not inspected')
        compact_screen=('not requested' if record['mode']=='off' else f'{done:,}/'+(f'{bank:,}' if bank is not None else 'unknown total') if done is not None else 'not started')
        return dict(state=state,coverage=coverage,screening=screening,match=match,reason=reason,detail=detail,
            summary=f"Preparation record: {state} · {match}",
            compact=f"Preparation record: {state} · Rosters: {compact_coverage} · Screening: {compact_screen} · {match}",record=record)


def empty(state,detail):
    return dict(state=state,coverage='Unknown',screening='Unknown',match='Not checked',reason=detail,detail=detail,summary='Preparation: '+state)


def waiting_reason(window,record):
    controller=window.idle_preparation
    if controller.readiness_key!=record['id']:return ''
    if controller.closed:return 'App closing; resume explicitly in a later session.'
    if controller.paused:return 'Paused by user; use Prepare this slate or explicit resume to continue.'
    queue=window.background_queue
    if record['state']=='Yielding':
        if queue.foreground():return 'Foreground work requested the worker to stop at its checkpoint.'
        names=[j['title'] for j in sorted(queue.pending.values(),key=lambda j:j['priority']) if j['priority']<30]
        if names:return 'Yielding for '+names[0]+'; waiting for checkpoint and worker retirement.'
        return 'App activity or an open dialog; checkpointing before returning to idle work.'
    if record['state']!='Queued':return ''
    if queue.active and queue.active['key']!='preparation':return 'Waiting for '+queue.active['title']+' to retire.'
    if queue.foreground():return 'Waiting for foreground work to retire.'
    if QtWidgets.QApplication.activeModalWidget() is not None:return 'Waiting for the open dialog to close.'
    delay=max(0,60-(window.idle_backup.clock()-window.idle_backup.last_input))
    if delay:return f'Waiting for idle app time ({math.ceil(delay)} seconds).'
    if window.history_refresh._reports:return 'Waiting for history or report work to retire.'
    return 'Queued; waiting for the background scheduler.'


def current_view(window):
    if not window.players:return empty('Load a slate','Load an NFL Showdown CSV to see preparation for its captured inputs.')
    if window._current_sport()!='NFL' or window._contest_mode()!='showdown':return empty('NFL Showdown only','This preparation summary supports NFL Showdown; Classic preparation remains in Advanced.')
    try:
        sig=signature(current_snapshot(window))
        return window.slate_readiness.view(sig,waiting=lambda r:waiting_reason(window,r))
    except (ValueError,TypeError,KeyError) as exc:return empty('Inputs need review',str(exc))


def install_summary(window):
    def refresh():
        view=current_view(window)
        label=window.lbl_slate_preparation
        text=view.get('compact',view['summary'])
        if 'record' in view and view['state'] in ('Queued','Yielding','Paused','Needs attention'):
            reason=view['reason'];text+=' · '+(reason[:110]+'…' if len(reason)>110 else reason)
        if label.text()!=text:label.setText(text)
        label.setToolTip(view['detail'])
    timer=window.slate_readiness_timer=QtCore.QTimer(window);timer.setInterval(1000);timer.timeout.connect(refresh);timer.start()
    window.slate_readiness.changed.connect(refresh)
    refresh()
