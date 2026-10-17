"""Diagnostic-only opportunity hurdles; never enabled by normal build settings.

Conditional on the existing workload and offensive draw, E[trial]=baseline.
Continuous shared workloads are left intact. Gates are not a discrete allocation
of targets/carries or a complete play-by-play model. Kicker feedback runs after
these offensive changes in the simulator; its mean is not guaranteed preserved.
"""
import hashlib
import math
import random
from collections import Counter

VERSION='rotation-zero-prototype-v1'


def _number(value):
    if isinstance(value,bool):raise ValueError('Boolean opportunity value')
    value=float(value)
    if not math.isfinite(value) or value<0:raise ValueError('Invalid opportunity value')
    return value


def opportunity(workload):
    carries=_number(workload['carries']);targets=_number(workload['targets'])
    catch=_number(workload['efficiency'][1])
    if catch>1:raise ValueError('Invalid catch rate')
    return carries+targets*catch


def eligible(player):
    from nfl_eligibility import depth,unavailable
    w=player.get('NFLWorkload') or {}
    try:
        order=depth(player)
        return (player.get('Position') in {'RB','WR','TE'} and order>1 and not unavailable(player)
            and str(player.get('NFLAvailability') or '').strip().upper()!='STARTER'
            and player.get('ProjectionSource')=='Automatic workload estimate'
            and w.get('version')=='workload-v2' and _number(w['depth'])==order
            and order-_number(w['promoted_slots'])>1 and math.isfinite(opportunity(w)))
    except (KeyError,ValueError,TypeError,IndexError):return False


def _gate_rng(rng):
    # Separate stream: preserve every default offense/DST draw and specialist seed.
    return random.Random(hashlib.sha256((VERSION+repr(rng.getstate())).encode()).digest())


class RotationZeroPrototype:
    def __init__(self,*,opportunity_basis='sampled',zero_strength=1.,team_gate_share=0.):
        if opportunity_basis not in {'sampled','frozen'}:raise ValueError('Unknown opportunity basis')
        zero_strength=_number(zero_strength);team_gate_share=_number(team_gate_share)
        if zero_strength>1 or team_gate_share>1:raise ValueError('Prototype controls must be in [0, 1]')
        self.zero_strength=zero_strength;self.team_gate_share=team_gate_share
        self.opportunity_basis=opportunity_basis
        self.counts=Counter()

    def apply(self,players,sampled_workloads,outcomes,rng):
        from nfl_simulation import player_key
        keys=[player_key(p) for p in players]
        if len(keys)!=len(set(keys)):raise ValueError('Ambiguous prototype player identities')
        result=dict(outcomes);gate_rng=_gate_rng(rng)
        team_gates={}
        if self.team_gate_share:
            # A mixture of independent and common uniforms preserves each marginal
            # zero rate. It changes joint events, not workload allocation.
            teams=sorted({str(p.get('Team') or '').strip().upper() for p in players if eligible(p)})
            if '' in teams:raise ValueError('Team gate requires explicit team identity')
            for team in teams:
                shared=gate_rng.random()<self.team_gate_share
                team_gates[team]=gate_rng.random() if shared else None
        for player in sorted(players,key=player_key):
            if not eligible(player):continue
            key=player_key(player)
            w=player['NFLWorkload'] if self.opportunity_basis=='frozen' else sampled_workloads.get(id(player))
            if w is None:raise ValueError('Eligible player missing shared sampled workload')
            probability=self.zero_strength*min(.95,math.exp(-opportunity(w)))
            uniform=team_gates.get(str(player.get('Team') or '').strip().upper())
            if uniform is None:uniform=gate_rng.random()
            score=_number(outcomes[key]);zero=uniform<probability
            result[key]=0. if zero else score/(1-probability)
            self.counts['eligible_draws']+=1;self.counts['zero_gates']+=int(zero)
        return result
