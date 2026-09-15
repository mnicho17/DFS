import unittest
from copy import deepcopy
from field_diagnostics import summarize_field, format_field
from lineup_space import calculate_lineup_space
from showdown_simulation import generate_showdown_field, showdown_signature
from showdown_field import sample_field
from optimizers import _salary, _cpt_salary
from test_showdown_performance import _showdown_players


class ShowdownFieldModelTests(unittest.TestCase):
    def test_salary_prior_raises_spend_with_legal_tail_and_determinism(self):
        players=_showdown_players();before=deepcopy(players)
        legacy=generate_showdown_field(players,200,seed=18,model='legacy')
        revised=generate_showdown_field(players,200,seed=18)
        again=generate_showdown_field(players,200,seed=18)
        self.assertEqual(players,before)
        self.assertEqual(len(revised),200)
        self.assertEqual([showdown_signature(lu) for lu in revised],[showdown_signature(lu) for lu in again])
        def salary(lu):return _cpt_salary(lu['Captain'])+sum(_salary(p) for p in lu['Flex'])
        self.assertGreater(sum(map(salary,revised))/200,sum(map(salary,legacy))/200+1000)
        self.assertTrue(any(salary(lu)<47000 for lu in revised))
        self.assertTrue(all(42500<=salary(lu)<=50000 for lu in revised))
        report=summarize_field(revised,players,showdown=True)
        self.assertIn('experimental salary-spending prior','\n'.join(format_field(report)))

    def test_unavailable_band_fallback_and_cancellation_are_disclosed(self):
        players=[dict(Name=str(i),FlexID=str(i),Team='A' if i<3 else 'B',Position='WR',
                      FlexSalary=7500,CptSalary=11250,FlexProjection=10) for i in range(6)]
        field=sample_field(players,20)
        self.assertEqual(len(field),20)
        self.assertGreater(field.diagnostic['fallback_entries'],0)
        self.assertEqual(sample_field(players,20,cancel_callback=lambda:True),[])
        self.assertEqual(sample_field(players,0),[])
        self.assertEqual(sample_field(players[:5],20),[])

    def test_legacy_ownership_is_not_reported_as_percentage_error(self):
        p=dict(Name='Example',FlexID='1',Team='A',Position='WR',FlexSalary=5000,ProjOwnPct=5)
        report=summarize_field([[p]],[p])
        self.assertIsNone(report['ownership'][0]['gap_pp'])
        text='\n'.join(format_field(report))
        self.assertIn('stored weight 5.00 (units unverified)',text)
        self.assertNotIn('difference +95.00',text)
        p['OwnershipUnits']='percent_of_entries'
        self.assertEqual(summarize_field([[p]],[p])['ownership'][0]['gap_pp'],95)

    def test_showdown_captain_lock_is_counted(self):
        players=_showdown_players();players[0]['LockCpt']=True
        space=calculate_lineup_space(players,mode='showdown')
        self.assertEqual(space['locked'],1)
        from build_diagnostics import create_build_diagnostic, format_build_report
        report=create_build_diagnostic(context={'kind':'showdown','lineup_space':space},timing_report={})
        self.assertIn('Slot locks: Captain 1; FLEX 0','\n'.join(format_build_report(report).splitlines()))
        players[0]['LockFlex']=True
        self.assertEqual(calculate_lineup_space(players,mode='showdown')['locked'],1)
