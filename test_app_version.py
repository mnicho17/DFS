import subprocess
import sys
import types
import unittest
from unittest import mock

import app_version


class AppVersionTests(unittest.TestCase):
    def test_release_and_newer_source_builds_are_distinguishable(self):
        for distance, expected in [('0', 'v1.24.0'), ('32', 'v1.24.0+32')]:
            with mock.patch.object(app_version, '_git', side_effect=[f'v1.24.0-{distance}-gabcdef0', 'abcdef012345', '']):
                value = app_version.identity()
            self.assertEqual(value['version'], expected)
            self.assertEqual(app_version.label(value), expected + ' · abcdef0')

    def test_modified_source_is_visible(self):
        with mock.patch.object(app_version, '_git', side_effect=['v1.24.0-2-gabcdef0', 'abcdef012345', ' M app.py']):
            self.assertIn('modified', app_version.label(app_version.identity()))

    def test_untagged_checkout_has_build_identity_without_inventing_version(self):
        with mock.patch.object(app_version, '_git', side_effect=['abcdef0', 'abcdef012345', '']):
            self.assertEqual(app_version.label(app_version.identity()), 'Development · abcdef0')

    def test_packaged_app_uses_embedded_identity_without_git(self):
        stamp = types.SimpleNamespace(BUILD_INFO=dict(version='v1.25.0', commit='123456789', modified=False))
        with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.dict(sys.modules, build_info_generated=stamp), mock.patch.object(app_version, '_git') as git:
            self.assertEqual(app_version.label(app_version.identity()), 'v1.25.0 · 1234567')
            git.assert_not_called()

    def test_missing_git_or_timeout_does_not_claim_latest(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('git', 2)):
            with mock.patch.object(app_version, '_git', side_effect=error), mock.patch.dict(sys.modules, build_info_generated=None):
                self.assertEqual(app_version.label(app_version.identity()), 'Version unavailable')

    def test_main_page_exposes_version_and_release_link(self):
        from PyQt5 import QtWidgets
        from main_window import MainWindow
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        info = dict(version='v1.25.0', commit='123456789', modified=False, source='packaged')
        with mock.patch.object(app_version, 'identity', return_value=info):
            window = MainWindow()
        try:
            self.assertIn('v1.25.0 · 1234567', window.lbl_app_version.text())
            self.assertIn(app_version.RELEASE_URL, window.lbl_app_version.text())
            self.assertTrue(window.lbl_app_version.openExternalLinks())
            self.assertIn('Running packaged build', window.lbl_app_version.toolTip())
        finally:
            window.close()
