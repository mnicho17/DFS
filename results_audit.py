"""Read-only diagnostics using stored forecasts and original standings side tables."""
import csv
import hashlib
import json
import math
import os
import re
import statistics
from collections import defaultdict


def _number(value):
    try:
        result = float(str(value).replace('%', '').replace(',', '').strip())
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _player_results(path, normalize):
    scores, ownership = {}, {}
    try:
        with open(path, newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            if not {'Player', 'FPTS'}.issubset(reader.fieldnames or []):
                return {}, {}, 'No player-result side table'
            blanks = 0
            for index, row in enumerate(reader):
                if index >= 5000:
                    return {}, {}, 'Player table exceeded audit read limit'
                name = normalize(row.get('Player'))
                if not name:
                    blanks += 1
                    if scores and blanks >= 10:
                        break
                    continue
                blanks = 0
                slot = str(row.get('Roster Position') or '').strip().upper()
                key = ('@cpt:' if slot in ('CPT', 'CAPTAIN') else '') + name
                value = _number(row.get('FPTS'))
                if value is not None:
                    scores[key] = value
                value = _number(row.get('%Drafted'))
                if value is not None:
                    ownership[key] = value
        return scores, ownership, '' if scores else 'No usable player scores'
    except (OSError, UnicodeError, csv.Error):
        return {}, {}, 'Original standings unavailable; reimport the results file'


def build_results_audit(conn, username=''):
    # Imported lazily to keep the database module independent of UI and this audit.
    from learning_db import _dk_username, _normalize_roster_token, _field_roster_signature
    lines = ['', 'Results audit — diagnostic only']
    lines.append('- No forecasts are regenerated and no model weights are changed.')
    imports = conn.execute('SELECT import_id,source_path,file_name FROM historical_imports ORDER BY created_at').fetchall()
    fingerprints = set()
    total_matched = 0
    for import_id, path, file_name in imports:
        rows = conn.execute(
            """SELECT h.entry_name,h.actual_points,h.raw_json,h.matched_lineup_id,
                      l.projection,l.base_projection,e.app_version,e.created_at
               FROM historical_results h LEFT JOIN lineups l ON l.lineup_id=h.matched_lineup_id
               LEFT JOIN exports e ON e.export_id=l.export_id WHERE h.import_id=?""",(import_id,)).fetchall()
        if username:
            rows = [r for r in rows if _dk_username(r[0]) == _dk_username(username)]
        if not rows:
            continue
        scores, ownership, problem = _player_results(path or '', _normalize_roster_token)
        if scores:
            signature = sorted((k,round(v,4)) for k,v in scores.items())
            fingerprints.add(hashlib.sha256(json.dumps(signature).encode()).hexdigest())
        matched = [r for r in rows if r[3]]
        total_matched += len(matched)
        lines.append(f'- Contest: {file_name}; your entries {len(rows)}, forecast matches {len(matched)}, unmatched {len(rows)-len(matched)}.')
        if problem:
            lines.append('  Data gap: '+problem)
        unmatched = [r for r in rows if not r[3]]
        for row in unmatched[:5]:
            raw = json.loads(row[2] or '{}')
            roster = str(raw.get('lineup') or '').strip()
            if roster:
                lines.append('  Unmatched roster: '+re.sub(r'\b\d{4,12}\b', '[player ID]', roster))
        if len(unmatched) > 5:
            lines.append(f'  {len(unmatched)-5} additional unmatched entries omitted from the copied audit.')
        checks = mismatches = missing = 0
        for row in rows:
            raw = json.loads(row[2] or '{}')
            tokens = _field_roster_signature(str(raw.get('lineup') or ''))
            if not tokens or row[1] is None or any(k not in scores for k in tokens):
                missing += 1
                continue
            checks += 1
            mismatches += abs(sum(scores[k] for k in tokens)-float(row[1])) > .11
        lines.append(f'  Score reconciliation: {checks} checked, {mismatches} mismatches (>0.11 DK points), {missing} missing player-score coverage.')
        captain_pairs = [(v,scores[k[5:]]) for k,v in scores.items() if k.startswith('@cpt:') and k[5:] in scores]
        if captain_pairs:
            bad = sum(abs(cpt-1.5*flex) > .02 for cpt,flex in captain_pairs)
            lines.append(f'  Captain actual scoring: {len(captain_pairs)} player pairs checked; {bad} differ from 1.5x FLEX (>0.02 points).')
        errors = [float(r[1])-float(r[4]) for r in matched if r[1] is not None and r[4] is not None]
        if errors:
            lines.append(f'  Lineup projection error: MAE {statistics.mean(abs(e) for e in errors):.2f}; actual minus forecast {statistics.mean(errors):+.2f} DK points; {sum(e<0 for e in errors)}/{len(errors)} below forecast.')
        if matched:
            versions = sorted({str(r[6] or 'unrecorded') for r in matched})
            lines.append('  Saved export version labels: '+', '.join(versions)+'.')
            stamps = sorted({str(r[7]) for r in matched if r[7]})
            if stamps:
                lines.append(f'  Export timestamps: {stamps[0]} through {stamps[-1]}; pre-lock timing is not verified by this audit.')
        player_errors = defaultdict(list)
        player_sources = defaultdict(set)
        ownership_pairs = []
        seen = set()
        for row in matched:
            if row[3] in seen:
                continue
            seen.add(row[3])
            for name,slot,projection,own,context_json in conn.execute('SELECT name,slot,projection,ownership,context_json FROM lineup_players WHERE lineup_id=?',(row[3],)):
                key = ('@cpt:' if str(slot).upper()=='CPT' else '') + _normalize_roster_token(name)
                context = json.loads(context_json or '{}')
                player_sources[key].add(str(context.get('ProjectionSource') or 'not recorded'))
                if projection is not None and key in scores:
                    player_errors[key].append((float(projection),scores[key]))
                if own is not None and key in ownership:
                    ownership_pairs.append((key,float(own),ownership[key]))
        if player_errors:
            lines.append('  Largest player forecast misses (Captain and FLEX kept separate):')
            ranked = sorted(player_errors.items(),key=lambda kv:abs(statistics.mean(v[1]-v[0] for v in kv[1])),reverse=True)
            for key,values in ranked[:10]:
                forecast=statistics.mean(v[0] for v in values);actual=values[0][1]
                label=key.replace('@cpt:', 'Captain: ')
                sources=', '.join(sorted(player_sources[key]))
                lines.append(f'    {label}: forecast {forecast:.2f}, actual {actual:.2f}, error {actual-forecast:+.2f}; {len(values)} saved lineup appearances; source {sources}.')
            lines.append('  Player appearances reuse the same game outcome; they are not independent observations.')
        if ownership_pairs:
            unique = {(k,p,a) for k,p,a in ownership_pairs}
            stored = [x[1] for x in unique];actual = [x[2] for x in unique]
            lines.append(f'  Recorded ownership values: range {min(stored):.2f}–{max(stored):.2f}; median {statistics.median(stored):.2f}. Corresponding field median {statistics.median(actual):.2f}%.')
            lines.append('  Ownership units are not recorded in older exports. Low stored values can indicate sampling weights; do not automatically rescale or treat this as confirmed ownership calibration.')
        if not matched:
            lines.append('  Player forecast audit unavailable: no matching original exports. Scores and observed ownership remain usable.')
    lines.append(f'- Matched entries audited: {total_matched}; distinct player-score tables: {len(fingerprints)}. Identical tables across contests share outcomes; this is a proxy, not a verified independent-game count.')
    lines.append('- Review score mismatches first, then large player misses and ownership provenance. More lineups from the same game do not establish predictive accuracy.')
    lines.append('- Report contains player names and aggregate results, but excludes source paths, entry IDs and API keys.')
    return lines
