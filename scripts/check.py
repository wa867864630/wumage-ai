"""Run from repository root: .venv/Scripts/python.exe scripts/check.py [--database]."""
import argparse
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api'))

parser = argparse.ArgumentParser()
parser.add_argument('--database', action='store_true', help='Isolated test schema in the configured development DB')
options = parser.parse_args()
logging.getLogger('campus.requests').disabled = True
if options.database:
    from app.db import get_engine
    os.environ['TEST_DATABASE_URL'] = get_engine().url.render_as_string(hide_password=False)
suite = unittest.defaultTestLoader.discover(str(ROOT / 'apps/api/tests'))
from app.errors import APIError
# Tests opt into synthetic model responses; never use the operator's saved key.
with patch('app.modules.intent.fallback.active_model', side_effect=APIError(409, 'AI_DISABLED', 'Test model disabled')):
    result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
