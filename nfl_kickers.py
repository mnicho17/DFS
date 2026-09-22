"""Weekly kicker opportunities with explicit, uncalibrated shrinkage priors."""
import math
import re
from collections import defaultdict

VERSION = 'kicker-opportunities-v1'


def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else None
    except (TypeError, ValueError):
        return None


def build_kicking_index(rows, season=None):
    from nfl_auto_data import normalize_nfl_name, normalize_nfl_team
    groups, allowed = defaultdict(list), defaultdict(list)
    valid, seen = [], set()
    for r in rows:
        if str(r.get('position','')).upper()!='K' or str(r.get('season_type','REG')).upper() not in {'REG','REGULAR'}:
            continue
        if season and number(r.get('season')) not in (None,float(season)): continue
        week=number(r.get('week'))
        values=[number(r.get(k)) for k in ('fg_att','fg_made','pat_att','pat_made')]
        if not week or any(v is None for v in values): continue
        a,m,xa,xm=values
        if m>a or xm>xa: continue
        name=normalize_nfl_name(r.get('player_display_name') or r.get('player_name'))
        team=normalize_nfl_team(r.get('recent_team') or r.get('team'))
        key=(name,team,week)
        if not name or not team or key in seen: continue
        seen.add(key)
        b=[number(r.get('fg_made_'+k)) for k in ('0_19','20_29','30_39','40_49','50_59','60_')]
        buckets=[sum(b[:3]),b[3],sum(b[4:])] if all(v is not None for v in b) and sum(b)==m else [0,0,0]
        rec=dict(fga=a,fgm=m,xpa=xa,xpm=xm,buckets=buckets,week=week)
        valid.append(rec);groups[(name,team)].append(rec)
        opponent=normalize_nfl_team(r.get('opponent_team'))
        if opponent: allowed[opponent].append(rec)
    if not valid: return {}
    def aggregate(records, recent=True):
        records=sorted(records,key=lambda r:r['week'])[-8:] if recent else records
        return dict(games=len(records),**{k:sum(r[k] for r in records) for k in ('fga','fgm','xpa','xpm')},
                    buckets=[sum(r['buckets'][i] for r in records) for i in range(3)])
    league=aggregate(valid,False);names=defaultdict(int);out={}
    for name,team in groups: names[name]+=1
    opponents={t:aggregate(v) for t,v in allowed.items()}
    for key,records in groups.items():
        rec=dict(version=VERSION,season=season,player=aggregate(records),league=league,opponents=opponents)
        out[key]=rec
        if names[key[0]]==1: out[(key[0],'')]=rec
    return out


def attach_kicking_history(p,index):
    from nfl_auto_data import normalize_nfl_name,normalize_nfl_team
    if p.get('Position')!='K': return
    name=normalize_nfl_name(p.get('Name'));team=normalize_nfl_team(p.get('Team'))
    rec=index.get((name,team)) or index.get((name,''))
    p.pop('NFLKickingHistory',None)
    if rec:
        p['NFLKickingHistory']={k:v for k,v in rec.items() if k!='opponents'}
        p['NFLKickingHistory']['opponent']=rec['opponents'].get(normalize_nfl_team(p.get('Opponent')))


def prepare_kickers(players):
    from nfl_workload import available
    for p in players:
        if not p.get('ProjectionInputsVersion'): continue
        p.pop('KickerProjection',None);p.pop('NFLKickerOpportunities',None)
        h=p.get('NFLKickingHistory') or {}
        if p.get('Position')!='K' or not available(p) or h.get('version')!=VERSION: continue
        a,l=h['player'],h['league']
        if not a['games'] or not l['games']: continue
        years=re.findall(r'20\d{2}',str(p.get('GameInfo') or ''))
        discount=.5 if years and str(h.get('season'))!=years[0] else 1.
        games=a['games']*discount;prior_games=6.
        lg=lambda k:l[k]/l['games']
        volume=lambda k:(a[k]*discount+prior_games*lg(k))/(games+prior_games)
        fg_rate=l['fgm']/l['fga'] if l['fga'] else .85
        xp_rate=l['xpm']/l['xpa'] if l['xpa'] else .95
        fg_rate=(a['fgm']*discount+20*fg_rate)/(a['fga']*discount+20)
        xp_rate=(a['xpm']*discount+20*xp_rate)/(a['xpa']*discount+20)
        fga,xpa=volume('fga'),volume('xpa')
        opponent=h.get('opponent') or {};opponent_factor=1.
        if opponent.get('games') and lg('fga')>0:
            rate=(opponent['fga']*discount+prior_games*lg('fga'))/(opponent['games']*discount+prior_games)
            opponent_factor=max(.8,min(1.2,math.sqrt(rate/lg('fga'))));fga*=opponent_factor
        team_total=number(p.get('NFLVegasTeamTotal'));team_factor=1.
        historical_points=6.95*volume('xpa')+3*volume('fga')*fg_rate
        if p.get('NFLVegasState')=='ok' and team_total and historical_points>0:
            team_factor=max(.8,min(1.2,team_total/historical_points));fga*=team_factor;xpa*=team_factor
        weather_factor=max(.85,min(1.,1.+.04*float(p.get('NFLWeatherScore') or 0)));fg_rate*=weather_factor
        total=sum(l['buckets']);prior=[v/total for v in l['buckets']] if total else [.5,.3,.2]
        made=sum(a['buckets'])*discount
        mix=[(a['buckets'][i]*discount+20*prior[i])/(made+20) for i in range(3)]
        p['KickerProjection']=round(fga*fg_rate*sum(w*s for w,s in zip(mix,(3,4,5)))+xpa*xp_rate,4)
        p['NFLKickerOpportunities']=dict(version=VERSION,season=h.get('season'),games=a['games'],
            fga=fga,xpa=xpa,fg_rate=fg_rate,xp_rate=xp_rate,made_distance_mix=mix,
            prior_season_discount=discount,opponent_factor=opponent_factor,team_factor=team_factor,weather_factor=weather_factor,
            assumptions='Latest 8 observed games; 6-game league volume prior; 20-attempt accuracy and 20-made-kick distance priors. Prior-season evidence half weight. Opponent FG allowance and available implied total adjustments capped at 20%. No measured red-zone conversion or fitted outcome calibration.')
