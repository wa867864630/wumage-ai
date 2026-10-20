"""One explicit entry point for migrate + API + durable delivery worker (local development)."""
from pathlib import Path
import os
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
if 'DATABASE_URL' in os.environ:
    print('DATABASE_URL is set and overrides config.local.toml (value hidden).')
try:
    with socket.create_connection(('127.0.0.1', 8000), timeout=.3):
        print('Port 8000 is already in use. Stop the old API with Ctrl+C first.')
        raise SystemExit(1)
except (ConnectionRefusedError, TimeoutError, OSError):
    pass
flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
subprocess.run([sys.executable, '-m', 'alembic', '-c', 'apps/api/alembic.ini', 'upgrade', 'head'], check=True)
worker = subprocess.Popen([sys.executable, '-m', 'app.workers.outbox'], cwd=ROOT / 'apps/api', creationflags=flags)
api = None
try:
    api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', 'apps/api',
        '--reload', '--reload-dir', 'apps/api/app', '--host', '127.0.0.1', '--port', '8000'])
    raise SystemExit(api.wait())
except KeyboardInterrupt:
    pass
finally:
    for process in (api, worker):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
