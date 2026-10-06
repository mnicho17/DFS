"""Read-only, whole-game holdout test of structural opponent-field fit."""
from collections import Counter
from contextlib import closing
from datetime import date
import hashlib
import json
import statistics
import analysis_imports as ai
from opponent_history import _connect, VERSION
from historical_field import CATEGORIES, distributions, sample_history_field, MODEL
from showdown_field import sample_field


def _pool(manifest, draw_mode='uniform'):
    roles = {}
    for row in manifest['players']:
        key = (ai._name(row['name']), row['team'], row['position'])
        group = roles.setdefault(key, {})
        if row['role'] in group:
            raise ValueError('Ambiguous historical athlete/role identity; no guess is made.')
        group[row['role']] = row
    players = []
    for key,group in sorted(roles.items()):
        if set(group)!= {'CPT','FLEX'}:
            raise ValueError('Historical salary pool lacks an exact CPT/FLEX association.')
        flex,captain = group['FLEX'],group['CPT']
        players.append(dict(Name=flex['name'],Team=flex['team'],Position=flex['position'],
            FlexID=flex['id'],CptID=captain['id'],FlexSalary=flex['salary'],CptSalary=captain['salary']))
        if draw_mode=='salary_proxy':
            # Ephemeral sampling inputs only: existing samplers' fallback uses
            # this weight^1.3. These values are never stored as athlete forecasts.
            players[-1]['FlexProjection']=flex['salary']/1000
    return players


def _counts(conn, key, cancelled):
    counts = {category:Counter() for category in CATEGORIES}
    known = entries = 0
    for i,(n,payload) in enumerate(conn.execute('SELECT entries,json_extract(stats_json,\'$.constructions\') FROM opponent_user_contest_stats WHERE contest_key=? ORDER BY user_key',(key,))):
        if i%500==0:ai._check(cancelled)
        entries+=n
        c = json.loads(payload or '{}');known+=c.get('known_entries',0)
        for category in CATEGORIES:
            counts[category].update({r['label']:r['entries'] for r in c.get('tables',{}).get(category,[])})
    probabilities = {category:{label:n/sum(values.values()) for label,n in values.items()} if values else {}
                     for category,values in counts.items()}
    return probabilities,known,entries


def _distance(predicted, observed):
    # Total variation distance, 0=identical and 1=disjoint category distributions.
    return .5*sum(abs(predicted.get(k,0)-observed.get(k,0)) for k in predicted.keys()|observed.keys())


