"""A small registered application catalog, not an executable application marketplace."""
from fastapi import APIRouter, Request
from app.modules.identity.auth import current_user
from app.modules.config.routes import published_config
from app.modules.config.schema import DisplayConfig
from app.errors import APIError

router = APIRouter(prefix='/api/v1', tags=['应用目录'])
PLANNED = [('leave', '↗', '请假申请', '校园事务'), ('purchase', '▣', '采购申请', '校园事务'),
           ('visit', '⌘', '访问申请', '校园事务'), ('venue', '◈', '场地预约', '校园生活'),
           ('events', '✧', '活动管理', '校园生活')]


def require_application(content, principal):
    application = DisplayConfig.model_validate(content).application
    if not set(application.visible_roles).intersection(principal.roles):
        raise APIError(403, 'APPLICATION_FORBIDDEN', '当前角色未获得此应用的使用权限。')


@router.get('/applications')
def catalog(request: Request):
    principal = current_user(request)
    published = published_config(principal)
    application = DisplayConfig.model_validate(published['content']).application
    apps = []
    from app.settings import database_is_configured
    active = True
    if database_is_configured():
        from app.modules.config.routes import execute
        from app.modules.config.lifecycle import status
        from app.modules.config.store import LEGACY_APPLICATION_ID
        def original_active(store):
            with store.engine.connect() as connection:
                return status(connection, store.tenant_id, LEGACY_APPLICATION_ID)['status'] == 'active'
        active = execute(original_active, principal)
    if active and set(application.visible_roles).intersection(principal.roles):
        apps.append({'id': 'schedule', 'icon': '▦', 'name': application.name,
                     'category': application.category, 'desc': application.description, 'ready': True})
    from app.settings import database_is_configured
    if database_is_configured():
        from app.modules.config.routes import execute
        from app.modules.config.application_store import ApplicationStore
        apps.extend(execute(lambda store: ApplicationStore(store).public_catalog(principal), principal))
    apps.extend({'id': key, 'icon': icon, 'name': name, 'category': category,
                 'desc': '规划中的校园微服务，业务能力尚未接入。', 'ready': False}
                for key, icon, name, category in PLANNED)
    return {'applications': apps, 'release_id': published['release_id']}
