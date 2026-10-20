"""Credential-free source baselines and isolated restore drills; never restores over the workspace."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {'apps', 'docs', 'infra', 'packages', 'scripts', '界面参考图'}
ROOT_FILES = {'.gitignore', '.env.example', 'AGENTS.md', 'README.md', 'plan.md', '任务记录.md',
    'AI驱动无代码智能应用平台.md', 'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml',
    'compose.yaml', 'config.example.toml', 'version.json'}
EXCLUDED = {'node_modules', '.venv', '.git', '.tmp', '.local', '.pnpm-store', 'dist', '__pycache__', '.pytest_cache', 'backups'}
MANIFEST = '_baseline/manifest.json'
MAX_BYTES = 128 * 1024 * 1024
MAX_FILES = 10000


class BaselineError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def allowed(name):
    path = PurePosixPath(name)
    if (not name or path.as_posix() != name or path.is_absolute() or '\\' in name or ':' in name
            or any(part in ('.', '..') or part in EXCLUDED for part in path.parts)):
        return False
    if any(part.startswith('.') for part in path.parts) and name not in ROOT_FILES:
        return False
    if len(path.parts) == 1:
        return name in ROOT_FILES
    return (path.parts[0] in SOURCE_DIRS and path.name not in ('config.local.toml', '.env')
        and path.suffix.lower() not in {'.secret', '.pyc', '.dump', '.backup', '.bundle', '.log', '.zip', '.gz', '.db', '.sqlite'})


def source_files(root=ROOT):
    result = {}
    def visit(folder):
        for path in sorted(folder.iterdir()):
            if path.is_symlink():
                raise BaselineError('Source baselines cannot include symlinks')
            if path.is_dir():
                if path.name not in EXCLUDED and not path.name.startswith('.'):
                    visit(path)
            elif allowed(path.relative_to(root).as_posix()):
                result[path.relative_to(root).as_posix()] = path
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.exists():
            if path.is_symlink():
                raise BaselineError('Source baselines cannot include symlinks')
            result[name] = path
    for name in sorted(SOURCE_DIRS):
        if (root / name).is_symlink():
            raise BaselineError('Source baselines cannot include symlinks')
        if (root / name).is_dir():
            visit(root / name)
    return dict(sorted(result.items()))


def inventory(root=ROOT):
    result = {}
    total = 0
    for name, path in source_files(root).items():
        data = path.read_bytes()
        total += len(data)
        if total > MAX_BYTES or len(result) >= MAX_FILES:
            raise BaselineError('Source baseline exceeds its budget')
        # Detect common accidental real model/private keys without echoing their values.
        if re.search(rb'\bsk-[A-Za-z0-9]{24,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', data):
            raise BaselineError('Credential-like material detected in a source candidate')
        result[name] = {'size': len(data), 'sha256': digest(data)}
    return result


def git_state(root=ROOT):
    def run(*args):
        return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=10)
    try:
        commit = run('rev-parse', '--verify', 'HEAD')
        if commit.returncode:
            return {'commit': None, 'clean': False, 'status': 'uncommitted-or-unavailable'}
        dirty = run('status', '--porcelain', '--untracked-files=normal')
        return {'commit': commit.stdout.strip(), 'clean': dirty.returncode == 0 and not dirty.stdout.strip(),
            'status': 'available'}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'clean': False, 'status': 'unavailable'}


def capture(root=ROOT):
    files = inventory(root)
    folder = root / '.local/baselines'
    folder.mkdir(parents=True, exist_ok=True)
    source_digest = digest(canonical(files))
    manifest = {'format': 'campus-source-baseline-v1', 'source_digest': source_digest, 'files': files,
        'created_at': datetime.now(timezone.utc).isoformat(), 'git': git_state(root),
        'scope': 'source-only; excludes local settings, keys, database data and generated dist'}
    # Content-addressed names avoid accidental overwriting of prior baselines.
    target = folder / ('source-' + source_digest + '.zip')
    if target.exists():
        return target, verify(target)
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, record in files.items():
            data = (root / name).read_bytes()
            if digest(data) != record['sha256']:
                raise BaselineError('Source changed while capturing; retry with a new baseline')
            archive.writestr(name, data)
        archive.writestr(MANIFEST, canonical(manifest))
    return target, verify(target)


def verify(path):
    if path.stat().st_size > MAX_BYTES:
        raise BaselineError('Archive exceeds its budget')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) > MAX_FILES + 1 or len(set(names)) != len(names) or MANIFEST not in names:
            raise BaselineError('Invalid or duplicate archive members')
        if sum(entry.file_size for entry in entries) > MAX_BYTES or archive.getinfo(MANIFEST).file_size > 4 * 1024 * 1024:
            raise BaselineError('Expanded archive exceeds its budget')
        for entry in entries:
            mode = entry.external_attr >> 16
            if entry.is_dir() or mode & 0o170000 == 0o120000 or (entry.filename != MANIFEST and not allowed(entry.filename)):
                raise BaselineError('Unsafe archive member')
        manifest = json.loads(archive.read(MANIFEST))
        if not isinstance(manifest, dict) or manifest.get('format') != 'campus-source-baseline-v1' or not isinstance(manifest.get('files'), dict):
            raise BaselineError('Unknown baseline format')
        if set(names) != set(manifest['files']) | {MANIFEST}:
            raise BaselineError('Archive and manifest disagree')
        if digest(canonical(manifest['files'])) != manifest.get('source_digest'):
            raise BaselineError('Invalid source digest')
        for name, record in manifest['files'].items():
            if not isinstance(record, dict) or set(record) != {'size', 'sha256'}:
                raise BaselineError('Invalid file record')
            data = archive.read(name)
            if len(data) != record['size'] or digest(data) != record['sha256']:
                raise BaselineError('Archive contents changed')
    return manifest


def drill(path, root=ROOT):
    manifest = verify(path)
    parent = root / '.tmp/baseline-drills'
    parent.mkdir(parents=True, exist_ok=True)
    # Only this newly created disposable directory is extracted into and removed.
    target = Path(tempfile.mkdtemp(prefix='source-', dir=parent))
    try:
        with zipfile.ZipFile(path) as archive:
            for name in manifest['files']:
                destination = target / name
                if not destination.resolve().is_relative_to(target.resolve()):
                    raise BaselineError('Restore target escapes the disposable directory')
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
        if inventory(target) != manifest['files']:
            raise BaselineError('Restored source differs')
        return {'status': 'passed', 'source_digest': manifest['source_digest'], 'file_count': len(manifest['files']),
            'scope': 'source-byte-restore; not a database or production deployment recovery'}
    finally:
        if target.resolve().parent != parent.resolve() or not target.name.startswith('source-'):
            raise BaselineError('Unexpected cleanup directory')
        shutil.rmtree(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['capture', 'verify', 'drill'])
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    try:
        if args.operation == 'capture':
            target, manifest = capture()
            report = {'status': 'passed', 'archive': target.relative_to(ROOT).as_posix(),
                'source_digest': manifest['source_digest'], 'file_count': len(manifest['files']), 'git': manifest['git']}
        elif args.archive is None:
            parser.error('--archive is required')
        elif args.operation == 'drill':
            report = drill(args.archive)
        else:
            manifest = verify(args.archive)
            report = {'status': 'passed', 'source_digest': manifest['source_digest'], 'file_count': len(manifest['files'])}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (BaselineError, OSError, ValueError, KeyError, zipfile.BadZipFile):
        print(json.dumps({'status': 'failed', 'reason': 'Source baseline validation or operation failed; no workspace restore performed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
