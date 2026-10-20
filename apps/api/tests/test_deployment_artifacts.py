import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from app.modules.deployment import artifacts as a


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class ArtifactTests(unittest.TestCase):
    def fixture(self, root):
        for name in a.REQUIRED:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture', encoding='utf-8')

    def archive(self, entries):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for entry, data in entries:
                archive.addfile(entry, io.BytesIO(data))
        return output.getvalue()

    def test_deterministic_archive_excludes_local_secrets_caches_and_source_maps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            for name in ('config.local.toml', '.env', 'apps/api/app/private.secret',
                         'apps/api/app/__pycache__/module.py', 'apps/web/dist/main.js.map',
                         'apps/web/dist/.env', 'apps/web/src/main.tsx'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('must-not-ship', encoding='utf-8')
            payload = a.build_archive(root, 'release-1', 'prebuilt-unverified')
            self.assertEqual(payload, a.build_archive(root, 'release-1', 'prebuilt-unverified'))
            result = a.validate_archive(payload, digest(payload))
            self.assertTrue(result['verified'])
            self.assertFalse(result['deployment_ready'])
            self.assertEqual(result['web_build'], 'prebuilt-unverified')
            self.assertEqual(result['file_count'], len(a.REQUIRED))
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                self.assertEqual(set(archive.getnames()), a.REQUIRED | {'manifest.json'})

    def test_changed_digest_or_content_and_incomplete_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            payload = a.build_archive(root, 'release-1', 'built')
            with self.assertRaises(a.ArtifactError):
                a.validate_archive(payload, 'f' * 64)
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                entries = [(entry, archive.extractfile(entry).read()) for entry in archive]
            altered = []
            for entry, data in entries:
                if entry.name == 'apps/web/dist/index.html':
                    data = b'changed'
                    entry.size = len(data)
                altered.append((entry, data))
            changed = self.archive(altered)
            with self.assertRaises(a.ArtifactError):
                a.validate_archive(changed, digest(changed))
            manifest = tarfile.TarInfo('manifest.json')
            data = json.dumps({'format': 'campus-release-v1', 'release_id': 'v1', 'web_build': 'built', 'files': {}}).encode()
            manifest.size = len(data)
            empty = self.archive([(manifest, data)])
            with self.assertRaises(a.ArtifactError):
                a.validate_archive(empty, digest(empty))

    def test_unsafe_paths_links_and_duplicate_files_are_rejected(self):
        for name, kind in [('../escape.py', tarfile.REGTYPE), ('/etc/passwd', tarfile.REGTYPE),
                           ('apps/api/app/../../escape.py', tarfile.REGTYPE),
                           ('apps/api/app/link.py', tarfile.SYMTYPE), ('apps/api/app/hard.py', tarfile.LNKTYPE),
                           ('config.local.toml', tarfile.REGTYPE), ('apps/web/dist/main.js.map', tarfile.REGTYPE)]:
            with self.subTest(name=name):
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = '/etc/passwd' if kind != tarfile.REGTYPE else ''
                payload = self.archive([(entry, b'')])
                with self.assertRaises(a.ArtifactError):
                    a.validate_archive(payload, digest(payload))
        entry = tarfile.TarInfo('apps/api/app/main.py')
        duplicate = self.archive([(entry, b''), (entry, b'')])
        with self.assertRaises(a.ArtifactError):
            a.validate_archive(duplicate, digest(duplicate))

    def test_archive_size_member_size_and_expansion_limits(self):
        entry = tarfile.TarInfo('apps/api/app/main.py')
        entry.size = 100
        payload = self.archive([(entry, b'x' * 100)])
        for name in ('MAX_ARCHIVE', 'MAX_FILE', 'MAX_TOTAL'):
            with self.subTest(limit=name), patch.object(a, name, 10), self.assertRaises(a.ArtifactError):
                a.validate_archive(payload, digest(payload))
        source = a.BoundedReader(io.BytesIO(b'12345'))
        source.remaining = 3
        with self.assertRaises(a.ArtifactError):
            source.read(1000000)

    def test_local_artifact_requires_content_addressed_file_and_rechecks_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            payload = a.build_archive(root, 'release-1', 'built')
            store = root / 'store'
            store.mkdir()
            sha = digest(payload)
            path = store / (sha + '.tar.gz')
            path.write_bytes(payload)
            self.assertEqual(a.local_artifact(sha, store)['release_id'], 'release-1')
            path.write_bytes(b'corrupted')
            with self.assertRaises(a.ArtifactError):
                a.local_artifact(sha, store)
            self.assertIsNone(a.local_artifact('a' * 64, store))
            with self.assertRaises(a.ArtifactError):
                a.local_artifact('../../etc/passwd', store)

    def test_packaging_refuses_missing_frontend_and_invalid_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(a.ArtifactError):
                a.build_archive(root, 'v1', 'built')
            self.fixture(root)
            for release in ('../v1', 'a;id', 'a' * 65):
                with self.assertRaises(a.ArtifactError):
                    a.build_archive(root, release, 'built')
