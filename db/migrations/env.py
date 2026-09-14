import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
database_url = os.environ.get("DATABASE_URL", "postgresql+psycopg://rag:rag@localhost:5432/rag")
# ConfigParser interpolation treats % specially (e.g. URL-encoded passwords).
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

# Migrations are hand-written SQL/DDL; there is no ORM metadata to autogenerate from.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
