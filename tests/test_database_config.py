"""Tests for database configuration.

Pure configuration logic, so these run without touching a real database. The
cases that matter in practice are the ones a user hits when wiring up Neon or
Supabase: the legacy `postgres://` scheme, an unset connection string, and the
serverless connection pooling.
"""
import pytest

from src import database as database_config


# --- picking a backend -----------------------------------------------------


def test_defaults_to_local_sqlite():
    config = database_config.resolve('/app', environ={})

    assert config['is_sqlite'] is True
    assert config['scheme'] == 'sqlite'
    assert config['uri'] == 'sqlite:////app/database/app.db'
    assert config['needs_directory'] is True


def test_hosted_postgres_is_opt_in():
    config = database_config.resolve(
        '/app', environ={'DATABASE_URL': 'postgresql://u:p@db.neon.tech/notes'}
    )

    assert config['is_sqlite'] is False
    assert config['scheme'] == 'postgresql'
    assert config['needs_directory'] is False


def test_legacy_postgres_scheme_is_accepted():
    """Neon and Supabase still hand out `postgres://`, which SQLAlchemy rejects."""
    config = database_config.resolve(
        '/app', environ={'DATABASE_URL': 'postgres://u:p@db.supabase.co/postgres'}
    )

    assert config['uri'] == 'postgresql://u:p@db.supabase.co/postgres'
    assert config['scheme'] == 'postgresql'


def test_sqlalchemy_uri_name_is_also_honoured():
    config = database_config.resolve(
        '/app', environ={'SQLALCHEMY_DATABASE_URI': 'postgresql://u:p@host/db'}
    )

    assert config['scheme'] == 'postgresql'


def test_database_url_wins_over_the_alias():
    config = database_config.resolve('/app', environ={
        'DATABASE_URL': 'postgresql://u:p@primary/db',
        'SQLALCHEMY_DATABASE_URI': 'postgresql://u:p@secondary/db',
    })

    assert config['uri'] == 'postgresql://u:p@primary/db'


def test_blank_value_falls_back_to_sqlite():
    config = database_config.resolve('/app', environ={'DATABASE_URL': '   '})

    assert config['is_sqlite'] is True


def test_windows_path_is_not_mistaken_for_a_url():
    """A Windows path has no scheme, so it must be escaped, not prefixed."""
    config = database_config.resolve('C:/code/app', environ={
        'DATABASE_PATH': r'C:\code\app\database\app.db',
    })

    assert config['uri'] == 'sqlite:///C:/code/app/database/app.db'
    assert config['is_sqlite'] is True


# --- schema creation -------------------------------------------------------


@pytest.mark.parametrize('value', ['0', 'false', 'no', 'off', 'OFF'])
def test_auto_create_can_be_switched_off(value):
    """Migrations-managed schemas should not run create_all on every cold start."""
    config = database_config.resolve('/app', environ={'AUTO_CREATE_TABLES': value})

    assert config['auto_create'] is False


def test_auto_create_is_on_by_default():
    """A fresh Neon or Supabase database has no tables until they are created."""
    assert database_config.resolve('/app', environ={})['auto_create'] is True


# --- pooling ---------------------------------------------------------------


def test_serverless_disables_connection_pooling():
    config = database_config.resolve(
        '/app', environ={'VERCEL': '1', 'DATABASE_URL': 'postgresql://u:p@h/db'}
    )

    assert config['serverless'] is True
    assert database_config.engine_options(config)['poolclass'].__name__ == 'NullPool'


def test_local_development_keeps_the_pool():
    config = database_config.resolve('/app', environ={})

    assert config['serverless'] is False
    assert 'poolclass' not in database_config.engine_options(config)


def test_pool_pre_ping_is_always_on():
    """Hosted providers drop idle connections, which otherwise surface as errors."""
    for environ in ({}, {'VERCEL': '1'}):
        options = database_config.engine_options(
            database_config.resolve('/app', environ=environ)
        )
        assert options['pool_pre_ping'] is True


# --- recovering the file path from the URI ---------------------------------


@pytest.mark.parametrize('root,expected', [
    ('/srv/app', '/srv/app/database/app.db'),      # POSIX absolute
    ('C:/code/app', 'C:/code/app/database/app.db'),  # Windows absolute
])
def test_sqlite_path_keeps_absolute_paths(root, expected):
    """Startup creates the parent directory, so it needs the real path back.

    Losing a leading separator here would create the database somewhere other
    than the one the URI points at.
    """
    config = database_config.resolve(root, environ={})

    assert database_config.sqlite_path(config) == expected


def test_sqlite_path_is_none_for_a_hosted_database():
    config = database_config.resolve(
        '/app', environ={'DATABASE_URL': 'postgresql://u:p@h/db'}
    )

    assert database_config.sqlite_path(config) is None


# --- driver ----------------------------------------------------------------


def test_missing_postgres_driver_is_reported_clearly(monkeypatch):
    """A missing driver should name the fix, not surface as an obscure error."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'psycopg':
            raise ImportError('no psycopg')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)

    with pytest.raises(database_config.DatabaseConfigError) as error:
        database_config.resolve('/app', environ={'DATABASE_URL': 'postgresql://u:p@h/db'})

    assert 'psycopg' in str(error.value)


def test_config_error_is_not_raised_for_sqlite():
    """Local development must never depend on the Postgres driver."""
    config = database_config.resolve('/app', environ={})

    assert config['is_sqlite'] is True
