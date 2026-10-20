"""Run named existing checks and persist their actual results; not a release approval command."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from baseline import ROOT, canonical, digest, git_state, inventory


def check_plan(profile, live=False, build=False):
    node = shutil.which('node') or 'node'
    web = ROOT / 'apps/web'
    checks = [
        ('contracts', [sys.executable, 'scripts/export_contracts.py', '--check'], ROOT),
        ('frontend-types', [node, str(web / 'node_modules/typescript/lib/tsc.js'), '--noEmit'], web),
        ('web-ssr-http', [node, 'scripts/check_web.mjs'], ROOT),
        ('application-schemas', [node, 'scripts/check_application_types.mjs'], ROOT),
    ]
    if profile == 'full':
        checks += [('backend-isolated-database', [sys.executable, 'scripts/check.py', '--database'], ROOT),
            ('web-dom-workbench', [node, 'scripts/check_workbench.mjs'], ROOT),
            ('synthetic-intent', [sys.executable, 'scripts/evaluate.py'], ROOT)]
    if live:
        checks.append(('live-demo-smoke', [sys.executable, 'scripts/verify_mvp.py'], ROOT))
    if build:
        checks.append(('production-web-build', [node, str(web / 'node_modules/vite/bin/vite.js'), 'build'], web))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['quick', 'full'], default='quick')
    parser.add_argument('--live', action='store_true', help='Loopback demo acceptance; creates/revokes test sessions only')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / '.local/checks/latest.json')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    logs = args.output.parent / (run_id + '-logs')
    logs.mkdir(exist_ok=False)
    before = digest(canonical(inventory()))
    result = {'format': 'campus-check-evidence-v1', 'run_id': run_id, 'profile': args.profile,
        'source_digest': before, 'git': git_state(), 'checks': [], 'production_ready': False,
        'limitations': ['DOM is not a manual browser/mobile acceptance',
            'Passed checks do not by themselves authorize deployment or database recovery']}
    result['status'] = 'running'
    args.output.write_bytes(canonical(result) + b'\n')
    for name, command, cwd in check_plan(args.profile, args.live, args.build):
        started = time.perf_counter()
        log = logs / (name + '.log')
        status, exit_code = 'failed', None
        with log.open('wb') as output:
            try:
                process = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, timeout=600)
                exit_code = process.returncode
                status = 'passed' if exit_code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                status = 'timeout'
            except OSError:
                status = 'blocked'
        entry = {'name': name, 'status': status, 'exit_code': exit_code,
            'duration_seconds': round(time.perf_counter() - started, 3), 'log': str(log)}
        result['checks'].append(entry)
        print(json.dumps({'check': name, 'status': status, 'duration_seconds': entry['duration_seconds']}), flush=True)
        # Persist incrementally; interrupted runs are never recorded as completed successes.
        result['status'] = 'running'
        args.output.write_bytes(canonical(result) + b'\n')
    result['source_unchanged_during_checks'] = before == digest(canonical(inventory()))
    result['status'] = 'passed' if result['source_unchanged_during_checks'] and all(
        check['status'] == 'passed' for check in result['checks']) else 'failed'
    args.output.write_bytes(canonical(result) + b'\n')
    print(json.dumps({'status': result['status'], 'report': str(args.output), 'production_ready': False}), flush=True)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
