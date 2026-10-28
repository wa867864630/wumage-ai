"""Bounded, allowlisted release archives; validation never extracts files."""
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[5]
STORE = ROOT / '.tmp' / 'deployment-artifacts'
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 4096
RELEASE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}', re.ASCII)
DIGEST = re.compile(r'[a-f0-9]{64}', re.ASCII)
REQUIRED = {'apps/api/app/main.py', 'apps/api/app/workers/outbox.py', 'apps/api/alembic/env.py',
            'apps/api/alembic.ini', 'apps/api/requirements.txt', 'apps/web/dist/index.html'}
WEB_SUFFIXES = {'.html', '.js', '.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2', '.ttf'}
TYPE_CONTRACT_FILES = {
    'packages/contracts/application-types/v1/' + name for name in (
        'type-manifest.schema.json', 'application-envelope.schema.json', 'course-config.schema.json',
        'course-application.schema.json', 'examples/course-type.json', 'form-config.schema.json', 'examples/form-type.json')}
# Reviewed UI source inputs are hash evidence for the form type bundle, not dynamically loaded code.
TYPE_CONTRACT_FILES |= {'apps/web/src/FormEditor.tsx', 'apps/web/src/FormRuntime.tsx'}
TYPE_CONTRACT_FILES |= {'apps/web/src/ApprovalEditor.tsx', 'apps/web/src/ApprovalRuntime.tsx',
    'packages/contracts/application-types/v1/leave-config.schema.json', 'packages/contracts/application-types/v1/examples/leave-type.json'}
REQUIRED |= TYPE_CONTRACT_FILES


class ArtifactError(ValueError):
    pass


class BoundedReader:
    def __init__(self, source):
        self.source = source
        self.remaining = MAX_TOTAL + (MAX_FILES + 1) * 1024 + 2 * 1024 * 1024

    def read(self, size):
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ArtifactError('归档解压流大小超限。')
        return data


def allowed(name):
    path = PurePosixPath(name)
    if (path.as_posix() != name or path.is_absolute() or '\\' in name or ':' in name
            or any(part in ('..', '.') or part.startswith('.') for part in path.parts)
            or any(ord(char) < 32 for char in name)):
        return False
    if name == 'apps/api/alembic.ini' or name in {
        'apps/api/requirements.txt', 'apps/api/requirements-opengauss.txt', 'apps/api/requirements-observability.txt'}:
        return True
    if name in TYPE_CONTRACT_FILES:
        return True
    if name.startswith(('apps/api/app/', 'apps/api/alembic/')):
        return path.suffix == '.py' and '__pycache__' not in path.parts
    return name.startswith('apps/web/dist/') and path.suffix.lower() in WEB_SUFFIXES


def regular_file(path, root):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ArtifactError('产物包含链接或非普通文件。')
    for parent in path.relative_to(root).parents:
        if (root / parent).is_symlink():
            raise ArtifactError('产物目录不允许符号链接。')