def evaluate_history(db_path, cutoff, cancelled=lambda:False, progress=lambda text:None, *, count=300, seeds=(17,101,509), draw_mode='salary_proxy'):
    cutoff = date.fromisoformat(cutoff).isoformat()
    if not isinstance(count,int) or not 50<=count<=1000 or not seeds or len(seeds)>5 or len(set(seeds))!=len(seeds) or any(not isinstance(s,int) for s in seeds):
        raise ValueError('Use 50–1000 opponents and 1–5 distinct integer seeds.')
    if draw_mode not in ('uniform','salary_proxy'):
        raise ValueError('Choose uniform or explicitly hypothetical salary-proxy draw weights.')
    excluded, candidates, receipts = [],[],{}
    with closing(_connect(db_path,True)) as conn:
        conn.execute('BEGIN')
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='opponent_contests'").fetchone():
            raise ValueError('Index username history first.')
        sources = {s['hash']:s for s in ai._sources(conn)}
        pairs = dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        rows = conn.execute('SELECT contest_key,result_hash,salary_hash,start_date,end_date,name FROM opponent_contests WHERE format=? AND version=? ORDER BY end_date,contest_key',('showdown',VERSION)).fetchall()
        manifests = {}
        for i,(key,r,s,start,end,name) in enumerate(rows):
            ai._check(cancelled);progress(f'Verifying historical game {i+1}/{len(rows)}: {name}')
            try:
                if r not in sources or s not in sources or pairs.get(r)!=s:
                    raise ValueError('Saved source or salary association is unresolved.')
                for digest in (r,s):
                    if digest not in receipts:
                        ai._verify(sources[digest],cancelled);receipts[digest]=sources[digest]
                if not ai.qualify_pair(sources[r],sources[s])['compatible']:
                    raise ValueError('Saved salary association no longer qualifies.')
                if s not in manifests:manifests[s]=ai._salary_manifest(sources[s]['snapshot'],cancelled)
                manifest = manifests[s]
                if len(manifest['dates'])!=1 or len(manifest['games'])!=1 or start!=end or end!=manifest['dates'][0]:
                    raise ValueError('A single dated game is required; unresolved or spanning dates are excluded.')
                game = (end,manifest['games'][0])
                known = conn.execute("SELECT SUM(json_extract(stats_json,'$.constructions.known_entries')) FROM opponent_user_contest_stats WHERE contest_key=?",(key,)).fetchone()[0] or 0
                if not known:raise ValueError('No known historical constructions.')
                candidates.append(dict(key=key,result_hash=r,salary_hash=s,date=end,game=game,name=name,known=known,pool=_pool(manifest,draw_mode)))
            except ai.ImportCancelled:raise
            except Exception as exc:excluded.append(dict(name=name,reason=str(exc)))
        # Freeze one representative contest per whole game before comparing fit.
        # Size uses construction coverage, never finish ranks or winner choices.
        games = {}
        for row in sorted(candidates,key=lambda r:(-r['known'],r['key'])):games.setdefault(row['game'],row)
        training = sorted((r for r in games.values() if r['date']<cutoff),key=lambda r:r['game'])
        heldout = sorted((r for r in games.values() if r['date']>=cutoff),key=lambda r:r['game'])
        if len(training)<2 or not heldout:
            raise ValueError('Choose a cutoff with at least two earlier games and one later game indexed.')
        if len(training)>100 or len(heldout)>30:
            raise ValueError('This bounded experiment supports at most 100 training games and 30 held-out games.')
        priors = {category:Counter() for category in CATEGORIES}
        training_evidence = []
        for row in training:
            probabilities,known,entries = _counts(conn,row['key'],cancelled)
            if any(not probabilities[c] for c in CATEGORIES):raise ValueError('Training game lacks comparable construction metadata.')
            for category in CATEGORIES:
                priors[category].update({label:value/len(training) for label,value in probabilities[category].items()})
            training_evidence.append({k:row[k] for k in ('key','date','game','result_hash','salary_hash','name','known')})
        results = []
        for i,row in enumerate(heldout):
            ai._check(cancelled);progress(f'Testing later game {i+1}/{len(heldout)}: {row["game"][1]} {row["date"]}')
            observed,known,entries = _counts(conn,row['key'],cancelled)
            if any(not observed[c] for c in CATEGORIES):raise ValueError('Held-out game lacks comparable construction metadata.')
            models = {'current':[], 'historical':[]}
            for seed in seeds:
                ai._check(cancelled)
                current = sample_field(row['pool'],count,seed=seed,cancel_callback=cancelled)
                historical = sample_history_field(row['pool'],count,priors,seed=seed,cancelled=cancelled)
                ai._check(cancelled)
                for name,field in (('current',current),('historical',historical)):
                    sampled = distributions(field)
                    metric = {c:_distance(sampled[c],observed[c]) for c in CATEGORIES} if len(field)==count else None
                    models[name].append(dict(seed=seed,returned=len(field),distances=metric,distributions=sampled,
                        diagnostic=dict(getattr(field,'diagnostic',{}) or {})))
            # Underfilled fields are failures, never scored as comparable full fields.
            usable = all(r['distances'] is not None for trials in models.values() for r in trials)
            averages = {name:{c:statistics.mean(r['distances'][c] for r in trials) for c in CATEGORIES}
                        for name,trials in models.items()} if usable else None
            results.append(dict(**{k:row[k] for k in ('key','date','game','result_hash','salary_hash','name')},
                entries=entries,known_entries=known,unknown_entries=entries-known,observed=observed,
                models=models,comparable=usable,mean_distances=averages))
    # Verify current associations outside the read snapshot; a remap during the
    # benchmark must not publish results bound to the superseded association.
    with closing(_connect(db_path,True)) as conn:
        current_pairs = dict(conn.execute('SELECT result_hash,salary_hash FROM analysis_salary_pairs'))
        current_sources = {s['hash']:s for s in ai._sources(conn)}
        for row in training+heldout:
            ai._check(cancelled)
            if current_pairs.get(row['result_hash'])!=row['salary_hash']:
                raise ValueError('A salary association changed during testing; retry.')
            for digest in (row['result_hash'],row['salary_hash']):
                if digest not in current_sources or current_sources[digest]['snapshot']!=receipts[digest]['snapshot']:
                    raise ValueError('Historical evidence changed during testing; retry.')
        for digest,source in receipts.items():ai._verify(source,cancelled)
    usable = [r for r in results if r['comparable']]
    scores = {name:{c:statistics.mean(r['mean_distances'][name][c] for r in usable) for c in CATEGORIES}
              for name in ('current','historical')} if usable else {}
    aggregate = {name:statistics.mean(values.values()) for name,values in scores.items()}
    ai._check(cancelled)
    return dict(model=MODEL,cutoff=cutoff,count=count,seeds=list(seeds),draw_mode=draw_mode,priors={k:dict(v) for k,v in priors.items()},
        training=training_evidence,games=results,excluded=excluded,scores=scores,aggregate=aggregate,
        profile_digest=hashlib.sha256(json.dumps(dict(cutoff=cutoff,evidence=training_evidence,priors=priors),sort_keys=True).encode()).hexdigest(),
        limitations=['Retrospective structural fit only: original salary files do not establish pregame availability, ownership or projections.',
            'Both models use the same historical salary pool and '+('uniform athlete draw weights' if draw_mode=='uniform' else 'explicit salary-proxy draw weights (salary/1000, floored at 0.1, raised to 1.3); these are hypothetical sampling weights, not ownership or player forecasts')+'; no outcome ranks, points or winners train targets.',
            'One largest known-construction contest represents each game. Training and scores give each game equal weight; other contests from that game are not independent samples.',
            'The historical model changes marginal construction weights and permits any legal under-cap spending; joint correlations and opponent entry portfolios are not fitted.',
            'Unavailable proposal targets and incomplete generated fields are disclosed. Proposal absence is not proof of infeasibility.',
            'No lineup returns, ranking gains, skill or profitability are established. Default SIM, ownership, projections, limits and generated lineups remain unchanged.'])


