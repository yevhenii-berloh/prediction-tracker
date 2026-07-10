import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prophet_checker.storage.engine import make_engine, ssl_connect_args


def test_ssl_connect_args_disable_returns_empty():
    assert ssl_connect_args("disable") == {}


def test_ssl_connect_args_require_passes_ssl_string():
    assert ssl_connect_args("require") == {"ssl": "require"}


def test_ssl_connect_args_verify_full_passes_ssl_string():
    assert ssl_connect_args("verify-full") == {"ssl": "verify-full"}


def test_ssl_connect_args_unknown_mode_raises():
    with pytest.raises(ValueError):
        ssl_connect_args("banana")


def test_make_engine_returns_async_engine_without_connecting():
    engine = make_engine("postgresql+asyncpg://u:p@localhost:5432/db", "disable")
    assert isinstance(engine, AsyncEngine)
    assert engine.url.database == "db"
