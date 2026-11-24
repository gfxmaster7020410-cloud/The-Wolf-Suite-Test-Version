"""
Module 1 — AI Engine facade (PHASE-03, decisions D2/D3/D4).

Single entry point used by Module1Worker to obtain an engagement decision
({reaction, comment, confidence, source, attempts}) for a feed-post
screenshot. Routes between the Gemini REST API (instant) and the Gemini
Web UI (browser tab) according to settings.ai_mode:

    api_only                  REST every post
    web_only                  Web tab every post
    api_primary_web_fallback  REST first, Web rescue   (DEFAULT)
    web_primary_api_fallback  Web first, REST rescue
    race_both                 concurrent; first SUCCESS wins, loser cancelled

Design rules:
  - Settings are re-read on EVERY decision (dashboard edits apply live).
  - The Web bridge is constructed lazily so pure-API users never pay the
    tab cost; ensure_ready() pre-opens it only for modes that need it.
  - race_both cancels the losing task best-effort; a stray draft left in
    the Gemini tab is harmless (fresh chat clears it next post).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, List, Optional

from services.module_1_feed_observer.config import M1SettingsStore

_WEB_MODES = {"web_only", "web_primary_api_fallback", "race_both"}


class AIEngine:
    """Mode-dispatching facade over [gemini_api | GeminiWebBridge]."""

    def __init__(
        self,
        store: M1SettingsStore,
        web_bridge_factory: Callable[[], Any],
        cancel_event: Optional[threading.Event] = None,
    ):
        self.store = store
        self._bridge_factory = web_bridge_factory
        self._bridge: Optional[Any] = None
        self.cancel_event = cancel_event

    # ------------------------------------------------------------------ api
    async def ensure_ready(self) -> None:
        """Pre-open the Web tab only when the selected mode can ever use it."""
        s = self.store.get()
        if s.ai_mode in _WEB_MODES:
            if self._bridge is None:
                self._bridge = self._bridge_factory()
            await self._bridge.ensure_ready()

    async def decide(self, screenshot_path: str) -> Dict[str, Any]:
        mode = self.store.get().ai_mode
        if mode == "api_only":
            decision = await self._via_api(screenshot_path)
            decision["attempts"] = ["api"]
            return decision
        if mode == "web_only":
            decision = await self._via_web(screenshot_path)
            decision["attempts"] = ["web"]
            return decision
        if mode == "race_both":
            return await self._race(screenshot_path)
        order = ("api", "web") if mode == "api_primary_web_fallback" else ("web", "api")
        return await self._fallback_chain(order, screenshot_path)

    async def aclose(self) -> None:
        """Close the Web bridge if one was opened; idempotent."""
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            try:
                await bridge.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ modes
    async def _fallback_chain(
        self, order: tuple, path: str
    ) -> Dict[str, Any]:
        errors: List[str] = []
        attempts: List[str] = []
        for which in order:
            try:
                fn = self._via_api if which == "api" else self._via_web
                decision = await fn(path)
                decision["attempts"] = attempts + [which]
                return decision
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(f"{which}: {type(exc).__name__}: {exc}")
                attempts.append(which)
        raise RuntimeError("All AI engines failed :: " + " | ".join(errors))

    async def _race(self, path: str) -> Dict[str, Any]:
        pending = {
            asyncio.create_task(self._safe(self._via_api(path))),
            asyncio.create_task(self._safe(self._via_web(path))),
        }
        winner: Optional[Dict[str, Any]] = None
        try:
            while pending and winner is None:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    result = task.result()
                    if isinstance(result, dict) and winner is None:
                        winner = result
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if winner is None:
            raise RuntimeError("race_both: no engine returned a valid decision")
        return winner

    # ------------------------------------------------------------------ transports
    async def _safe(self, coro) -> Optional[Dict[str, Any]]:
        try:
            result = await coro
            return result if isinstance(result, dict) else None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _via_api(self, path: str) -> Dict[str, Any]:
        from services.module_1_feed_observer.gemini_api import generate_engagement_v2

        s = self.store.get()
        return await generate_engagement_v2(
            path,
            api_key=(s.gemini_api_key or None),
            model=(s.gemini_api_model or None),
            prompt=self.build_prompt(s),
            structured=True,
            strip_tags=not s.allow_hashtags,
        )

    async def _via_web(self, path: str) -> Dict[str, Any]:
        if self._bridge is None:
            self._bridge = self._bridge_factory()
        await self._bridge.ensure_ready()
        s = self.store.get()
        self._bridge.structured = True
        self._bridge.prompt = self.build_prompt(s)
        self._bridge.strip_tags = not s.allow_hashtags
        return await self._bridge.generate_decision(path)

    # ------------------------------------------------------------------ prompt
    def build_prompt(self, s) -> str:
        """Single prompt choke-point — PHASE-10 extends this."""
        return s.prompt()
