import unittest

from app.errors import APIError
from app.modules.config.type_registry import ExactTypeRegistry, TypeBundle, require_type, course_type
from app.modules.forms.adapter import form_type
from app.modules.approvals.adapter import approval_type


class ExactRegistryTests(unittest.TestCase):
    def bundle(self, version, fingerprint):
        return {'type_id': 'test.reviewed', 'type_version': version, 'type_digest': fingerprint * 64,
                'installed': True, 'metadata': {'name': version}}

    def test_two_versions_coexist_without_latest_fallback(self):
        registry = ExactTypeRegistry([TypeBundle('test.reviewed', version, lambda version=version, key=key: self.bundle(version, key))
                                      for version, key in [('1.0.0', 'a'), ('2.0.0', 'b')]])
        self.assertEqual(registry.require('test.reviewed', '1.0.0', 'a' * 64)['metadata']['name'], '1.0.0')
        self.assertEqual(registry.require('test.reviewed', '2.0.0', 'b' * 64)['metadata']['name'], '2.0.0')
        for version in ('latest', '1', '1.1.0', ''):
            with self.assertRaises(APIError) as caught:
                registry.require('test.reviewed', version)
            self.assertEqual(caught.exception.code, 'APPLICATION_TYPE_UNAVAILABLE')
        with self.assertRaises(APIError) as caught:
            registry.require('test.reviewed', '1.0.0', 'b' * 64)
        self.assertEqual(caught.exception.code, 'APPLICATION_TYPE_CHANGED')

    def test_duplicate_registration_never_shadows_old_version(self):
        with self.assertRaises(APIError) as caught:
            ExactTypeRegistry([TypeBundle('test.reviewed', '1.0.0', lambda: self.bundle('1.0.0', key)) for key in ('a', 'b')])
        self.assertEqual(caught.exception.code, 'TYPE_REGISTRY_DUPLICATE')

    def test_provider_identity_and_digest_are_checked(self):
        for patch in ({'type_id': 'other'}, {'type_version': '2.0.0'}, {'installed': False}, {'type_digest': ''}, {'type_digest': 'z' * 64}):
            registry = ExactTypeRegistry([TypeBundle('test.reviewed', '1.0.0', lambda patch=patch: {**self.bundle('1.0.0', 'a'), **patch})])
            with self.assertRaises(APIError) as caught:
                registry.require('test.reviewed', '1.0.0')
            self.assertEqual(caught.exception.code, 'TYPE_BUNDLE_INVALID')

    def test_returned_metadata_cannot_mutate_a_registered_bundle(self):
        original = self.bundle('1.0.0', 'a')
        registry = ExactTypeRegistry([TypeBundle('test.reviewed', '1.0.0', lambda: original)])
        registry.require('test.reviewed', '1.0.0')['metadata']['name'] = 'changed'
        self.assertEqual(registry.require('test.reviewed', '1.0.0')['metadata']['name'], '1.0.0')

    def test_existing_three_bundles_keep_their_original_metadata_and_digest(self):
        for loader in (course_type, form_type, approval_type):
            original = loader()
            self.assertEqual(require_type(original['type_id'], original['type_version'], original['type_digest']), original)
