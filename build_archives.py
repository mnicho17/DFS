"""Local audit archives of generated outputs; never export or submission records."""
import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import tempfile
import uuid
import zipfile
from functools import lru_cache
from numbers import Number
from pathlib import Path

from build_diagnostics import build_history_path, format_build_report
from build_snapshots import load_snapshot
from lineup_ranking import finish_rank, ranked_lineups
from optimizers import lineup_slots_for_sport

PLAYER_FIELDS = (
    'Name', 'Team', 'Position', 'GameInfo', 'GameKey', 'FlexID', 'CptID',
    'FlexNamePlusID', 'CptNamePlusID', 'FlexSalary', 'CptSalary',
    'FlexProjection', 'CptProjection', 'BaseProjection', 'ProjectionSource',
    'ProjOwnPct', 'ProjFlexOwnPct', 'ProjCptOwnPct', 'OwnershipUnits',
    'NFLUsageSeason', 'NFLUsageSource', 'NFLUsageGames', 'NFLUsageCheckedAt',
    'NFLUsageFetchState', 'NFLUsageFetchedAt', 'NFLQBEligible',
    'LockFlex', 'LockCpt', 'FadeFlex', 'FadeCpt', 'MinPct', 'MaxPct',
    'MinCptPct', 'MaxCptPct')


def archive_folder():
    return Path(build_history_path()).parent / 'build-archives'


def scalar(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Number):
        value = float(value)
        return value if math.isfinite(value) else None
    return None


@lru_cache(maxsize=1)
def implementation_id():
    from candidate_library import code_id
    return code_id()


def _csv_cell(value):
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def save_build_archive(payload, context, diagnostic):
    rows = list(payload.get('lineups') or [])
    if not rows:
        return dict(status='no outputs', lineups=0)
    kind = str(payload.get('kind') or context.get('kind') or 'classic').lower()
    sport = str(payload.get('sport') or context.get('sport') or 'NFL').upper()
    has_sim = any(finish_rank(lu)[0] for lu in rows)
    if has_sim:
        rows = ranked_lineups(rows)
    output = []
    for index, lineup in enumerate(rows, 1):
        assigned = ([('CPT', lineup['Captain'])] + [('FLEX', p) for p in lineup['Flex']]
                    if kind == 'showdown' else lineup_slots_for_sport(lineup, sport))
        if not assigned or any(p is None for _, p in assigned):
            raise ValueError('Cannot archive an incomplete output roster')
        slots = [dict(slot=slot, player={k: scalar(p[k]) for k in PLAYER_FIELDS if k in p})
                 for slot, p in assigned]
        metrics = {str(k): scalar(v) for k, v in (getattr(lineup, 'sim_metrics', {}) or {}).items()
                   if isinstance(v, (str, bool, Number)) or v is None}
        output.append(dict(rank=index, slots=slots, sim_metrics=metrics,
            source=str(getattr(lineup, 'candidate_source', '') or ''),
            archetype=str(getattr(lineup, 'candidate_archetype', '') or '')))
    folder = archive_folder()
    folder.mkdir(parents=True, exist_ok=True)
    input_id = str(context.get('input_id') or '')
    files = {}
    snapshot_status = 'unavailable'
    if re.fullmatch(r'[0-9a-f]{64}', input_id):
        try:
            snapshot = load_snapshot(str(folder.parent / 'snapshots' / (input_id + '.json')))
            if snapshot['input_id'] != input_id:
                raise ValueError('Snapshot ID mismatch')
            files['input-snapshot.json'] = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode('utf-8')
            snapshot_status = 'included'
        except (OSError, ValueError, TypeError, KeyError):
            snapshot_status = 'unavailable or invalid'
    now = datetime.datetime.now(datetime.timezone.utc)
    audit_id = uuid.uuid4().hex
    metadata = dict(schema_version=1, audit_id=audit_id, created_at=now.isoformat(),
        record_type='generated_outputs', submission_status='not established',
        build_status='cancelled' if payload.get('cancelled') else 'completed',
        sport=sport, kind=kind, input_id=input_id, snapshot_status=snapshot_status,
        app_code_id=implementation_id(), output_count=len(output),
        ordering='canonical SIM order' if has_sim else 'build output order',
        settings=context.get('settings') or {}, portfolio_rules=context.get('portfolio_rules') or {})
    files['lineups.json'] = json.dumps(dict(metadata=metadata, lineups=output),
        ensure_ascii=False, allow_nan=False).encode('utf-8')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['Rank', 'Slot', 'Player', 'Player ID', 'Team', 'Salary', 'Projection',
                     'Top 1%', 'Top 2%', 'Top 5%', 'First %', 'Mean points', 'Scenarios'])
    for row in output:
        m = row['sim_metrics']
        for slot in row['slots']:
            p = slot['player']; prefix = 'Cpt' if slot['slot'] == 'CPT' else 'Flex'
            writer.writerow([_csv_cell(v) for v in [row['rank'], slot['slot'], p.get('Name'),
                p.get(prefix+'ID') or p.get('FlexID'), p.get('Team'), p.get(prefix+'Salary'),
                p.get(prefix+'Projection'), *[m.get(k) for k in ('sim_top_one_pct', 'sim_top_two_pct',
                    'sim_top_five_pct', 'sim_win_rate', 'sim_mean', 'sim_scenarios')]]])
    files['audit-lineups.csv'] = stream.getvalue().encode('utf-8-sig')
    files['build-report.txt'] = format_build_report(diagnostic).encode('utf-8')
    files['manifest.json'] = json.dumps(dict(metadata=metadata,
        sha256={name: hashlib.sha256(data).hexdigest() for name, data in files.items()}),
        ensure_ascii=False, allow_nan=False).encode('utf-8')
    filename = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{audit_id[:12]}.zip"
    fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        os.replace(temporary, folder / filename)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return dict(status='saved', filename=filename, lineups=len(output), snapshot=snapshot_status)
