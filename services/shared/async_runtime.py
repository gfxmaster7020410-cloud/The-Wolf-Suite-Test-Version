"""
Windows-safe async runtime helpers for Playwright under uvicorn.

On Windows, uvicorn often runs a SelectorEventLoop that cannot spawn
subprocesses → Playwright raises NotImplementedError. Running browser jobs
in a dedicated thread with ProactorEventLoopPolicy fixes this.

IMPORTANT: create the coroutine *inside* the worker thread (factory pattern).
A coroutine object bound to uvicorn's loop cannot be driven by asyncio.run().
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Callable, Coroutine, TypeVar

T = TypeVar("T")


def run_coro_playwright_safe(coro: Coroutine[Any, Any, T]) -> T:
    """
    Execute an async coroutine in a fresh event loop that supports subprocesses.
    Safe to call from a worker thread (not from a running event loop thread).
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(coro)


async def await_playwright_job(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """
    Run `factory()` on a Proactor-safe worker thread and await the result.

    Example:
        result = await await_playwright_job(
            lambda: run_module_visual("m1", preview_session_id=sid)
        )
    """
    loop = asyncio.get_running_loop()

    def _target() -> T:
        return run_coro_playwright_safe(factory())

    return await loop.run_in_executor(None, _target)
