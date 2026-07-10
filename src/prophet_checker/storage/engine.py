from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_SSL_MODES = frozenset({"disable", "require", "verify-full"})


def ssl_connect_args(mode: str) -> dict[str, str]:
    # asyncpg читає TLS-режим з kwarg `ssl` (libpq-рядок), а не з `?sslmode=` в URL —
    # інакше при rds.force_ssl конект відхиляється. `disable` = без TLS (локаль).
    if mode not in _SSL_MODES:
        raise ValueError(f"unknown db_ssl_mode: {mode!r}")
    if mode == "disable":
        return {}
    return {"ssl": mode}


def make_engine(url: str, ssl_mode: str) -> AsyncEngine:
    return create_async_engine(url, echo=False, connect_args=ssl_connect_args(ssl_mode))
