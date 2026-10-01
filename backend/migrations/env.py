"""Alembic environment; schema changes are PostgreSQL-only and forward-only."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from reconbench_app.db.metadata import metadata
from reconbench_app.db.migration_config import migration_url
from sqlalchemy import engine_from_config, pool, text

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic config uses %-interpolation for URLs, so escape URL-encoded credentials.
config.set_main_option("sqlalchemy.url", migration_url().replace("%", "%%"))

# Task 2.2 migrations are authoritative SQL copied from the validated Backend
# Schema; this static SQLAlchemy metadata is their drift-comparison contract.
target_metadata = metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str]) -> bool:
    """Compare ReconBench tables while leaving Alembic's version table internal."""
    if type_ == "schema":
        return name in (None, "public")
    if type_ == "table":
        return name != "alembic_version"
    return True


def run_migrations_offline() -> None:
    """Render the fixed-forward SQL without opening a database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply each ordered Schema section in its own transaction."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        role_exists = connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reconbench_migrator')")
        )
        migration_role_owns_version_table = connection.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_class relation "
                "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                "JOIN pg_roles owner ON owner.oid = relation.relowner "
                "WHERE namespace.nspname = 'public' "
                "AND relation.relname = 'alembic_version' "
                "AND owner.rolname = 'reconbench_migrator')"
            )
        )
        if role_exists and migration_role_owns_version_table:
            # Deploy login roles are granted this NOLOGIN group role separately.
            # Fresh installs can share a PostgreSQL cluster with another
            # ReconBench database, so role existence alone is not sufficient:
            # switch only after revision 0015 transferred this database's
            # Alembic version table to the group role.
            connection.exec_driver_sql("SET ROLE reconbench_migrator")
        # The revision probe above starts SQLAlchemy's implicit transaction.
        # End it before Alembic establishes its per-revision transaction.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_name=include_name,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
