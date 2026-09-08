import unittest
from field_diagnostics import summarize_field, format_field
from optimizers import ShowdownLineup


def player(name, team, pos, own=None):
    return dict(Name=name, Team=team, Position=pos, FlexID=name, FlexSalary=5000,
                CptSalary=7500, FlexProjection=10, ProjOwnPct=own)


class FieldDiagnosticsTests(unittest.TestCase):
    def test_showdown_counts_duplicates_captain_swaps_and_slot_ownership(self):
        players = [player(str(i), 'A' if i < 3 else 'B', pos, 0)
                   for i, pos in enumerate(['QB', 'WR', 'DST', 'QB', 'K', 'DST'])]
        a = ShowdownLineup(players[0], players[1:])
        b = ShowdownLineup(players[1], [players[0]] + players[2:])
        report = summarize_field([a,a,b], players, showdown=True)
        self.assertEqual(report['unique_entries'], 2)
        self.assertEqual(report['largest_duplicate_count'], 2)
        self.assertAlmostEqual(report['repeated_entry_pct'], 33.33)
        self.assertEqual(report['ownership_sum_pct'], 600)
        self.assertEqual(report['both_defenses_pct'], 100)
        self.assertEqual(report['three_specialists_pct'], 100)
        self.assertEqual(report['qb_stack_observations'], 6)
        self.assertAlmostEqual(sum(r['sampled_pct'] for r in report['captain_ownership']), 100, places=2)
        self.assertAlmostEqual(sum(r['sampled_pct'] for r in report['flex_ownership']), 500, places=2)
        self.assertTrue(all(r['input_pct'] is None for r in report['captain_ownership']))

    def test_missing_zero_and_unused_players_are_distinct(self):
        used = player('used', 'A', 'QB', 0)
        unused = player('unused', 'B', 'WR', 25)
        missing = player('missing', 'B', 'TE')
        report = summarize_field([[used, missing]], [used, unused, missing], fallback=True)
        rows = {r['label'].split()[0]: r for r in report['ownership']}
        self.assertEqual(rows['used']['gap_pp'], 100)
        self.assertEqual(rows['unused']['gap_pp'], -25)
        self.assertIsNone(rows['missing']['gap_pp'])
        self.assertIn('fallback opponents', '\n'.join(format_field(report)))

    def test_classic_roster_order_does_not_create_unique_entries(self):
        roster = [player(str(i), 'A' if i < 4 else 'B', 'QB' if i == 0 else 'WR') for i in range(9)]
        report = summarize_field([roster, list(reversed(roster))], roster)
        self.assertEqual(report['unique_entries'], 1)
        self.assertEqual(report['ownership_sum_pct'], 900)
        self.assertEqual(report['salary_mean'], 45000)
        self.assertEqual(report['qb_receiver_stacks'], {'3': 2})

    def test_empty_field_is_unavailable(self):
        self.assertFalse(summarize_field([], [])['available'])
        self.assertEqual(format_field(summarize_field([], [])), [])

    def test_build_report_preserves_field_and_discloses_names(self):
        from build_diagnostics import create_build_diagnostic, format_build_report
        p = player('Example', 'A', 'QB', 20)
        field = summarize_field([[p]], [p])
        record = create_build_diagnostic(context={'kind': 'classic'}, timing_report={},
                                         sim_report={'field_diagnostic': field})
        self.assertEqual(record['field_diagnostic'], field)
        text = format_build_report(record)
        self.assertIn('Sampled opponent field', text)
        self.assertIn('Example', text)
        self.assertIn('includes lineup names', text)
        self.assertNotIn('no players, lineups', text)
