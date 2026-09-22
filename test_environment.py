"""Process-owned storage and network isolation, installed before DFS imports.

This module is only imported by tests/the isolated test runner. QSettings keeps
its real PyQt5 implementation and enum attributes; production identities never
reach native storage. Each window starts with its own empty INI settings file.
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import os
from pathlib import Path
import tempfile

_root = None
network_attempts = []
settings_stores = []


def install():
    global _root
    if _root is not None:
        return _root
    temporary = tempfile.TemporaryDirectory(prefix="dfs-tests-")
    _root = Path(temporary.name)
    previous_cwd = Path.cwd()
    os.environ["DFS_OPTIMIZER_DATA_DIR"] = str(_root / "data")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    # Relative logs/outputs (including solver scratch files) stay disposable.
    os.chdir(_root)
    from PyQt5 import QtCore
    real_settings = QtCore.QSettings

    class IsolatedSettings(real_settings):
        def __init__(self, *args):
            if args == ("DFS Optimizer", "DFS Optimizer"):
                filename = _root / f"settings-{len(settings_stores)}.ini"
            elif len(args) == 2 and args[1] == real_settings.IniFormat:
                # Existing tests supply their own disposable INI identities.
                # Preserve reopen semantics while keeping every store here.
                identity = hashlib.sha256(str(Path(args[0]).resolve()).encode()).hexdigest()
                filename = _root / f"explicit-{identity}.ini"
            else:
                raise AssertionError(f"Unexpected settings constructor: {args!r}")
            super().__init__(str(filename), real_settings.IniFormat)
            self.setFallbacksEnabled(False)
            assert self.format() == real_settings.IniFormat
            assert Path(self.fileName()).parent == _root
            assert not self.fallbacksEnabled()
            self.setValue("isolation_probe", "ok")
            self.sync()
            assert self.value("isolation_probe") == "ok"
            settings_stores.append(self)

    QtCore.QSettings = IsolatedSettings
    import socket
    real_connect = socket.socket.connect

    def deny_connect(sock, address):
        network_attempts.append(repr(address))
        raise AssertionError("Unexpected external connection in isolated tests")

    socket.socket.connect = deny_connect
    import requests
    real_request = requests.sessions.Session.request

    def deny_request(session, method, url, *args, **kwargs):
        network_attempts.append(f"{method} {url}")
        raise AssertionError("Unexpected external request in isolated tests")

    requests.sessions.Session.request = deny_request

    def cleanup():
        # Test cases own worker retirement. Never tear down isolation around a
        # surviving worker; failing cases must drain before they return.
        from PyQt5 import sip
        for settings in settings_stores:
            # Standalone documentation captures may have already destroyed
            # QApplication and its settings objects before atexit runs.
            if not sip.isdeleted(settings):
                settings.sync()
        settings_stores.clear()
        logging.shutdown()
        os.chdir(previous_cwd)
        temporary.cleanup()
        socket.socket.connect = real_connect
        requests.sessions.Session.request = real_request
        QtCore.QSettings = real_settings

    atexit.register(cleanup)
    return _root
