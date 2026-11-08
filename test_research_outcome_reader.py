"""Research-only full-file score reads retain legacy evidence and finite budgets."""
from test_environment import install
install()
import copy
import csv
import io
from pathlib import Path
import unittest
from unittest.mock import patch
import analysis_imports as ai
import historical_identity as hi
import hindsight_evidence as he
from field_outcome_validation import _scores
from scripts.research_outcome_reader import ResearchScoreReader, _Budget
from test_hindsight_evidence import SourceFixture


class ResearchOutcomeReaderTests(unittest.TestCase):
    def setUp(self):
        self.fixture=SourceFixture();self.addCleanup(self.fixture.close)
        self.manifest=self.fixture.salary['manifest']
        with Path(self.fixture.source['snapshot']).open(encoding='utf-8-sig',newline='') as h:
            self.rows=list(csv.reader(h))
        self.path=self.fixture.root/'research.csv'

    def write(self, rows):
        with self.path.open('w',encoding='utf-8-sig',newline='') as h:csv.writer(h).writerows(rows)
        return dict(snapshot=str(self.path),hash=ai._hash(self.path),name='synthetic')

    def test_small_source_exact_legacy_parity_and_nonmutation(self):
        source=self.write(self.rows);original=self.path.read_bytes();manifest=copy.deepcopy(self.manifest)
        self.assertEqual(ResearchScoreReader().scores(source,manifest),_scores(source,manifest,lambda:False))
        self.assertEqual(original,self.path.read_bytes());self.assertEqual(manifest,self.manifest)
        self.assertEqual(hi.MAX_RESULTS,200_000)

    def test_real_legacy_row_boundary_and_late_scores(self):
        # No runtime budget monkeypatch: real standings prefix exceeds 200,000.
        rows=[self.rows[0]]+[['']*len(self.rows[0])]*200_001+self.rows[1:]
        source=self.write(rows)
        with self.assertRaisesRegex(ValueError,'row limit'):_scores(source,self.manifest,lambda:False)
        result=ResearchScoreReader().scores(source,self.manifest)
        self.assertEqual(len(result[1]),len(self.rows)-1)

    def test_late_explicit_identity_and_score_conflicts_are_not_skipped(self):
        for kind in ('identity','score','captain'):
            with self.subTest(kind=kind):
                late=list(self.rows[1]);h=self.rows[0]
                if kind=='identity':late[h.index('PlayerID')]='9999999'
                elif kind=='score':late[h.index('FPTS')]='99'
                else:
                    late[h.index('Roster Position')]='CPT'
                    late[h.index('PlayerID')]='1100';late[h.index('FPTS')]='99'
                source=self.write(self.rows+[['']*len(h)]*200_001+[late])
                with self.assertRaisesRegex(ValueError,'(?i)conflict'):
                    ResearchScoreReader().scores(source,self.manifest)

    def test_exact_row_and_byte_limits_and_one_over(self):
        source=self.write(self.rows);size=self.path.stat().st_size;n=len(self.rows)-1
        self.assertEqual(ResearchScoreReader(file_bytes=size,rows=n).scores(source,self.manifest),
                         _scores(source,self.manifest,lambda:False))
        with self.assertRaisesRegex(ValueError,'row budget'):
            ResearchScoreReader(rows=n-1).scores(source,self.manifest)
        for operation in ('scores','verify'):
            with self.subTest(operation=operation),self.assertRaisesRegex(ValueError,'file-byte budget'):
                reader=ResearchScoreReader(file_bytes=size-1)
                getattr(reader,operation)(source,*([self.manifest] if operation=='scores' else []))

    def test_capture_and_verification_cancellation(self):
        source=self.write(self.rows)
        for operation in ('scores','verify'):
            calls=[]
            def cancelled():calls.append(1);return len(calls)>=3
            with self.subTest(operation=operation),self.assertRaises(ai.ImportCancelled):
                getattr(ResearchScoreReader(),operation)(source,*([self.manifest] if operation=='scores' else []),cancelled=cancelled)

    def test_cancellation_during_score_scan_and_final_verification(self):
        source=self.write(self.rows);original=he._score_observations
        for phase in ('scan','final'):
            state={'cancel':False}
            def observed(*args):
                if phase=='scan':state['cancel']=True
                result=original(*args)
                state['cancel']=True
                return result
            with self.subTest(phase=phase),patch.object(he,'_score_observations',side_effect=observed),self.assertRaises(ai.ImportCancelled):
                ResearchScoreReader().scores(source,self.manifest,cancelled=lambda:state['cancel'])

    def test_time_budget_during_verification_and_scan(self):
        source=self.write(self.rows)
        with patch('scripts.research_outcome_reader.time.monotonic',side_effect=[0,1]):
            with self.assertRaisesRegex(ValueError,'elapsed-time'):ResearchScoreReader(seconds=.5).verify(source)
        original=he._score_observations;clock={'now':0}
        def observed(*args):clock['now']=2;return original(*args)
        with patch('scripts.research_outcome_reader.time.monotonic',side_effect=lambda:clock['now']),patch.object(he,'_score_observations',side_effect=observed):
            with self.assertRaisesRegex(ValueError,'elapsed-time'):ResearchScoreReader(seconds=1).scores(source,self.manifest)

    def test_changed_source_before_and_after_capture_withholds_result(self):
        source=self.write(self.rows);bad=dict(source,hash='0'*64)
        with self.assertRaisesRegex(ValueError,'source changed'):ResearchScoreReader().scores(bad,self.manifest)
        original=he._score_observations
        def observed(*args):
            result=original(*args);self.path.write_bytes(self.path.read_bytes()+b'\n');return result
        with patch.object(he,'_score_observations',side_effect=observed):
            with self.assertRaisesRegex(ValueError,'source changed'):ResearchScoreReader().scores(source,self.manifest)

    def test_growth_is_bounded_by_actual_reads_not_stat(self):
        source=self.write(self.rows);size=self.path.stat().st_size;original=he._score_observations
        def observed(*args):
            result=original(*args);self.path.write_bytes(self.path.read_bytes()+b'x'*100);return result
        with patch.object(he,'_score_observations',side_effect=observed):
            with self.assertRaisesRegex(ValueError,'file-byte budget'):
                ResearchScoreReader(file_bytes=size).scores(source,self.manifest)

    def test_score_observation_guard_retained_and_no_cross_source_fill(self):
        source=self.write(self.rows)
        with patch.object(hi,'MAX_SCORE_ROWS',2),self.assertRaisesRegex(OverflowError,'observation limit'):
            ResearchScoreReader().scores(source,self.manifest)
        h=self.rows[0];missing=copy.deepcopy(self.rows)
        for row in missing[1:]:row[h.index('FPTS')]=''
        source=self.write(missing);reader=ResearchScoreReader()
        self.assertEqual(reader.scores(source,self.manifest)[1],{})
        other=dict(self.fixture.source)
        self.assertTrue(reader.scores(other,self.manifest)[1])
        self.assertEqual(reader.scores(source,self.manifest)[1],{})

    def test_invalid_csv_header_and_total_byte_budget(self):
        source=self.write([['Player','Player'],['A','A']])
        with self.assertRaisesRegex(ValueError,'Duplicate CSV columns'):
            ResearchScoreReader().scores(source,self.manifest)
        budget=_Budget(dict(file_bytes=1,seconds=60),lambda:False)
        with self.assertRaisesRegex(ValueError,'total-byte'):budget.consume(3+64*1024+1)

    def test_multibyte_dialect_sample_with_exact_byte_budget(self):
        rows=copy.deepcopy(self.rows)
        rows[1][rows[0].index('EntryName')]='😀'*3000
        source=self.write(rows);size=self.path.stat().st_size
        self.assertEqual(ResearchScoreReader(file_bytes=size).scores(source,self.manifest),
                         _scores(source,self.manifest,lambda:False))

    def test_invalid_budgets_rejected(self):
        for kwargs in ({'rows':0},{'rows':True},{'file_bytes':-1},{'file_bytes':1.5},{'seconds':float('inf')},{'seconds':float('nan')},{'seconds':True},{'seconds':0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):ResearchScoreReader(**kwargs)


if __name__=='__main__':unittest.main()