def render_evaluation(report):
    lines=[f'Whole-game Showdown field experiment — training before {report["cutoff"]}',
           'Athlete draws: '+('uniform' if report['draw_mode']=='uniform' else 'salary-weighted proxy — hypothetical, not recorded pregame forecasts'),
           f'{len(report["training"])} training games; {len(report["games"])} later games; {report["count"]} opponents × {len(report["seeds"])} fixed seeds per model/game.',
           'Total variation distance: lower is closer to the observed construction distribution; 0 is identical, 1 is disjoint.',
           'Scores average seeds within each game, then give each comparable game equal weight.',
           'Training profile evidence digest: '+report['profile_digest']]
    for category in CATEGORIES:
        if report['scores']:lines.append(f'{category}: current {report["scores"]["current"][category]:.3f}; historical {report["scores"]["historical"][category]:.3f}')
    if report['aggregate']:
        lines.append(f'Average over the five categories: current {report["aggregate"]["current"]:.3f}; historical {report["aggregate"]["historical"]:.3f}')
    for game in report['games']:
        lines.append(f'\n{game["date"]} {game["game"][1]} — {game["known_entries"]:,} known / {game["entries"]:,} observed entries; representative: {game["name"]}')
        if game['comparable']:
            for name,values in game['mean_distances'].items():lines.append(f'{name}: mean distance {statistics.mean(values.values()):.3f}')
        else:lines.append('Incomplete generated field: excluded from aggregate scores.')
        for model,trials in game['models'].items():
            for trial in trials:
                missing=trial['diagnostic'].get('unavailable_targets')
                if missing:lines.append(f'{model} seed {trial["seed"]}: targets absent from proposals: {missing}')
                if trial['returned']!=report['count']:lines.append(f'{model} seed {trial["seed"]}: {trial["returned"]}/{report["count"]} opponents returned.')
    lines.extend('\n'+note for note in report['limitations'])
    lines.extend('Excluded '+r['name']+': '+r['reason'] for r in report['excluded'])
    return '\n'.join(lines)
