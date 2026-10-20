"""Build and package a release without local credentials; no remote operations."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api'))
from app.modules.deployment.artifacts import ArtifactError, STORE, build_archive, local_artifact, validate_archive, RELEASE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--use-existing-web-dist', action='store_true',
                        help='Review only: package existing dist, explicitly mark its build unverified.')
    parser.add_argument('--verify-only', action='store_true', help='Treat --release as an archive SHA256 and verify it.')
    args = parser.parse_args()
    try:
        if args.verify_only:
            report = local_artifact(args.release)
            if report is None:
                raise ArtifactError('未找到本地产物。')
        else:
            if not RELEASE.fullmatch(args.release):
                raise ArtifactError('版本号只允许小写字母、数字和连字符，最多 64 字符。')
            if not args.use_existing_web_dist:
                pnpm = shutil.which('pnpm')
                if not pnpm:
                    raise ArtifactError('未找到 pnpm，请先安装前端构建工具。')
                result = subprocess.run([pnpm, 'build'], cwd=ROOT)
                if result.returncode:
                    raise ArtifactError('前端构建失败，未生成产物。')
            payload = build_archive(ROOT, args.release, 'prebuilt-unverified' if args.use_existing_web_dist else 'built')
            digest = hashlib.sha256(payload).hexdigest()
            report = validate_archive(payload, digest)
            STORE.mkdir(parents=True, exist_ok=True)
            target = STORE / (digest + '.tar.gz')
            try:
                with target.open('xb') as output:
                    output.write(payload)
            except FileExistsError:
                if local_artifact(digest) != report:
                    raise ArtifactError('已有同名产物校验失败，未覆盖。') from None
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ArtifactError, OSError):
        # Never print raw paths or arbitrary exception bodies.
        print('产物生成或校验失败；请检查版本号、构建结果、文件权限和归档完整性。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
