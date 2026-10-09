import copy
import unittest
from unittest.mock import patch

from showdown_field import ownership_salary_check, sample_field
from field_diagnostics import summarize_field, format_field
from showdown_simulation import showdown_signature


class OwnershipSalaryCompatibilityTests(unittest.TestCase):
    def players(self, salary=5000):
        return [dict(Name=str(i), FlexID=str(i), CptID='c'+str(i), Position='WR',
                     Team='A' if i < 3 else 'B', FlexSalary=salary, CptSalary=salary*1.5,
                     ProjCptOwnPct=100/6, ProjFlexOwnPct=500/6,
                     OwnershipUnits='percent_of_entries') for i in range(6)]

    def test_marginal_salary_proves_incompatible_spending_targets(self):
        players=self.players(); before=copy.deepcopy(players)
        check=ownership_salary_check(players,[60,25,10,5],100,50000)
        self.assertEqual(check['ownership_implied_mean'],32500)
        self.assertEqual(check['band_mean_min'],48650)
        self.assertEqual(check['band_mean_max'],49575)
        self.assertTrue(check['incompatible']); self.assertEqual(players,before)

    def test_inside_interval_is_only_a_necessary_condition(self):
        check=ownership_salary_check(self.players(7500),[60,25,10,5],100,50000)
        self.assertEqual(check['ownership_implied_mean'],48750)
        self.assertFalse(check['incompatible'])
        self.assertTrue(ownership_salary_check(self.players(8000),[60,25,10,5],100,50000)['incompatible'])

    def test_unknown_units_missing_slots_duplicate_identity_or_wrong_totals_are_unavailable(self):
        for change in ('units','slot','identity','totals'):
            players=self.players()
            if change=='units': players[0].pop('OwnershipUnits')
            if change=='slot': players[0].pop('ProjFlexOwnPct')
            if change=='identity': players[0]['FlexID']=players[1]['FlexID']
            if change=='totals': players[0]['ProjFlexOwnPct']=0
            self.assertIsNone(ownership_salary_check(players,[60,25,10,5],100,50000))
        self.assertIsNone(ownership_salary_check(self.players(),[60,25,10,5],0,50000))

    def test_actual_integer_band_targets_and_custom_cap(self):
        check=ownership_salary_check(self.players(),[1,0,0,0],1,40000)
        self.assertEqual(check['band_mean_min'],39600)
        self.assertEqual(check['band_mean_max'],40000)

    def test_diagnostic_preserves_seeded_sampling_and_explains_conflict(self):
        players=self.players(7500); before=copy.deepcopy(players)
        field=sample_field(players,20,seed=19)
        with patch('showdown_field.ownership_salary_check',return_value=None):
            baseline=sample_field(players,20,seed=19)
        self.assertEqual([showdown_signature(lu) for lu in field], [showdown_signature(lu) for lu in baseline])
        self.assertEqual(field.ownership_fit,baseline.ownership_fit)
        self.assertEqual(players,before)
        text='\n'.join(format_field(summarize_field(field,players,showdown=True)))
        self.assertIn('Mean-salary condition satisfied',text)
        field.diagnostic['ownership_salary_check']=ownership_salary_check(self.players(),[12,5,2,1],20,50000)
        text='\n'.join(format_field(summarize_field(field,players,showdown=True)))
        self.assertIn('cannot both be met',text)
        self.assertIn('No targets or settings were changed',text)
