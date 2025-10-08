from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from common.db import SQLALCHEMY_METADATA, get_sync_engine
import os
# Import model modules so their tables are attached to SQLALCHEMY_METADATA
from common.models import smtp as _m_smtp, send_log as _m_send_log, autoswitch as _m_autoswitch, service as _m_service, short as _m_short, user as _m_user, api_token as _m_api_token, click as _m_click  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLALCHEMY_METADATA

def run_migrations_offline():
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.getenv("DATABASE_URL")
        or context.get_x_argument(as_dictionary=True).get("db_url")
        or "sqlite:///./dev.db"
    )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    engine = get_sync_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
