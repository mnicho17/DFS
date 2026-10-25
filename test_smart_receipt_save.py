from test_environment import install
install()
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smart_preparation import save


def windows_error(code):
    error=PermissionError('Synthetic Windows replacement denial')
    error.winerror=code
    return error


class SmartReceiptSaveTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'receipt.json'
        self.path.write_text('{"count":7981}')

    def test_transient_windows_denials_retry_same_complete_file_atomically(self):
        import os
        original=os.replace
        attempts=[]
        def replace(source,target):
            attempts.append(source)
            self.assertEqual(json.loads(Path(source).read_text()),{'count':8100})
            self.assertEqual(json.loads(self.path.read_text()),{'count':7981})
            if len(attempts)<=3:raise windows_error((5,32,33)[len(attempts)-1])
            return original(source,target)
        with patch('smart_preparation.os.replace',side_effect=replace),patch('smart_preparation.time.sleep') as sleep:
            save(self.path,{'count':8100})
        self.assertEqual(len(set(attempts)),1)
        self.assertEqual(sleep.call_count,3)
        self.assertEqual(json.loads(self.path.read_text()),{'count':8100})
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_persistent_denial_is_bounded_and_preserves_previous_receipt(self):
        previous=self.path.read_bytes()
        error=windows_error(5)
        with patch('smart_preparation.os.replace',side_effect=error) as replace,patch('smart_preparation.time.sleep') as sleep:
            with self.assertRaises(PermissionError) as raised:save(self.path,{'count':8100})
        self.assertIs(raised.exception,error)
        self.assertEqual(replace.call_count,7)
        self.assertAlmostEqual(sum(call.args[0] for call in sleep.call_args_list),3.15)
        self.assertEqual(self.path.read_bytes(),previous)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_other_errors_fail_immediately_without_replacing_previous_receipt(self):
        for error in (PermissionError('permissions'),windows_error(112),OSError('disk failure')):
            with self.subTest(error=error):
                previous=self.path.read_bytes()
                with patch('smart_preparation.os.replace',side_effect=error) as replace,patch('smart_preparation.time.sleep') as sleep:
                    with self.assertRaises(OSError):save(self.path,{'count':8100})
                self.assertEqual(replace.call_count,1)
                sleep.assert_not_called()
                self.assertEqual(self.path.read_bytes(),previous)
                self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_serialization_failure_never_replaces_previous_receipt(self):
        previous=self.path.read_bytes()
        with patch('smart_preparation.os.replace') as replace:
            with self.assertRaises(ValueError):save(self.path,{'count':float('nan')})
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(),previous)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
