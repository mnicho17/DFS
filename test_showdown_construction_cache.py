"""Exact weighted draws and output parity frozen from main 4eab227."""
from test_environment import install
install()
import copy
import hashlib
import json
import unittest
from optimizers import ShowdownOptimizer, _pkey
from test_showdown_performance import _showdown_players
from scripts.benchmark_scenario_replay import exact

BASELINE = {'Strategic': {'rosters': 'a9b7654449ba9577ebdb1f4c43f5dfbc27273e47f157a8a8ff318ad4b4cbeb4d', 'weighted_draws': '879ebf4b948ca2fe31667fbf3be9c54f9ded6bad3d12c7a64bb6dbe1b5264ff6', 'rng': 'c1ffaad313f5afa60c5f464d7981b068fbadec375fa248bb4c54d8d58e6a5e4a', 'count': 12}, 'Balanced': {'rosters': 'a9b7654449ba9577ebdb1f4c43f5dfbc27273e47f157a8a8ff318ad4b4cbeb4d', 'weighted_draws': '879ebf4b948ca2fe31667fbf3be9c54f9ded6bad3d12c7a64bb6dbe1b5264ff6', 'rng': 'c1ffaad313f5afa60c5f464d7981b068fbadec375fa248bb4c54d8d58e6a5e4a', 'count': 12}, 'Contrarian': {'rosters': 'a9b7654449ba9577ebdb1f4c43f5dfbc27273e47f157a8a8ff318ad4b4cbeb4d', 'weighted_draws': '879ebf4b948ca2fe31667fbf3be9c54f9ded6bad3d12c7a64bb6dbe1b5264ff6', 'rng': 'c1ffaad313f5afa60c5f464d7981b068fbadec375fa248bb4c54d8d58e6a5e4a', 'count': 12}, 'Chalk': {'rosters': 'a9b7654449ba9577ebdb1f4c43f5dfbc27273e47f157a8a8ff318ad4b4cbeb4d', 'weighted_draws': '879ebf4b948ca2fe31667fbf3be9c54f9ded6bad3d12c7a64bb6dbe1b5264ff6', 'rng': 'c1ffaad313f5afa60c5f464d7981b068fbadec375fa248bb4c54d8d58e6a5e4a', 'count': 12}, 'Randomized': {'rosters': 'a9b7654449ba9577ebdb1f4c43f5dfbc27273e47f157a8a8ff318ad4b4cbeb4d', 'weighted_draws': '879ebf4b948ca2fe31667fbf3be9c54f9ded6bad3d12c7a64bb6dbe1b5264ff6', 'rng': 'c1ffaad313f5afa60c5f464d7981b068fbadec375fa248bb4c54d8d58e6a5e4a', 'count': 12}, 'caps': {'rosters': '911a809e109fbd8cf0bf39c572f6af1ada79d103ef81c5051e9cfb9b7ba76539', 'weighted_draws': '82185460b48be74d75f1bb0bd59458be1d8b8ce90bf3955185fb9dd263258de9', 'rng': '5b997bd462ff55bd951e7f66ab28611197282b18116181a17c1c2f890e3bfe2d', 'count': 4}, 'exhausted': {'rosters': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945', 'weighted_draws': '86e170744a74fadc41610a498d2a86963a36cf0a4245436787112f7b3b83fbe0', 'rng': 'a0b8ce81ba2b407fd9dcf44427039078efc8029d12d7de4c9980552433ab3694', 'count': 0}, 'cancelled': {'rosters': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945', 'weighted_draws': '16ee1988b39389173c97c928cc6fc1c67ff4008eb8c644ef7f1bc42dfeb8fb3b', 'rng': '1e1f8fe29e68b305dc0ab971ee18199b51b17331ca54167f3a12ae15ba5254d4', 'count': 0}, 'repeat': {'rosters': 'be1b4ca3161b9ba9bf90829111b22214f028e570a754e8f4976bb2766cfad76e', 'weighted_draws': '3fd727c2d5536aee1cdf190ac263ace838dcfcaebd982f5a123883c41458b20e', 'rng': '03f026920b2f5431fddb0f3365f930b800014c3fd490d0e238e6af3daf97c45a', 'count': 24}}

def capture(label):
    players=_showdown_players()
    for p in players[:5]:p['LockCpt']=True
    style=label if label in ('Strategic','Balanced','Contrarian','Chalk','Randomized') else 'Strategic'
    minimum=47500
    if label=='caps':
        for p in players:p.update(MaxCptPct=25,MaxFlexPct=40)
        players[8]['LockFlex']=True;players[8]['MaxFlexPct']=100
        players[9]['FadeFlex']=True
    if label=='exhausted':
        players=players[:3]+players[18:21]
        for p in players:p['LockCpt']=False
        players[0]['LockCpt']=True;minimum=0
    before=copy.deepcopy(players)
    optimizer=ShowdownOptimizer(players,seed=35764,own_mode='Balanced',own_weight=.15,build_style=style,minimum_salary=minimum)
    draws=hashlib.sha256();choose=optimizer.rng.choices
    def observed(pool,*args,**kwargs):
        record=([_pkey(p) for p in pool],kwargs.get('weights'))
        draws.update(json.dumps(exact(record),separators=(',',':')).encode()+b'\n')
        return choose(pool,*args,**kwargs)
    optimizer.rng.choices=observed
    calls=[]
    def cancelled():calls.append(1);return len(calls)>=25
    options=dict(cancel_callback=cancelled) if label=='cancelled' else {}
    if label=='exhausted':
        options['excluded_signatures']={(_pkey(players[0]),tuple(sorted(_pkey(p) for p in players[1:])))}
    rows=optimizer._build_lineups_fast(12,**options)
    signatures=[(_pkey(r['Captain']),tuple(_pkey(p) for p in r['Flex'])) for r in rows]
    if label=='repeat':
        rows2=optimizer._build_lineups_fast(12)
        signatures.extend((_pkey(r['Captain']),tuple(_pkey(p) for p in r['Flex'])) for r in rows2)
    assert players==before
    return dict(rosters=hashlib.sha256(json.dumps(signatures,separators=(',',':')).encode()).hexdigest(),weighted_draws=draws.hexdigest(),rng=hashlib.sha256(repr(optimizer.rng.getstate()).encode()).hexdigest(),count=len(signatures))

class ConstructionCacheTests(unittest.TestCase):
    def test_all_styles_keep_original_ordered_flex_and_weighted_draws(self):
        for style in ('Strategic','Balanced','Contrarian','Chalk','Randomized'):
            with self.subTest(style=style):self.assertEqual(capture(style),BASELINE[style])
    def test_exposure_changes_locks_and_fades_keep_original_draws(self):self.assertEqual(capture('caps'),BASELINE['caps'])
    def test_duplicate_exhaustion_keeps_original_random_state(self):self.assertEqual(capture('exhausted'),BASELINE['exhausted'])
    def test_cancellation_keeps_original_checkpoint_and_draws(self):self.assertEqual(capture('cancelled'),BASELINE['cancelled'])
    def test_repeated_calls_rebuild_private_state(self):self.assertEqual(capture('repeat'),BASELINE['repeat'])

if __name__=='__main__':unittest.main()
