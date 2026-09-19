"""Run the normal unittest suite inside disposable storage and Qt settings."""
from pathlib import Path
import sys
import unittest

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository))
from test_environment import install, network_attempts
root = install()
suite = (unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1
         else unittest.defaultTestLoader.discover(str(repository)))
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(f"Unexpected network attempts: {network_attempts}")
raise SystemExit(0 if result.wasSuccessful() and not network_attempts else 1)
