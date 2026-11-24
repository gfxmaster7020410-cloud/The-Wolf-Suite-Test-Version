"""Shared Wolf Suite utilities.

Import submodules directly when possible to avoid circular imports, e.g.:
  from services.shared.sqlite_store import store
  from services.shared.humanization import humanization
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SafetyBridge",
    "safety_bridge",
    "WolfSQLiteStore",
    "store",
    "HumanizationEngine",
    "HumanizationProfile",
    "humanization",
    "get_redis",
    "redis_backend",
    "reset_redis_client",
]


def __getattr__(name: str) -> Any:
    if name in {"SafetyBridge", "safety_bridge"}:
        from .safety_bridge import SafetyBridge, safety_bridge

        return SafetyBridge if name == "SafetyBridge" else safety_bridge
    if name in {"WolfSQLiteStore", "store"}:
        from .sqlite_store import WolfSQLiteStore, store

        return WolfSQLiteStore if name == "WolfSQLiteStore" else store
    if name in {"HumanizationEngine", "HumanizationProfile", "humanization"}:
        from .humanization import HumanizationEngine, HumanizationProfile, humanization

        return {
            "HumanizationEngine": HumanizationEngine,
            "HumanizationProfile": HumanizationProfile,
            "humanization": humanization,
        }[name]
    if name in {"get_redis", "redis_backend", "reset_redis_client"}:
        from . import redis_client

        return getattr(redis_client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
