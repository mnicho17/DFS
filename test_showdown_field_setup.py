"""Exact pre-optimization receipts from main 1096cfe, synthetic players only."""
from test_environment import install
install()
import copy
import hashlib
import json
import unittest
from showdown_field import _sample_field
from showdown_simulation import showdown_signature
from test_showdown_performance import _showdown_players

BASELINE = {'normal': {'sha256': '12ec0aa6023bbbf5f48e317e320f538ce5166248707185f450b7327036cea473', 'count': 80, 'calls': 0}, 'fractional': {'sha256': '2858f9548fa199dec2f206753bb5259c26390c5446bc98f5b8a04187c7dbc562', 'count': 80, 'calls': 0}, 'one_team': {'sha256': 'b9ee811bf164063232cd1a30da4062eef40d0efc2b9bbb5511e5aefa1ff49c52', 'count': 0, 'calls': 0}, 'three_teams': {'sha256': '8e106c8cd216bc17cee764adec25074ce759cf365dd17b685162c6977b7188f6', 'count': 80, 'calls': 0}, 'fallback': {'sha256': '638fb3e57b1a15c53af9574de4326f990ec1c6681ed77c3ac0da1ddb8442722f', 'count': 80, 'calls': 0}, 'cancelled': {'sha256': 'e3545aed074f1c89d4b2c4a5890fd7359a36a162072074b5b0cb410469d6ad82', 'count': 10, 'calls': 38}}

class FieldSetupParityTests(unittest.TestCase):
    def check_case(self, name):
        players = _showdown_players()
        cap = 50000
        if name == 'fractional':
            for i, player in enumerate(players):
                player['FlexSalary'] += i * .013
                player['CptSalary'] += i * .017
        if name == 'one_team':
            for player in players: player['Team'] = 'A'
        if name == 'three_teams':
            for i, player in enumerate(players): player['Team'] = 'ABC'[i % 3]
        if name == 'fallback': cap = 39000
        calls = [0]
        def stop():
            calls[0] += 1
            return calls[0] >= 37
        before = copy.deepcopy(players)
        rows = _sample_field(players, 80, salary_cap=cap, seed=90211,
                             cancel_callback=stop if name == 'cancelled' else None)
        payload = dict(rows=[list(showdown_signature(row)) + [[p['FlexID'] for p in row['Flex']]] for row in rows],
                       diagnostic=rows.diagnostic)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(dict(sha256=digest, count=len(rows), calls=calls[0]), BASELINE[name])
        self.assertEqual(players, before)

    def test_seeded_rosters_flex_order_and_diagnostics(self): self.check_case('normal')
    def test_fractional_salary_arithmetic(self): self.check_case('fractional')
    def test_single_team_has_no_legal_rosters(self): self.check_case('one_team')
    def test_three_team_pool_still_requires_exactly_two(self): self.check_case('three_teams')
    def test_lower_cap_fallback_and_band_counts(self): self.check_case('fallback')
    def test_cancellation_draws_and_partial_rosters(self): self.check_case('cancelled')

if __name__ == '__main__': unittest.main()
