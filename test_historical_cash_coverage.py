from test_environment import install
install()
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import analysis_imports as ai
from historical_cash_coverage import inspect_source,render


class HistoricalCashCoverageTests(unittest.TestCase):
    def source(self,text):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'source.csv';path.write_text(text,encoding='utf-8');return path
    def test_standings_scores_and_prize_pool_do_not_establish_cash(self):
        p=self.source('Rank,EntryId,Points,FPTS,PrizePool\n1,one,100,20,$100000\n');before=p.read_bytes()
        c=inspect_source(p);self.assertEqual(c['state'],'missing_columns')
        self.assertEqual(c['missing'],['entry_fee','winnings','currency']);self.assertEqual(p.read_bytes(),before)
        self.assertIn('No cash totals or ROI calculated',render(c))
    def test_column_presence_never_qualifies_values_zero_losses_or_prizes(self):
        p=self.source('EntryId,Entry Fee,Winnings,Currency,Prize Type\none,,ticket,USD,ticket\n')
        c=inspect_source(p);self.assertEqual(c['state'],'columns_present_values_unverified')
        self.assertIn('VALUES UNVERIFIED',render(c));self.assertNotIn('net',c);self.assertNotIn('roi',c)
        self.assertEqual(c['columns']['prize_type'],['prizetype'])
    def test_dollar_symbols_and_one_currency_side_are_not_explicit_compatible_currency(self):
        for header in ('EntryFee,Winnings','EntryFee,Winnings,Entry Fee Currency'):
            c=inspect_source(self.source(header+'\n$10,$20,USD\n'))
            self.assertEqual(c['state'],'missing_columns');self.assertEqual(c['missing'],['currency'])
    def test_separate_currency_columns_are_only_presence_not_compatibility(self):
        p=self.source('Buy-In,Actual Winnings,Entry Fee Currency,Winnings Currency\n10,20,USD,CAD\n')
        c=inspect_source(p);self.assertEqual(c['state'],'columns_present_values_unverified');self.assertEqual(c['missing'],[])
        self.assertIn('VALUES UNVERIFIED',render(c))
    def test_multiple_semantic_aliases_are_ambiguous_not_first_wins(self):
        for header in ('EntryFee,Buy-In,Winnings,ActualWinnings,Currency','ActualWinnings,Winnings,Buy-In,EntryFee,Currency'):
            c=inspect_source(self.source(header+'\n10,20,30,40,USD\n'))
            self.assertEqual(c['state'],'ambiguous_columns');self.assertEqual(c['ambiguous'],['entry_fee','winnings'])
            self.assertIn('ambiguous columns',render(c))
    def test_duplicate_normalized_or_malformed_headers_remain_unavailable(self):
        for text in ('EntryFee,entry_fee,Winnings,Currency\n10,10,20,USD\n','"EntryFee,Winnings,Currency\n',''):
            self.assertEqual(inspect_source(self.source(text))['state'],'unavailable')
        p=self.source('EntryId');p.write_bytes(b'\xff\xfe')
        self.assertEqual(inspect_source(p)['state'],'unavailable')
    def test_bounded_read_accepts_complete_header_before_large_body(self):
        p=self.source('EntryFee,Winnings,Currency\n'+('x'*200))
        with patch('historical_cash_coverage.MAX_HEADER_CHARS',40):
            self.assertEqual(inspect_source(p)['state'],'columns_present_values_unverified')
        p=self.source('EntryFee,Winnings,Currency,'+('x'*80)+'\n')
        with patch('historical_cash_coverage.MAX_HEADER_CHARS',40):
            self.assertEqual(inspect_source(p)['state'],'unavailable')
    def test_cancellation_is_not_converted_to_unavailable(self):
        p=self.source('EntryFee,Winnings,Currency\n')
        with self.assertRaises(ai.ImportCancelled):inspect_source(p,lambda:True)
        answers=iter((False,True))
        with self.assertRaises(ai.ImportCancelled):inspect_source(p,lambda:next(answers))
    def test_frozen_and_legacy_rendering_never_open_current_sources(self):
        c=inspect_source(self.source('EntryFee,Winnings,Currency\n'))
        with patch('builtins.open',side_effect=AssertionError('No source reinspection')):
            self.assertIn('VALUES UNVERIFIED',render(c));self.assertIn('older capture',render(None))
