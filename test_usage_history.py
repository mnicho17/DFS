import copy
import unittest
from usage_history import season_index, match_season, attach_history, history_evidence
from nfl_auto_data import apply_auto_nfl_context, clear_nfl_context


def row(week, season=2025, name='Veteran', team='SEA', identity='one', **extra):
    return dict(player_display_name=name, team=team, player_id=identity, week=week,
                season=season, season_type='REG', position='RB', carries=0, **extra)


class UsageHistoryTests(unittest.TestCase):
    def test_full_season_distinct_games_excludes_postseason_and_other_years(self):
        rows=[row(w) for w in range(1,18)]+[row(1),row(1,2024),row(0),row(19)]
        post=row(18);post['season_type']='POST';rows.append(post)
        index=season_index(rows,2025)
        self.assertEqual(match_season(index,dict(Name='Veteran',Team='SEA'),2025)['games'],17)

    def test_trades_and_ambiguous_names(self):
        index=season_index([row(1),row(2,team='BUF'),row(2,team='SEA')],2025)
        self.assertEqual(match_season(index,dict(Name='Veteran',Team='NYJ'),2025)['games'],2)
        index=season_index([row(1,identity=''),row(2,team='BUF',identity='')],2025)
        self.assertEqual(match_season(index,dict(Name='Veteran',Team='NYJ'),2025)['state'],'ambiguous')
        self.assertEqual(match_season(index,dict(Name='Veteran',Team='SEA'),2025)['games'],1)
        index=season_index([row(1),row(2,identity='different')],2025)
        self.assertEqual(match_season(index,dict(Name='Veteran',Team='SEA'),2025)['state'],'ambiguous')

    def test_enrichment_keeps_recent_zero_and_full_prior_evidence(self):
        p=dict(Name='Veteran',Team='SEA',Position='RB',FlexProjection=8,BaseProjection=8)
        current=[row(1,2026)];prior=[row(w) for w in range(1,18)]
        apply_auto_nfl_context([p],sleeper_data={},usage_rows=current,usage_season=2026,
            prior_usage_rows=prior,fetch_external=False,season=2026)
        self.assertEqual(p['NFLUsageGames'],1)
        self.assertEqual(p['NFLRecentCarries'],0)
        self.assertEqual(p['NFLUsageHistory']['prior']['games'],17)
        self.assertFalse(history_evidence(p)['stress'])
        self.assertIn('Short current',history_evidence(p)['reason'])
        clear_nfl_context([p])
        self.assertNotIn('NFLUsageHistory',p)

    def test_absent_recent_window_does_not_erase_season_evidence(self):
        p=dict(Name='Veteran',Team='SEA',Position='RB',FlexProjection=8,BaseProjection=8)
        prior=[row(w) for w in range(1,10)]+[row(w,name='Other',identity='two') for w in range(14,18)]
        apply_auto_nfl_context([p],sleeper_data={},usage_rows=prior,usage_season=2025,
            fetch_external=False,season=2026)
        self.assertEqual(p['NFLUsageGames'],0)
        self.assertEqual(p['NFLUsageHistory']['prior']['games'],9)
        self.assertFalse(history_evidence(p)['stress'])

    def test_missing_source_no_match_and_legacy_are_not_career_counts(self):
        p=dict(Name='New',Team='SEA',NFLUsageGames=2)
        before=copy.deepcopy(p)
        self.assertIn('missing',history_evidence(p)['reason']);self.assertEqual(p,before)
        index=season_index([row(1)],2025)
        attach_history(p,{},index,2026,'now')
        self.assertIsNone(p['NFLUsageHistory']['prior']['games'])
        self.assertEqual(p['NFLUsageHistory']['prior']['state'],'no_player_match')
        self.assertIn('coverage incomplete',history_evidence(p)['reason'])
        attach_history(p,season_index([row(1,2026,name='New')],2026),index,2026,'now')
        self.assertIn('Limited matched',history_evidence(p)['reason'])
        p['Rookie']=True
        self.assertEqual(history_evidence(p)['reason'],'Explicit rookie flag')

    def test_both_formats_use_full_history_and_preserve_export_evidence(self):
        import tempfile
        from test_projection_sensitivity import ProjectionSensitivityTests
        from ownership_sensitivity import load_sensitivity_bank
        from projection_sensitivity import prepare_targets, PROFILES
        from learning_db import _player_projection_context
        for kind in ('classic','showdown'):
            with tempfile.TemporaryDirectory() as folder:
                path,_,_=ProjectionSensitivityTests().bank(folder,kind)
                payload=load_sensitivity_bank(path)['payload']
                before=path.read_bytes()
                for p in payload['players']:
                    rows=[row(w,name=p['Name'],team=p['Team']) for w in range(1,10)]
                    attach_history(p,{},season_index(rows,2025),2026,'test')
                    self.assertEqual(_player_projection_context(p,'FLEX')[3]['NFLUsageHistory'],p['NFLUsageHistory'])
                self.assertEqual(prepare_targets(payload)[PROFILES[2]],[])
                self.assertEqual(path.read_bytes(),before)
