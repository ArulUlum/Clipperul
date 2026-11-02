import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
import app.models  # noqa: F401 — register all models on Base.metadata


@pytest.fixture(autouse=True)
def no_zernio_key(monkeypatch):
    """A real key in the developer's .env must never route a test through Zernio — tests that need
    it set it themselves."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ZERNIO_API_KEY", None)
    monkeypatch.setattr(settings, "ZERNIO_PROFILE_ID", None)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
