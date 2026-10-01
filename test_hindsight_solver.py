"""Independent exhaustive oracles and real CBC proof/identity checks for RL-07A."""
from test_environment import install
install()
import copy
from decimal import Decimal
import itertools
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import pulp
import bounded_solver
import hindsight_solver as hs
from hindsight_contract import Frozen, SCALE, score, restrictions, validate_roster


def athletes(kind='showdown',values=None):
    if kind=='showdown':
        names=list('ABCDEFGH');positions=['QB','RB','WR','TE','K','DST','WR','QB'];base=values or [20,16,12,8,4,0,-2,-5]
    else:
        names=['Q1','Q2','R1','R2','R3','W1','W2','W3','W4','T1','T2','D1','D2']
        positions=['QB']*2+['RB']*3+['WR']*4+['TE']*2+['DST']*2;base=values or [20,18,15,12,5,18,14,10,4,9,3,8,-2]
    out=[]
    for i,(name,pos,value) in enumerate(zip(names,positions,base)):
        units=int(Decimal(str(value))*SCALE)
        roles={'FLEX':dict(id=str(100+i),salary=6000,score_units=units),
               'CPT':dict(id=str(1100+i),salary=9000,score_units=units*3//2)} if kind=='showdown' else {
                   r:dict(id=str(100+i),salary=5000,score_units=units) for r in ([pos,'FLEX'] if pos in ('RB','WR','TE') else [pos])}
        out.append(dict(key=str(100+i),name=name,position=pos,team='NE' if i%2 else 'SEA',game='NE@SEA',roles=roles))
    return out


def captured(kind='showdown',pool=None,local=None):
    return Frozen.freeze(dict(version=1,format=kind,pool=pool if pool is not None else athletes(kind),
        gates={'supplied':dict(status='ready',blockers=[]),'snapshot':dict(status='ready' if local is not None else 'not_requested',blockers=[])},
        restricted=dict(rules=local or restrictions()),observed=dict(highest=None),actual_coverage=dict(known=8,athletes=8)))


def exhaustive(pool,kind,rules=None):
    """Test-only combinatorial oracle; no production model/validation/eligibility calls."""
    rules=rules or dict(salary_cap=50000,require=[],exclude=[],groups=[])
    best=None;ties=set()
    for chosen in itertools.combinations(pool,6 if kind=='showdown' else 9):
        if len({p['team'] for p in chosen})<2:continue
        if kind=='showdown':
            assignments=[[(p,'CPT' if p is captain else 'FLEX') for p in chosen] for captain in chosen]
        else:
            # Unique recorded primary positions in these oracles. Try every FLEX
            # athlete and count exact remaining positions, without MILP helpers.
            assignments=[]
            for flex in chosen:
                if 'FLEX' not in flex['roles']:continue
                assigned=[(p,'FLEX' if p is flex else p['position']) for p in chosen]
                if sorted(r for p,r in assigned)==sorted(['QB','RB','RB','WR','WR','WR','TE','FLEX','DST']):assignments.append(assigned)
        for rows in assignments:
            if any(role not in p['roles'] for p,role in rows):continue
            if sum(p['roles'][role]['salary'] for p,role in rows)>rules['salary_cap']:continue
            keys={p['key'] for p,r in rows}
            includes=lambda item:any(p['key']==item[0] and (item[1]=='ANY' or r==item[1]) for p,r in rows)
            if any(not includes(item) for item in rules['require']) or any(includes(item) for item in rules['exclude']):continue
            if any((len(keys&set(g['keys']))<1 if g['type']=='at_least_one' else len(keys&set(g['keys']))>1) for g in rules['groups']):continue
            value=sum(p['roles'][role]['score_units'] for p,role in rows)
            signature=tuple(sorted(('@cpt:' if kind=='showdown' and role=='CPT' else '')+p['key'] for p,role in rows))
            if best is None or value>best:best=value;ties={signature}
            elif value==best:ties.add(signature)
    return best,ties


class HindsightSolverTests(unittest.TestCase):
    def oracle(self,kind,pool=None,local=None,expected=None):
        pool=pool or athletes(kind);want,signatures=exhaustive(pool,kind,local)
        d=hs.calculate(captured(kind,pool,local),seconds=10).data
        r=d['scopes']['snapshot' if local is not None else 'supplied']
        self.assertEqual(r['status'],'optimal' if want is not None else 'infeasible',r)
        self.assertEqual(r['score_units'],want)
        if want is not None:
            self.assertEqual(r['ties']['status'],'complete',r)
            self.assertEqual({tuple(row['signature']) for row in r['lineups']},signatures)
            self.assertEqual(r['ties']['exact_total'],len(signatures))
            if expected is not None:self.assertEqual(Decimal(r['points']),Decimal(str(expected)))
        return d

    def test_real_showdown_unique_70_oracle(self):self.oracle('showdown',expected=70)
    def test_real_classic_unique_111_oracle(self):self.oracle('classic',expected=111)

    def test_fade_and_never_together_46_and_52(self):
        for group,expected in ((False,46),(True,52)):
            local=restrictions()
            if group:local['groups']=[dict(type='never_together',keys=['100','101'])]
            else:local['exclude']=[['100','ANY']]
            d=self.oracle('showdown',local=local,expected=expected)
            self.assertEqual(d['gaps']['snapshot_restriction_units'],(70-expected)*SCALE)

    def test_two_captain_ties_74_and_fine_exact_perturbation(self):
        self.oracle('showdown',athletes(values=[20,20,12,8,4,0,-2,-5]),expected=74)
        d=self.oracle('showdown',athletes(values=['20.01',20,12,8,4,0,-2,-5]),expected='74.015')
        self.assertEqual(d['scopes']['supplied']['ties']['exact_total'],1)

    def test_classic_flex_at_each_supported_position(self):
        for index in (4,8,10):
            pool=athletes('classic')
            for role in pool[index]['roles']:pool[index]['roles'][role]['score_units']=16*SCALE
            self.oracle('classic',pool)

    def test_all_negative_and_essential_zero_are_known(self):
        self.oracle('showdown',athletes(values=[-1,-2,-3,-4,-5,-6,-7,-8]),expected='-21.5')
        r=hs.calculate(captured(pool=athletes(values=[0]*8)),seconds=10,tie_limit=2).data['scopes']['supplied']
        self.assertEqual(r['score_units'],0);self.assertEqual(r['ties']['returned'],2)
        self.assertEqual(r['ties']['status'],'partial');self.assertIsNone(r['ties']['exact_total']);self.assertGreaterEqual(r['ties']['lower_bound'],3)

    def test_salary_boundary_and_lower_cap_changes_best_captain(self):
        pool=athletes();pool[0]['roles']['FLEX']['salary']=12000;pool[0]['roles']['CPT']['salary']=18000
        for cap in (42000,42001,41999):
            local=restrictions();local['salary_cap']=cap;self.oracle('showdown',pool,local)
        d=self.oracle('showdown',pool,dict(restrictions(),salary_cap=42000))
        self.assertNotEqual(d['scopes']['snapshot']['lineups'][0]['roster'][0]['key'],'100')

    def test_empty_one_team_and_contradictory_locks_prove_infeasible(self):
        pool=athletes()
        for p in pool:p['team']='NE'
        for cap in (captured(pool=[]),captured(pool=pool),captured(local=dict(restrictions(),require=[['100','CPT'],['101','CPT']]))):
            d=hs.calculate(cap,seconds=5).data
            name='snapshot' if cap.data['gates']['snapshot']['status']=='ready' else 'supplied'
            self.assertEqual(d['scopes'][name]['status'],'infeasible',d)

    def test_snapshot_role_lock_does_not_allow_captain_substitution(self):
        local=dict(restrictions(),require=[['100','FLEX']])
        d=self.oracle('showdown',local=local)
        self.assertEqual(next(s['role'] for s in d['scopes']['snapshot']['lineups'][0]['roster'] if s['key']=='100'),'FLEX')

    def test_independent_validator_rejects_ids_salary_roles_duplicates_and_game(self):
        pool=athletes();r=hs.calculate(captured(pool=pool),seconds=5,tie_limit=0).data['scopes']['supplied']['lineups'][0]['roster']
        for field,value in (('id','wrong'),('salary',0),('score_units',1),('role','QB'),('key','101')):
            rows=copy.deepcopy(r);rows[0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_roster(rows,pool,'showdown')
        invalid=copy.deepcopy(pool);invalid[0]['game']='NE@NYG'
        with self.assertRaises(ValueError):validate_roster(r,invalid,'showdown')

    def test_normalization_keeps_captain_and_classic_identity(self):
        for kind in ('classic','showdown'):
            pool=athletes(kind);r=hs.calculate(captured(kind,pool),seconds=5,tie_limit=0).data['scopes']['supplied']['lineups'][0]
            rows=copy.deepcopy(r['roster'])
            for s in rows:s['role']=' Captain ' if s['role']=='CPT' else ' D/St ' if s['role']=='DST' else ' '+s['role'].lower()+' '
            rows.reverse();self.assertEqual(validate_roster(rows,pool,kind),r)

    def test_stopped_integer_feasible_and_positive_gap_are_not_certificates(self):
        p=pulp.LpProblem('counterexample');p.assignStatus(pulp.LpStatusOptimal,pulp.LpSolutionIntegerFeasible)
        fake=dict(processed=True,zero_gap_options=True,exit_code=0,model_status=p.status,solution_status=p.sol_status,
                  termination='optimal',complete_variables=True)
        self.assertEqual(hs.proof(fake,p),'time_limit')
        p.assignStatus(pulp.LpStatusOptimal,pulp.LpSolutionOptimal);fake.update(solution_status=p.sol_status,termination='unverified')
        self.assertNotEqual(hs.proof(fake,p),'optimal')
        for status in (pulp.LpStatusUndefined,pulp.LpStatusUnbounded):
            p.assignStatus(status);fake.update(model_status=status,solution_status=p.sol_status)
            self.assertEqual(hs.proof(fake,p),'solver_error')

    def test_corrupt_witness_or_wrong_header_objective_fails(self):
        actual=bounded_solver.solve
        for mode in ('nonbinary','objective','missing_variables'):
            def bad(p,*args,**kwargs):
                result=actual(p,*args,**kwargs)
                if mode=='nonbinary':next(v for v in p.variables() if v.value()==1).varValue=.999
                elif mode=='objective':result['objective']='1'
                else:result['complete_variables']=False
                return result
            with self.subTest(mode=mode),patch.object(bounded_solver,'solve',side_effect=bad):
                r=hs.calculate(captured(),seconds=5).data['scopes']['supplied']
                self.assertIn(r['status'],('validation_failed','solver_error'));self.assertFalse(r['lineups'])

    def test_primary_solves_precede_ties_and_share_deadline(self):
        actual=bounded_solver.solve;deadlines=[];fixed=[]
        def observed(p,deadline,*args,**kwargs):
            deadlines.append(deadline);fixed.append(len(p.constraints));return actual(p,deadline,*args,**kwargs)
        with patch.object(bounded_solver,'solve',side_effect=observed):
            d=hs.calculate(captured(local=restrictions()),seconds=5).data
        self.assertEqual(len(set(deadlines)),1);self.assertEqual(fixed[0],fixed[1]);self.assertGreater(fixed[2],fixed[0])
        self.assertEqual([d['scopes'][n]['status'] for n in ('supplied','snapshot')],['optimal','optimal'])

    def test_timeout_and_cancel_do_not_claim_infeasibility_or_optimality(self):
        with patch.object(bounded_solver,'solve',side_effect=TimeoutError()):
            self.assertEqual(hs.calculate(captured(),seconds=5).data['scopes']['supplied']['status'],'time_limit')
        self.assertEqual(hs.calculate(captured(),seconds=0).data['scopes']['supplied']['status'],'time_limit')
        self.assertEqual(hs.calculate(captured(),cancelled=lambda:True).data['scopes']['supplied']['status'],'cancelled')

    def test_numerical_bounds_and_no_float_or_boolean_score_coercion(self):
        for value in ('10','10.0','10.00'):self.assertEqual(score(value),Decimal(10))
        for value in (True,10.0,'','NaN','Inf','1e1000','10000.01','0.00001'):
            with self.subTest(value=value),self.assertRaises(ValueError):score(value)
        self.assertEqual(score('0'),0);self.assertEqual(score('-1.25'),Decimal('-1.25'))

    def test_reordered_input_and_repeat_solve_match_without_mutation(self):
        pool=athletes(values=[20,20,12,8,4,0,-2,-5]);original=copy.deepcopy(pool)
        a=hs.calculate(captured(pool=pool),seconds=5).data
        b=hs.calculate(captured(pool=list(reversed(pool))),seconds=5).data
        self.assertEqual(a['scopes']['supplied']['lineups'],b['scopes']['supplied']['lineups']);self.assertEqual(pool,original)

    def test_invalid_budget_and_tie_limit(self):
        for kwargs in ({'seconds':True},{'seconds':31},{'seconds':float('nan')},{'tie_limit':21},{'tie_limit':False}):
            with self.assertRaises(ValueError):hs.calculate(captured(),**kwargs)

    def test_actual_solution_parser_rejects_stopped_gap_malformed_and_nonzero_exit(self):
        original=bounded_solver.subprocess.Popen
        cases=[('Stopped on time - objective value 1.00000000','Result - Stopped on time limit',0,'time_limit'),
               ('Optimal (within gap tolerance) - objective value 1.00000000','Result - Optimal solution found (within gap tolerance)',0,'solver_error'),
               ('not a solution','',0,'error'),(None,'',0,'missing'),(None,'',3,'error')]
        for header,log,exit_code,want in cases:
            def process(args,**kwargs):
                sol=args[args.index('-solution')+1]
                code='import pathlib,sys; '+('pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); ' if header else '')+'print(sys.argv[3]); sys.exit(int(sys.argv[4]))'
                return original([sys.executable,'-c',code,sol,(header or '')+'\n0 X0000000 1 0\n',log,str(exit_code)],**kwargs)
            with self.subTest(header=header,exit_code=exit_code),patch.object(bounded_solver.subprocess,'Popen',side_effect=process):
                p=pulp.LpProblem('proof',pulp.LpMaximize);x=pulp.LpVariable('x',cat='Binary');p+=x;p+=x==1
                if want=='error':
                    with self.assertRaises(Exception):bounded_solver.solve(p,time.perf_counter()+5,lambda:False,strict=True)
                else:
                    meta=bounded_solver.solve(p,time.perf_counter()+5,lambda:False,strict=True)
                    self.assertEqual(hs.proof(meta,p),'solver_error' if want=='missing' else want)

    def test_deadline_reaps_unresponsive_child_and_removes_owned_files(self):
        original=bounded_solver.subprocess.Popen;children=[];folders=[]
        def process(args,**kwargs):
            folders.append(Path(args[1]).parent)
            child=original([sys.executable,'-c','import time; time.sleep(60)'],**kwargs);children.append(child);return child
        with patch.object(bounded_solver.subprocess,'Popen',side_effect=process):
            report=hs.calculate(captured(),seconds=.2).data
        self.assertEqual(report['scopes']['supplied']['status'],'time_limit');self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children));self.assertTrue(all(not folder.exists() for folder in folders))

    def test_partial_tie_timeout_retains_certified_score_without_uniqueness_claim(self):
        original=bounded_solver.solve;calls=[]
        def limited(*args,**kwargs):
            calls.append(1)
            if len(calls)>1:raise TimeoutError()
            return original(*args,**kwargs)
        with patch.object(bounded_solver,'solve',side_effect=limited):r=hs.calculate(captured(),seconds=5).data['scopes']['supplied']
        self.assertEqual(r['status'],'optimal');self.assertEqual(r['points'],'70')
        self.assertEqual(r['ties']['status'],'partial');self.assertIsNone(r['ties']['exact_total'])

    def test_larger_synthetic_pools_measure_status_and_tie_completeness(self):
        for kind,target in (('classic',133),('showdown',32)):
            pool=athletes(kind);template=copy.deepcopy(pool)
            while len(pool)<target:
                i=len(pool);p=copy.deepcopy(template[i%len(template)]);p.update(key=str(100+i),name=f'Synthetic athlete {i}')
                for role,row in p['roles'].items():
                    row.update(id=str(100+i+(1000 if role=='CPT' else 0)),score_units=(i*217-600)* (3 if role=='CPT' else 2))
                pool.append(p)
            started=time.perf_counter();d=hs.calculate(captured(kind,pool),seconds=15,tie_limit=2).data;r=d['scopes']['supplied']
            self.assertEqual(r['status'],'optimal',r)
            print(f"RL-07A synthetic {kind}: {len(pool)} athletes, {time.perf_counter()-started:.3f}s, CBC {r['solver']['solver_version']}, PuLP {r['solver']['pulp_version']}, status {r['status']}, ties {r['ties']['status']}, returned {r['ties']['returned']}, lower bound {r['ties']['lower_bound']}")
