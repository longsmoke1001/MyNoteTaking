"""Database configuration for notes and users.

Two backends are supported:

* A local SQLite file - the default, used for development and on a normal
  server. It needs no setup beyond a writable directory.
* A hosted PostgreSQL database (Neon, Supabase, or any other provider) - used
  when a connection string is supplied through `DATABASE_URL`. This is what a
  serverless host such as Vercel needs, because its filesystem is read-only and
  nothing survives between invocations.

The hosted database is opt-in. With no connection string set the app keeps using
SQLite and behaves exactly as before.

Translation never touches any of this; it works with or without a database.
"""
import os

# Connection string for the hosted database. Both spellings are accepted because
# Neon and Supabase still hand out the older Heroku-style name in their
# dashboards, and `postgres://` is not understood by SQLAlchemy 2.0.
DATABASE_URL_ENV_VARS = ('DATABASE_URL', 'SQLALCHEMY_DATABASE_URI')

# Serverless hosts set one of these. There, each request is a fresh short-lived
# process, so holding a connection pool open only exhausts the provider's
# connection limit without ever being reused.
SERVERLESS_ENV_VARS = (
    'VERCEL',
    'AWS_LAMBDA_FUNCTION_NAME',
    'FUNCTION_TARGET',
    'K_SERVICE',
)

POSTGRES_SCHEMES = ('postgresql', 'postgres', 'postgresql+psycopg')


class DatabaseConfigError(RuntimeError):
    """Raised when the configured database cannot be used.

    This is deliberately separate from a connection error: it always means the
    setup is wrong (a typo, a missing driver) rather than the database being
    briefly unreachable, so the message can tell the user what to fix.
    """


def _env(environ, name):
    return (environ.get(name) or '').strip()


def is_serverless(environ=None):
    """True when running on a host where the filesystem does not persist."""
    environ = os.environ if environ is None else environ
    return any(_env(environ, name) for name in SERVERLESS_ENV_VARS)


def normalize_uri(uri):
    """Return `uri` with a scheme SQLAlchemy accepts.

    `postgres://` and `postgresql://` both mean plain PostgreSQL over the
    default driver, so the legacy spelling is rewritten rather than rejected.
    """
    uri = (uri or '').strip()
    if uri.startswith('postgres://'):
        return 'postgresql://' + uri[len('postgres://'):]
    return uri


def default_sqlite_uri(root_dir, environ=None):
    """The local SQLite file used when no hosted database is configured.

    A Windows path is not a valid SQLite URL, so backslashes become slashes and
    the path is appended as-is. That yields the right form for each case:
    `sqlite:///C:/notes.db` on Windows, `sqlite:////srv/notes.db` for a POSIX
    absolute path, and `sqlite:///notes.db` for a relative one. Leading
    separators are kept, because dropping them would silently turn an absolute
    path into one relative to the working directory.
    """
    environ = os.environ if environ is None else environ
    path = _env(environ, 'DATABASE_PATH') or os.path.join(root_dir, 'database', 'app.db')
    return 'sqlite:///' + path.replace('\\', '/')


def resolve(root_dir, environ=None):
    """Work out which database to use.

    Returns a dict with the connection `uri`, the `scheme`, whether the backend
    is a local `sqlite` file, and whether the schema should be created at
    startup. Raises DatabaseConfigError when a hosted database is configured
    but its driver is not installed.
    """
    environ = os.environ if environ is None else environ

    configured = ''
    for name in DATABASE_URL_ENV_VARS:
        configured = _env(environ, name)
        if configured:
            break

    if not configured:
        uri = default_sqlite_uri(root_dir, environ)
    else:
        uri = normalize_uri(configured)
        if uri.startswith(POSTGRES_SCHEMES):
            _require_postgres_driver()

    scheme = uri.split('://', 1)[0]
    is_sqlite = scheme.startswith('sqlite')

    # `AUTO_CREATE_TABLES` defaults to on so a fresh Neon or Supabase database
    # works on the first request. Turn it off when the schema is managed by
    # migrations, or when the connection limit is too tight to spare.
    auto_create = _env(environ, 'AUTO_CREATE_TABLES').lower() not in (
        '0', 'false', 'no', 'off',
    )

    return {
        'uri': uri,
        'scheme': scheme,
        'is_sqlite': is_sqlite,
        'auto_create': auto_create,
        # Only a local file needs its directory created before opening it.
        'needs_directory': is_sqlite,
        'serverless': is_serverless(environ),
    }


def sqlite_path(config):
    """The filesystem path behind a SQLite URI, or None for a hosted database.

    Everything after the `sqlite:///` prefix is the path, so a POSIX absolute
    path keeps the leading slash that makes it absolute.
    """
    if not config['is_sqlite']:
        return None
    return config['uri'].split('sqlite:///', 1)[1]


def engine_options(config):
    """Connection options tuned for where the app is running.

    `pool_pre_ping` discards connections the provider has already dropped,
    which is routine on a hosted database. On a serverless host the pool is
    disabled entirely, since every invocation is a new process that would
    otherwise open connections it never reuses.
    """
    if config['serverless']:
        from sqlalchemy.pool import NullPool

        return {'poolclass': NullPool, 'pool_pre_ping': True}
    return {'pool_pre_ping': True}


def _require_postgres_driver():
    """Fail with an actionable message when psycopg is missing."""
    try:
        import psycopg  # noqa: F401
    except ImportError as exc:
        raise DatabaseConfigError(
            'A hosted database is configured but the PostgreSQL driver is not '
            'installed. Run: pip install "psycopg[binary]"'
        ) from exc
