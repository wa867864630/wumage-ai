"""Generate version relationships without publishing code, configurations or credentials."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

from baseline import ROOT, canonical, digest, git_state, inventory


def report(database=False):
    files = inventory()
    declared = json.loads((ROOT / 'version.json').read_text(encoding='utf-8'))
    sys.path.insert(0, str(ROOT / 'apps/api'))
    from app.db import LATEST_REVISION
    from app.modules.config.type_registry import course_type
    from app.modules.forms.adapter import form_type, enabled as forms_enabled
    from app.modules.approvals.adapter import approval_type, enabled as approvals_enabled
    from app.modules.unified_editor.runtime import unified_type
    installed = course_type()
    installed_types = [(installed, True, 'manifest/schema/manual implementation version; not implementation bytes'),
        (form_type(), forms_enabled(), 'reviewed LF-normalized type source/schema inputs; shared platform covered by implementation_digest'),
        (approval_type(), approvals_enabled(), 'reviewed LF-normalized approval inputs and form type dependency; shared platform covered by implementation_digest'),
        (unified_type('platform.form'), forms_enabled(), 'immutable v1.1 adapter and legacy dependency; shared platform-editor@1 covered by implementation_digest'),
        (unified_type('platform.leave-approval'), approvals_enabled(), 'immutable v1.1 adapter and legacy dependency; shared platform-editor@1 covered by implementation_digest')]
    implementation = {name: entry for name, entry in files.items() if name.startswith(
        ('apps/api/app/', 'apps/web/src/', 'packages/contracts/')) or name.endswith(
        ('requirements.txt', 'requirements-opengauss.txt', 'pnpm-lock.yaml'))}
    result = {'format': 'campus-baseline-evidence-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'declared': declared, 'source': {'git': git_state(), 'file_count': len(files),
            'source_digest': digest(canonical(files)), 'implementation_digest': digest(canonical(implementation))},
        'database': {'expected_revision': LATEST_REVISION, 'observed': False},
        'contracts': {name: entry['sha256'] for name, entry in files.items() if name.startswith('packages/contracts/')},
        'dependencies': {name: entry['sha256'] for name, entry in files.items() if name.endswith(
            ('pnpm-lock.yaml', 'requirements.txt', 'requirements-opengauss.txt'))},
        'types': [{'type_id': item['type_id'], 'type_version': item['type_version'], 'creation_enabled': enabled,
            'registered_digest': item['type_digest'], 'registered_digest_scope': scope} for item, enabled, scope in installed_types],
        'production_ready': False,
        'limitations': ['Not a production release or database backup',
            'Implementation evidence does not alter existing type digests or authorize a type upgrade',
            'Full production build and manual browser acceptance require independent evidence']}
    if declared['database_revision'] != LATEST_REVISION:
        raise ValueError('Declared and application database versions disagree')
    if database:
        import sqlalchemy as sa
        from app.db import get_engine
        from app.modules.config.store import state, releases
        from app.modules.config.application_store import applications
        with get_engine().begin() as connection:
            connection.execute(sa.text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            revision = connection.scalar(sa.text('SELECT version_num FROM alembic_version'))
            rows = connection.execute(sa.select(state.c.tenant_id, state.c.application_id, state.c.revision,
                state.c.current_release_id, state.c.draft)).mappings().all()
            apps = connection.execute(sa.select(applications.c.tenant_id, applications.c.application_id,
                applications.c.type_id, applications.c.type_version, applications.c.type_digest)).mappings().all()
            versions = connection.execute(sa.select(releases.c.id, releases.c.tenant_id,
                releases.c.application_id, releases.c.content_hash, releases.c.runtime_binding)).mappings().all()
        result['database'] = {'expected_revision': LATEST_REVISION, 'actual_revision': revision,
            'observed': True, 'matches_expected': revision == LATEST_REVISION,
            'applications': [{'tenant_scope_hash': digest(row['tenant_id'].encode()),
                'application_id': row['application_id'], 'draft_revision': row['revision'],
                'draft_hash': digest(canonical(row['draft'])), 'current_release_id': row['current_release_id']} for row in rows],
            'registered_instances': [{'tenant_scope_hash': digest(row['tenant_id'].encode()),
                **{key: value for key, value in row.items() if key != 'tenant_id'}} for row in apps],
            'releases': [{'tenant_scope_hash': digest(row['tenant_id'].encode()),
                **{key: value for key, value in row.items() if key != 'tenant_id'}} for row in versions]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', action='store_true', help='Read only application/version metadata; never model settings')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        value = report(args.database)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(canonical(value) + b'\n')
            print(json.dumps({'status': 'passed', 'file': str(args.output),
                'source_digest': value['source']['source_digest'], 'production_ready': False}))
        else:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'message': 'Version evidence could not be collected'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
