"""Submitted-entry finish statistics independent of export and forecast matches."""
import math
import statistics


def username_finishes(conn,username):
    from learning_db import _dk_username,_parse_rank
    groups={}
    sizes=dict(conn.execute('SELECT import_id,MAX(field_size) FROM contest_field_summaries GROUP BY import_id'))
    for entry,rank,size,import_id in conn.execute('SELECT entry_name,rank_text,field_size,import_id FROM historical_results'):
        if not username or _dk_username(entry)!=_dk_username(username):continue
        group=groups.setdefault(import_id,dict(entries=0,percentiles=[],top_one=[]))
        group['entries']+=1;rank=_parse_rank(rank)
        size=int(sizes.get(import_id) or size or 0)
        if size<=0 or not 1<=rank<=size:continue
        group['percentiles'].append(100*(1-(rank-1)/size))
        group['top_one'].append(int(rank<=math.ceil(size*.01)))
    values=[v for g in groups.values() for v in g['percentiles']]
    hits=[v for g in groups.values() for v in g['top_one']]
    result=dict(entries=sum(g['entries'] for g in groups.values()),covered=len(values),
        avg_percentile=statistics.mean(values) if values else None,
        top_one_pct=statistics.mean(hits)*100 if hits else None,contests=[])
    for import_id,g in groups.items():
        name=conn.execute('SELECT file_name FROM historical_imports WHERE import_id=?',(import_id,)).fetchone()
        result['contests'].append(dict(name=name[0] if name else 'Unknown contest',entries=g['entries'],covered=len(g['percentiles']),
            avg_percentile=statistics.mean(g['percentiles']) if g['percentiles'] else None,
            top_one_pct=statistics.mean(g['top_one'])*100 if g['top_one'] else None))
    return result


def finish_lines(result):
    lines=['- Username-based finishes: '+str(result['covered'])+'/'+str(result['entries'])+' entries have a valid rank and field size; exports and snapshots are not required.']
    if result['covered']:
        lines += [f"- Average finish percentile: {result['avg_percentile']:.1f}% ({result['covered']} username-matched entries; higher is better).",
                  f"- Top 1% rate: {result['top_one_pct']:.1f}% ({result['covered']} username-matched entries)."]
    for row in result['contests']:
        if row['covered']:
            lines.append(f"  {row['name']}: {row['covered']}/{row['entries']} entries; average finish percentile {row['avg_percentile']:.1f}%; top 1% rate {row['top_one_pct']:.1f}%.")
        else:lines.append(f"  {row['name']}: finish comparison unavailable; rank or field size missing/invalid.")
    lines.append('- Finishes are entry-weighted across contests. Rank ties share the reported rank; top 1% uses rank <= ceil(field size / 100). These are finish rates, not cash rates or ROI.')
    return lines