def build_archive(root: Path, release_id: str, web_build: str):
    """Caller builds the frontend first, or explicitly marks existing dist unverified."""
    if not RELEASE.fullmatch(release_id) or web_build not in ('built', 'prebuilt-unverified'):
        raise ArtifactError('版本号或构建状态无效。')
    files = {}
    for directory in ('apps/api/app', 'apps/api/alembic', 'apps/web/dist'):
        source = root / directory
        if not source.is_dir() or source.is_symlink():
            raise ArtifactError('缺少后端、迁移或前端构建目录，或目录为链接。')
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ArtifactError('产物不允许符号链接。')
            if path.is_file() and allowed(path.relative_to(root).as_posix()):
                regular_file(path, root)
                files[path.relative_to(root).as_posix()] = path
    for name in ('apps/api/alembic.ini', 'apps/api/requirements.txt', 'apps/api/requirements-opengauss.txt',
                 'apps/api/requirements-observability.txt', *sorted(TYPE_CONTRACT_FILES)):
        path = root / name
        if path.exists():
            regular_file(path, root)
            files[name] = path
    if not REQUIRED.issubset(files) or len(files) > MAX_FILES:
        raise ArtifactError('产物缺少必要文件或文件数量超限。')
    contents = {}
    total = 0
    for name, path in sorted(files.items()):
        if path.stat().st_size > MAX_FILE:
            raise ArtifactError('产物单文件过大。')
        data = path.read_bytes()
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL:
            raise ArtifactError('产物大小超限。')
        contents[name] = data
    manifest = {'format': 'campus-release-v1', 'release_id': release_id, 'web_build': web_build,
                'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                          for name, data in contents.items()}}
    contents['manifest.json'] = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(contents.items()):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                entry.mode = 0o644
                archive.addfile(entry, io.BytesIO(data))
    payload = output.getvalue()
    if len(payload) > MAX_ARCHIVE:
        raise ArtifactError('压缩产物大小超限。')
    return payload


def validate_archive(payload: bytes, expected_sha256: str):
    if not DIGEST.fullmatch(expected_sha256) or len(payload) > MAX_ARCHIVE:
        raise ArtifactError('产物摘要或大小无效。')
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ArtifactError('产物摘要不匹配。')
    inventory = {}
    manifest = None
    total = 0
    try:
        # Stream mode avoids retaining an unbounded tar member table.
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode='rb') as zipped, tarfile.open(
                fileobj=BoundedReader(zipped), mode='r|') as archive:
            for entry in archive:
                if (not entry.isfile() or entry.name in inventory or len(inventory) >= MAX_FILES + 1
                        or not (entry.name == 'manifest.json' or allowed(entry.name))
                        or not 0 <= entry.size <= MAX_FILE):
                    raise ArtifactError('产物包含非法路径、链接、重复条目或过大文件。')
                total += entry.size
                if total > MAX_TOTAL or (entry.name == 'manifest.json' and entry.size > 1024 * 1024):
                    raise ArtifactError('产物展开大小超限。')
                stream = archive.extractfile(entry)
                if stream is None:
                    raise ArtifactError('无法读取产物文件。')
                data = stream.read(entry.size + 1)
                if len(data) != entry.size:
                    raise ArtifactError('产物文件不完整。')
                inventory[entry.name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                if entry.name == 'manifest.json':
                    manifest = json.loads(data)
    except (tarfile.TarError, OSError, EOFError, ValueError, RecursionError) as exc:
        raise ArtifactError('产物归档或清单无效。') from exc
    inventory.pop('manifest.json', None)
    if (not isinstance(manifest, dict) or manifest.get('format') != 'campus-release-v1'
            or not isinstance(manifest.get('release_id'), str) or not RELEASE.fullmatch(manifest['release_id'])
            or manifest.get('web_build') not in ('built', 'prebuilt-unverified')
            or manifest.get('files') != inventory or not REQUIRED.issubset(inventory)):
        raise ArtifactError('产物清单与文件内容不一致或缺少必要文件。')
    return {'sha256': expected_sha256, 'release_id': manifest['release_id'], 'file_count': len(inventory),
            'archive_bytes': len(payload), 'unpacked_bytes': total, 'web_build': manifest['web_build'],
            'verified': True, 'deployment_ready': False}


def local_artifact(sha256: str, store=None):
    if not DIGEST.fullmatch(sha256):
        raise ArtifactError('产物摘要无效。')
    root = STORE if store is None else store
    path = root / (sha256 + '.tar.gz')
    if not path.exists():
        return None
    regular_file(path, root)
    if path.stat().st_size > MAX_ARCHIVE:
        raise ArtifactError('压缩产物大小超限。')
    with path.open('rb') as source:
        payload = source.read(MAX_ARCHIVE + 1)
    return validate_archive(payload, sha256)
