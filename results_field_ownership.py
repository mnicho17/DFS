"""Field ownership profiles use observed rosters, never projections or listed weights."""
import csv
import hashlib
import json
import math
import statistics
from collections import Counter

VERSION = 'observed-rosters-v2'


def field_profile(path, field_size, source=None, *, ownership=None, counts=None, cancelled=lambda:False):
    from learning_db import (_field_roster_signature, _parse_rank, _canon_result_col,
        _new_ownership_profile_accumulator, _add_ownership_profile, _finalize_ownership_profile, _ImportCancelled)
    def rows():
        with open(path,newline='',encoding='utf-8-sig') as f:
            sample=f.read(4096);f.seek(0)
            try:dialect=csv.Sniffer().sniff(sample,delimiters=',;\t|')
            except csv.Error:dialect=csv.excel
            reader=csv.DictReader(f,dialect=dialect)
            cols={_canon_result_col(h):h for h in reader.fieldnames or []}
            for i,row in enumerate(reader):
                if i%2500==0 and cancelled():raise _ImportCancelled()
                signature=_field_roster_signature(str(row.get(cols.get('lineup'),'') or ''))
                if not signature:continue
                key=hashlib.blake2b('\x1f'.join(signature).encode(),digest_size=12).digest()
                yield signature,key,_parse_rank(row.get(cols.get('rank')))
    if ownership is None:
        players=Counter();counts=Counter();n=0
        for signature,key,rank in rows():
            players.update(signature);counts[key]+=1;n+=1
        ownership={p:v/max(1,n)*100 for p,v in players.items()}
    acc=_new_ownership_profile_accumulator();cut=max(1,math.ceil(field_size*.01))
    for signature,key,rank in rows():
        _add_ownership_profile(acc,signature,key,ownership,top_one=0<rank<=cut)
    errors=[abs(v-source[p]) for p,v in ownership.items() if source and p in source]
    profile=_finalize_ownership_profile(acc,counts,source_vs_computed_mae=statistics.mean(errors) if errors else None)
    profile.update(version=VERSION,ownership_source='Observed roster appearances / parsed field entries')
    return profile


def refresh_cached_profile(conn,import_id,path,cancelled=lambda:False):
    from learning_db import _preflight_complete_field_csv
    rows=conn.execute('SELECT field_id,field_size,ownership_profile_json FROM contest_field_summaries WHERE import_id=?',(import_id,)).fetchall()
    for field_id,size,encoded in rows:
        old=json.loads(encoded or '{}')
        if old.get('version')==VERSION:continue
        preflight=_preflight_complete_field_csv(path,cancel_callback=cancelled)
        if not preflight.get('complete'):continue
        profile=field_profile(path,size,preflight.get('source_ownership'),cancelled=cancelled)
        conn.execute('UPDATE contest_field_summaries SET ownership_profile_json=? WHERE field_id=?',(json.dumps(profile),field_id))
