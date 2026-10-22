"""Synthetic report evidence; never modifies a user's history or calibration."""
import copy
import csv
import hashlib
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

import analysis_imports as ai
import historical_identity as hi
from historical_coverage import load_saved
from historical_field_report import FieldRows, RankRows, summarize, report_lines
from learning_db import generate_learning_report, load_nfl_field_calibration
import test_historical_identity as fixtures


class FieldReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HistoricalIdentityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.fixture()
        self.salary = self.fixture.salary['manifest']
        self.text = 'CPT Alpha FLEX Bravo FLEX Charlie FLEX Delta FLEX Echo FLEX Foxtrot'

    def saved(self):
        hi.reconcile(self.fixture.db)
        return load_saved(self.fixture.db), ai.pairing_state(self.fixture.db)

    def test_exact_captain_salary_and_identical_duplicate_rows(self):
        rows = FieldRows(self.salary)
        rows.add(self.text)
        rows.add(self.text)
        payload = rows.payload(True)
        self.assertEqual(payload['counts']['supported_rows'], 2)
        self.assertEqual(payload['counts']['salary_sum'], 65000)
        self.assertEqual(payload['features']['Captain team split: 3–3'], 2)
        self.assertIn('not unique entries', payload['basis'])

    def test_explicit_wrong_captain_id_and_partial_identity_are_unknown(self):
        rows = FieldRows(self.salary)
        rows.add(self.text.replace('Alpha', 'Alpha (100)'))  # FLEX ID at CPT
        rows.add(self.text.replace('Bravo', 'Missing'))
        rows.add(self.text.replace('Alpha', 'Alpha (1100)'))
        rows.add(self.text)  # a name without an ID resolves only when unique
        self.assertEqual(rows.counts['unknown_identity_rows'], 2)
        self.assertEqual(rows.counts['supported_rows'], 2)

    def test_conflicting_explicit_ids_have_order_independent_row_counts(self):
        texts = [self.text.replace('Alpha','Alpha (1100)'), self.text.replace('Alpha','Alpha (9999)')]
        results = []
        for sequence in (texts, list(reversed(texts))):
            rows = FieldRows(self.salary)
            for text in sequence:
                rows.add(text)
            results.append(rows.payload(True))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['counts']['unknown_identity_rows'], 1)

    def test_ambiguous_name_does_not_pick_first_salary_record(self):
        salary = copy.deepcopy(self.salary)
        player = copy.deepcopy(salary['players'][0]); player['id'] = '9999'
        salary['players'].append(player)
        for players in (salary['players'], list(reversed(salary['players']))):
            rows = FieldRows(dict(salary, players=players))
            rows.add(self.text)
            rows.add(self.text.replace('Alpha','Alpha (1100)'))
            self.assertEqual(rows.counts['unknown_identity_rows'], 1)
            self.assertEqual(rows.counts['supported_rows'], 1)

    def test_blank_malformed_and_single_team_rows_are_disclosed(self):
        salary = copy.deepcopy(self.salary)
        for p in salary['players']: p['team'] = 'SEA'
        rows = FieldRows(salary)
        for text in ('', 'CPT Alpha', self.text): rows.add(text)
        self.assertEqual(rows.counts['blank_roster_rows'], 1)
        self.assertEqual(rows.counts['malformed_roster_rows'], 1)
        self.assertEqual(rows.counts['invalid_team_rows'], 1)
        self.assertNotIn('salary_sum', rows.counts)

    def test_over_cap_excluded_not_clamped_and_partial_denominator(self):
        rows = FieldRows(self.salary)
        rows.add(self.text); rows.add('')
        expensive = copy.deepcopy(self.salary)
        for p in expensive['players']: p['salary'] = 10000
        invalid = FieldRows(expensive); invalid.add(self.text)
        self.assertEqual(invalid.counts['over_cap_rows'], 1)
        self.assertEqual(invalid.counts['supported_rows'], 0)
        self.assertEqual(rows.counts['salary_sum']/rows.counts['supported_rows'], 32500)
        self.assertEqual(rows.counts['entry_rows'], 2)

    def test_classic_eligible_flex_and_salary(self):
        from test_analysis_imports import salary_file
        path = salary_file(self.fixture.root/'classic.csv','classic')
        salary = ai._salary_manifest(path, lambda: False)
        rows = FieldRows(salary)
        rows.add('QB Alpha RB Bravo RB Charlie WR Delta WR Echo WR Foxtrot TE Golf FLEX Hotel DST Defense')
        self.assertEqual(rows.counts['salary_sum'], 45000)
        self.assertEqual(rows.features['FLEX position: RB'], 1)
        self.assertEqual(rows.features['Kicker/defense slots: 1'], 1)

    def test_scan_failure_clears_partial_report_metrics(self):
        with patch.object(hi,'MAX_OUTCOME_ROWS', 2):
            evidence = hi._outcomes(self.fixture.source,self.salary,lambda:False)
        self.assertFalse(evidence['field_row_summary']['scan_complete'])
        self.assertEqual(evidence['field_row_summary']['counts'], {})

    def test_source_change_during_second_pass_discards_summary(self):
        with patch.object(hi,'_verify', side_effect=ValueError('changed')):
            evidence = hi._outcomes(self.fixture.source,self.salary,lambda:False)
        self.assertFalse(evidence['field_row_summary']['scan_complete'])
        self.assertEqual(evidence['field_row_summary']['features'], {})

    def test_new_summary_is_revision_bound_and_missing_score_independent(self):
        saved, pairs = self.saved()
        summary = summarize(saved,pairs)
        self.assertEqual(summary['sources'],1)
        self.assertEqual(summary['mean_salary'],32500)
        self.assertEqual(summary['counts']['supported_rows'],30)
        changed = copy.deepcopy(pairs)
        changed['results'][0]['status'] = 'INVALID_SAVED_PAIR'
        self.assertIsNone(summarize(saved,changed)['mean_salary'])
        changed = copy.deepcopy(pairs)
        changed['results'][0]['salary_hash'] = 'different'
        self.assertEqual(summarize(saved,changed)['sources'],0)

    def test_no_actual_score_table_still_supports_salary_without_qualifying_outcomes(self):
        path = self.fixture.root/'no-scores.csv'
        with path.open('w',newline='',encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['EntryId','Lineup']); writer.writerow(['1',self.text])
        source = dict(snapshot=str(path),hash=hashlib.sha256(path.read_bytes()).hexdigest())
        evidence = hi._outcomes(source,self.salary,lambda:False)
        self.assertFalse(evidence['complete'])
        self.assertEqual(evidence['field_row_summary']['counts']['salary_sum'],32500)
        self.assertEqual(evidence['known_scores'],0)

    def test_missing_old_or_corrupt_cached_metrics_do_not_fall_back(self):
        saved, pairs = self.saved()
        data = saved['contests'][0].data
        del data['results_evidence']['field_row_summary']
        saved['contests'] = (hi.QualifiedHistoricalContest(json.dumps(data)),)
        self.assertIsNone(summarize(saved,pairs)['mean_salary'])
        self.fixture.change("UPDATE historical_contest_identities SET evidence_hash='bad'")
        saved = load_saved(self.fixture.db)
        self.assertEqual(saved['invalid_rows'],1)
        self.assertEqual(summarize(saved,pairs)['sources'],0)

    def test_report_uses_verified_data_preserves_legacy_calibration_and_cache(self):
        saved,pairs = self.saved()
        self.fixture.change('UPDATE contest_field_summaries SET avg_salary=50126,metadata_coverage_pct=55.6')
        before = self.fixture.query('SELECT * FROM contest_field_summaries')
        cache = self.fixture.query('SELECT * FROM historical_contest_identities')
        calibration = load_nfl_field_calibration('150-Max', db_path=self.fixture.db)
        with patch('historical_identity.reconcile',side_effect=AssertionError('report must not reconcile')):
            report = generate_learning_report(db_path=self.fixture.db,username='Synthetic_User')
        self.assertIn('Average source-row salary used: $32,500',report['text'])
        self.assertNotIn('Average field salary used: $50,126',report['text'])
        self.assertNotIn('Complete fields analyzed:',report['text'])
        self.assertIn('Cached reconciliation:',report['text'])
        self.assertIn('not established by these counts',report['text'])
        self.assertEqual(report['field_metadata_coverage_pct'],100)
        self.assertEqual(before,self.fixture.query('SELECT * FROM contest_field_summaries'))
        self.assertEqual(cache,self.fixture.query('SELECT * FROM historical_contest_identities'))
        self.assertEqual(calibration,load_nfl_field_calibration('150-Max',db_path=self.fixture.db))

    def test_existing_connection_read_keeps_caller_transaction(self):
        self.saved()
        with closing(sqlite3.connect(self.fixture.db)) as conn:
            conn.execute('BEGIN')
            saved = load_saved(self.fixture.db,conn=conn)
            self.assertEqual(len(saved['contests']),1)
            self.assertTrue(conn.in_transaction)

    def test_mixed_formats_keep_construction_denominators_separate(self):
        saved,pairs = self.saved()
        data = saved['contests'][0].data
        other = copy.deepcopy(data)
        other['results_evidence']['source_hash'] = 'classic-source'
        other['salary_evidence']['format'] = 'classic'
        payload = other['results_evidence']['field_row_summary']
        payload['features'] = {'FLEX position: RB':30,'Kicker/defense slots: 1':30}
        payload['cohorts'] = {}
        saved['contests'] += (hi.QualifiedHistoricalContest(json.dumps(other)),)
        pairs['results'].append(dict(pairs['results'][0],hash='classic-source'))
        summary = summarize(saved,pairs)
        text = '\n'.join(report_lines(saved,summary))
        self.assertIn('Captain position: QB: 30/30 (100.0%)',text)
        self.assertIn('FLEX position: RB: 30/30 (100.0%)',text)
        self.assertNotIn('Captain position: QB: 30/60',text)
        self.assertIn('Kicker/defense slots: 0: 30/30',text)
        self.assertIn('Kicker/defense slots: 1: 30/30',text)
        self.assertIn('Classic / Unclassified',text)
        self.assertIn('Showdown / Unclassified',text)

    def test_cohorts_use_ties_and_their_own_supported_denominators(self):
        rows = FieldRows(self.salary,usernames=['me'],rank_size=100,rank_basis='supplied field size; completeness unverified')
        rows.add(self.text,rank=1,username='me')
        rows.add(self.text,rank=1,username='other')
        rows.add('',rank=1,username='me')
        rows.add(self.text,rank=5,username='other')
        rows.add(self.text,rank=None,username='me')
        groups=rows.payload(True)['cohorts']
        self.assertEqual(groups['field']['counts']['supported_rows'],4)
        self.assertEqual(groups['top1']['counts']['entry_rows'],3)
        self.assertEqual(groups['top1']['counts']['supported_rows'],2)
        self.assertEqual(groups['top5']['counts']['supported_rows'],3)
        self.assertEqual(groups['rank1']['counts']['supported_rows'],2)
        self.assertEqual(groups['user:me']['counts']['supported_rows'],2)
        self.assertEqual(groups['user:me']['counts']['missing_rank_rows'],1)

    def test_rank_basis_never_infers_complete_field_from_observed_rows(self):
        ranks=RankRows()
        for i in range(100): ranks.add(dict(EntryId=str(i)))
        size,basis=ranks.receipt()
        self.assertEqual(size,100)
        self.assertIn('observed source-row',basis)
        self.assertIn('completeness unverified',basis)
        ranks.add(dict(EntryId='a',FieldSize='200'))
        self.assertEqual(ranks.receipt(),(200,'supplied field size; completeness unverified'))
        ranks.add(dict(EntryId='b',FieldSize='300'))
        self.assertIsNone(ranks.receipt()[0])
        rows=FieldRows(self.salary,rank_size=None)
        rows.add(self.text,rank=1)
        self.assertIn('rank1',rows.cohorts)
        self.assertNotIn('top1',rows.cohorts)

    def test_invalid_field_sizes_disable_percentile_cutoffs(self):
        for value in ('0','-1','nan','inf','1.5','unknown','1e308'):
            with self.subTest(value=value):
                ranks=RankRows();ranks.add(dict(EntryId='1',FieldSize=value))
                self.assertIsNone(ranks.receipt()[0])

    def test_current_username_is_selected_without_other_users_leaking_into_yours(self):
        saved,pairs=self.saved()
        first=summarize(saved,pairs,'SYNTHETIC_USER (1/20)')
        group=first['scopes'][0]['cohorts']['yours']
        self.assertEqual(group['counts']['supported_rows'],30)
        self.assertNotIn('yours',summarize(saved,pairs,'Someone Else')['scopes'][0]['cohorts'])
        self.assertNotIn('yours',summarize(saved,pairs,'')['scopes'][0]['cohorts'])

    def test_entry_presets_are_separate_and_unclassified_remains_explicit(self):
        saved,pairs=self.saved()
        first=saved['contests'][0].data
        first['results_evidence']['field_row_summary']['entry_preset']='Single Entry'
        second=copy.deepcopy(first)
        second['results_evidence']['source_hash']='20max'
        second['results_evidence']['field_row_summary']['entry_preset']='20-Max'
        saved['contests']=(hi.QualifiedHistoricalContest(json.dumps(first)),hi.QualifiedHistoricalContest(json.dumps(second)))
        pairs['results'].append(dict(pairs['results'][0],hash='20max'))
        scopes=summarize(saved,pairs)['scopes']
        self.assertEqual({s['preset'] for s in scopes},{'Single Entry','20-Max'})
        self.assertTrue(all(s['counts']['supported_rows']==30 for s in scopes))

    def test_older_summary_still_splits_format_but_does_not_supply_cohorts(self):
        saved,pairs=self.saved()
        data=saved['contests'][0].data
        payload=data['results_evidence']['field_row_summary']
        payload['version']=1;payload.pop('cohorts')
        saved['contests']=(hi.QualifiedHistoricalContest(json.dumps(data)),)
        summary=summarize(saved,pairs)
        self.assertEqual(summary['mean_salary'],32500)
        self.assertEqual(summary['scopes'][0]['old_sources'],1)
        self.assertEqual(summary['scopes'][0]['cohorts'],{})
        self.assertIn('older summaries lack cohort', '\n'.join(report_lines(saved,summary)))

    def test_username_budget_does_not_retain_arbitrary_subset(self):
        rows=FieldRows(self.salary,usernames=[str(i) for i in range(257)])
        rows.add(self.text,username='0')
        payload=rows.payload(True)
        self.assertTrue(payload['username_limit_exceeded'])
        self.assertEqual(set(payload['cohorts']),{'field'})

    def test_cohorts_are_cleared_on_scan_failure(self):
        rows=FieldRows(self.salary,usernames=['me'],rank_size=100)
        rows.add(self.text,rank=1,username='me')
        self.assertEqual(rows.payload(False)['cohorts'],{})

    def test_opposing_defense_and_kicker_features_are_descriptive(self):
        salary=copy.deepcopy(self.salary)
        for p in salary['players']:
            if p['name']=='Bravo': p['position']='DST'
            if p['name']=='Charlie': p['position']='K'
        rows=FieldRows(salary);rows.add(self.text)
        self.assertEqual(rows.features['Captain with opposing FLEX defense: yes'],1)
        self.assertEqual(rows.features['Kickers: 1'],1)
        self.assertEqual(rows.features['Defenses: 1'],1)
        self.assertEqual(rows.counts['supported_rows'],1)


if __name__ == '__main__':
    unittest.main()
