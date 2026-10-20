"""Review-only deployment plans. No shell commands or execution credentials are retained."""
import hashlib
import json
from app.errors import APIError
from app.modules.deployment.preflight import assess_preflight


def build_plan(command, inspection: dict, artifact=None):
    if inspection.get('status') != 'checked' or inspection.get('authenticated') is not True:
        raise APIError(409, 'DEPLOYMENT_PREFLIGHT_REQUIRED', '必须先通过指纹验证、SSH 认证及受支持的 Ubuntu 检查。')
    checks = {item['name']: item['ok'] for item in inspection['checks']}
    if not checks.get('systemd'):
        raise APIError(409, 'DEPLOYMENT_SYSTEMD_REQUIRED', '此部署模板要求 systemd，请先检查目标环境。')
    root = '/opt/campus-ai'
    dependencies = [name for name in ('python3', 'nginx') if not checks.get(name)]
    preflight = assess_preflight(command.operation, inspection.get('deployment_facts'), artifact)
    if artifact is None or artifact.get('web_build') != 'built':
        preflight['blockers'].append({'code': 'BUILD_UNVERIFIED', 'message': '产物未通过打包前完整构建，需重新构建。'})
    if dependencies:
        preflight['blockers'].append({'code': 'DEPENDENCIES_MISSING', 'message': '缺少运行依赖：' + '、'.join(dependencies) + '，需确认安装后重新检查。'})
    steps = [
        {'id': 'backup', 'description': '确认数据库与本机配置备份及恢复负责人；数据库不自动逆向回滚。', 'requires_sudo': False},
        {'id': 'directories', 'description': f'校验 {root} 及父目录的所有权、符号链接和现有版本；冲突时停止。', 'requires_sudo': False},
    ]
    if dependencies:
        steps.append({'id': 'dependencies', 'description': '经单独确认后安装缺失依赖：' + '、'.join(dependencies) + '；Python 虚拟环境支持需另行检查。', 'requires_sudo': True})
    steps.extend([
        {'id': 'artifact', 'description': '校验实际构建产物摘要并安全解包到新版本目录；拒绝路径穿越、链接及已有版本覆盖。', 'requires_sudo': False},
        {'id': 'configuration', 'description': '检查独立保存的数据库、认证及运行配置；不从本地开发配置复制密码或启用演示认证。', 'requires_sudo': False},
        {'id': 'services', 'description': '首次安装 systemd/Nginx 服务配置。' if command.operation == 'install' else '比对现有 systemd/Nginx 配置；变更需单独确认。', 'requires_sudo': True},
        {'id': 'migration', 'description': '备份确认后执行增量迁移；失败停止切换，数据库恢复由操作者按备份方案处理。', 'requires_sudo': False},
        {'id': 'activation', 'description': '新版本健康检查通过后切换 current 并重启服务；失败恢复原代码指向，再验证健康状态。', 'requires_sudo': True},
    ])
    plan = {
        'kind': 'deployment-review-plan-v1', 'operation': command.operation,
        'target': {'host': command.host, 'port': command.port, 'username': command.username,
                   'fingerprint': inspection['fingerprint']},
        'install_root': root, 'release_id': command.release_id,
        'release_directory': f'{root}/releases/{command.release_id}',
        'artifact_sha256': command.artifact_sha256,
        'artifact': artifact,
        'preflight': preflight,
        'missing_dependencies': dependencies, 'steps': steps,
        'execution_available': False,
        'limitations': [
            '本计划仅供审阅，不是执行授权；执行前必须重新检查主机、权限、路径、产物及计划内容。',
            ('本地产物归档与逐文件摘要已验证；尚未上传服务器，依赖安装与实际运行仍未验证。'
             if artifact else '尚未上传或验证实际构建产物，填写摘要不代表产物已就绪。'),
            ('前端使用已有 dist，未验证构建是否对应当前源码，执行前必须重新完整构建。'
             if artifact and artifact['web_build'] != 'built' else '生产环境配置及部署后的健康检查尚待验证。'),
            '目录、权限、磁盘、venv 模块、端口及服务状态检查仅是当前快照；sudo 和实际服务配置仍需另行验证。',
            '文件传输、安装、迁移、服务切换及恢复执行器尚未实现。',
        ],
    }
    plan['digest'] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan
