"""Lazy imports keep the original Mock API usable without database extras."""
import os
from functools import lru_cache
from app.settings import SettingsError, database_options

LATEST_REVISION = '0011_work_drafts_lifecycle'


class ConfigUnavailable(Exception):
    pass


@lru_cache
def get_engine():
    url = os.environ.get('DATABASE_URL')
    if 'DATABASE_URL' in os.environ and not url:
        raise ConfigUnavailable('DATABASE_URL 为空；使用配置文件时请清除此环境变量。')
    if url and not url.startswith(('postgresql+psycopg://', 'opengauss+psycopg2://')):
        raise ConfigUnavailable('DATABASE_URL 必须使用 postgresql+psycopg:// 或 opengauss+psycopg2://。')
    try:
        options = None if url else database_options()
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        if options:
            driver = 'opengauss+psycopg2' if options['backend'] == 'opengauss' else 'postgresql+psycopg'
            url = URL.create(driver, username=options['user'], password=options['password'],
                             host=options['host'], port=options['port'], database=options['name'],
                             query={'sslmode': options['sslmode']})
        if str(url).startswith('opengauss+'):
            # Explicit vendor dialect; never pretend an openGauss version is a PostgreSQL version.
            import opengauss_sqlalchemy  # noqa: F401
        return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=0,
                             pool_timeout=5, connect_args={'connect_timeout': 5})
    except SettingsError as exc:
        raise ConfigUnavailable(str(exc)) from exc
    except ImportError as exc:
        raise ConfigUnavailable('缺少数据库依赖；PostgreSQL 安装 requirements.txt，openGauss 安装 requirements-opengauss.txt。') from exc


def get_store(principal=None):
    engine = get_engine()
    from app.modules.config.store import ConfigStore
    return ConfigStore(engine, tenant_id=principal.tenant_id if principal else 'local-demo',
                       actor_id=principal.subject if principal else 'local-admin')
