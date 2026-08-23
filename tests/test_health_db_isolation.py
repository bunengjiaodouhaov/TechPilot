from sqlalchemy.pool import NullPool

from app.db.session import engine as application_engine
from app.services.health import _postgres_health_engine


def test_postgres_health_probe_uses_isolated_null_pool() -> None:
    assert _postgres_health_engine is not application_engine
    assert isinstance(_postgres_health_engine.sync_engine.pool, NullPool)
    assert not isinstance(application_engine.sync_engine.pool, NullPool)
