"""Unit tests for Module 1 PHASE-03 — AI Engine selector + API transport.

Covers all five ai_mode routings, fallback chains, race semantics,
lazy bridge construction, aclose idempotency, the parameterized REST
transport (with a faked HTTP layer), and the runner test-ai endpoint.
No network. No browser.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from services.module_1_feed_observer.ai_engine import AIEngine
from services.module_1_feed_observer.config import M1Settings, M1SettingsStore


class FakeStore:
    """Minimal store stand-in: get() returns the current M1Settings."""

    def __init__(self, settings: M1Settings):
        self._settings = settings

    def get(self) -> M1Settings:
        return self._settings


def _store(**overrides) -> FakeStore:
    return FakeStore(M1Settings(**overrides))


def _engine(store, bridge=None):
    """Returns (engine, bridge, calls) where calls['n'] counts factory hits."""
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return bridge

    eng = AIEngine(store, web_bridge_factory=factory,
                   cancel_event=threading.Event())
    return eng, bridge, calls


class FakeBridge:
    def __init__(self):
        self.ready_calls = 0
        self.closed = False
        self.prompts: list[str] = []

    async def ensure_ready(self):
        self.ready_calls += 1

    async def generate_decision(self, path: str):
        return {"reaction": "LOVE", "comment": "web says hi",
                "confidence": 0.9, "source": "gemini_web"}

    async def close(self):
        self.closed = True


def _api_ok(path, **kw):
    return {"reaction": "LIKE", "comment": "api fast", "confidence": 0.9,
            "source": "gemini_api"}


async def _api_ok_async(path, **kw):
    return _api_ok(path)


async def _api_fail(path, **kw):
    raise RuntimeError("api exploded")


class TestModeRouting(unittest.TestCase):
    def test_api_only_uses_api_never_web(self) -> None:
        eng, bridge, calls = _engine(_store(ai_mode="api_only"), FakeBridge())
        eng._via_api = _api_ok_async  # type: ignore[method-assign]

        async def run():
            await eng.ensure_ready()
            return await eng.decide("x.png")

        d = asyncio.run(run())
        self.assertEqual(calls["n"], 0)        # factory never invoked
        self.assertEqual(d["attempts"], ["api"])
        self.assertEqual(d["source"], "gemini_api")

    def test_web_only_preopens_and_uses_web(self) -> None:
        bridge = FakeBridge()
        eng, _, calls = _engine(_store(ai_mode="web_only"), bridge)

        async def run():
            await eng.ensure_ready()
            return await eng.decide("x.png")

        d = asyncio.run(run())
        self.assertGreaterEqual(calls["n"], 1)
        self.assertGreaterEqual(bridge.ready_calls, 2)  # pre-open + decide
        self.assertEqual(d["attempts"], ["web"])

    def test_api_primary_falls_back_to_web(self) -> None:
        eng, _, _ = _engine(_store(ai_mode="api_primary_web_fallback"), FakeBridge())
        eng._via_api = _api_fail  # type: ignore[method-assign]
        eng._via_web = lambda path: asyncio.sleep(0, result={  # type: ignore[attr-defined]
            "reaction": "LIKE", "comment": "rescued", "confidence": 0.8,
            "source": "gemini_web"})

        async def run():
            return await eng.decide("x.png")

        d = asyncio.run(run())
        self.assertEqual(d["attempts"], ["api", "web"])
        self.assertEqual(d["comment"], "rescued")

    def test_web_primary_falls_back_to_api(self) -> None:
        eng, _, calls = _engine(_store(ai_mode="web_primary_api_fallback"), FakeBridge())
        seen_order: list[str] = []

        async def api_ok(path, **kw):
            seen_order.append("api")
            return _api_ok(path)

        async def web_fail(path):
            seen_order.append("web")
            raise RuntimeError("tab broken")

        eng._via_api = api_ok  # type: ignore[method-assign]
        eng._via_web = web_fail  # type: ignore[method-assign]

        async def run():
            return await eng.decide("x.png")

        d = asyncio.run(run())
        self.assertEqual(seen_order, ["web", "api"])
        self.assertEqual(d["attempts"], ["web", "api"])
        self.assertEqual(calls["n"], 0)  # lazily built only on real web use

    def test_both_engines_failing_raises_aggregated_error(self) -> None:
        eng, _, _ = _engine(_store(ai_mode="api_primary_web_fallback"))

        async def fail(path, **kw):
            raise ValueError("boom-api")

        eng._via_api = fail  # type: ignore[method-assign]

        async def web_fail(path):
            raise OSError("nope")

        eng._via_web = web_fail  # type: ignore[method-assign]

        async def run():
            return await eng.decide("x.png")

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(run())
        msg = str(ctx.exception)
        self.assertIn("All AI engines failed", msg)
        self.assertIn("api:", msg)
        self.assertIn("web:", msg)


class TestRaceMode(unittest.TestCase):
    def _eng(self, store) -> AIEngine:
        eng, _, _ = _engine(store, FakeBridge())
        return eng

    def test_first_success_wins_fast(self) -> None:
        eng = self._eng(_store(ai_mode="race_both"))
        cancelled = {"api": False}

        async def slow_api(path, **kw):
            try:
                await asyncio.sleep(30)
                return _api_ok(path)
            except asyncio.CancelledError:
                cancelled["api"] = True
                raise

        async def quick_web(path):
            await asyncio.sleep(0.01)
            return {"reaction": "LIKE", "comment": "winner-web",
                    "confidence": 0.9, "source": "gemini_web"}

        eng._via_api = slow_api  # type: ignore[method-assign]
        eng._via_web = quick_web  # type: ignore[method-assign]

        async def run():
            started = time.perf_counter()
            d = await eng.decide("x.png")
            await asyncio.sleep(0.05)  # let cancellation callbacks settle
            return d, time.perf_counter() - started

        d, elapsed = asyncio.run(run())
        self.assertEqual(d["comment"], "winner-web")
        self.assertLess(elapsed, 1.0)
        self.assertTrue(cancelled["api"])  # loser task observed cancellation

    def test_race_survives_fast_failure_of_one_engine(self) -> None:
        eng = self._eng(_store(ai_mode="race_both"))

        async def instant_fail(path, **kw):
            raise RuntimeError("dead key")

        async def slow_ok_web(path):
            await asyncio.sleep(0.05)
            return {"reaction": "HAHA", "comment": "late but valid",
                    "confidence": 0.9, "source": "gemini_web"}

        eng._via_api = instant_fail  # type: ignore[method-assign]
        eng._via_web = slow_ok_web  # type: ignore[method-assign]

        async def run():
            return await eng.decide("x.png")

        d = asyncio.run(run())
        self.assertEqual(d["comment"], "late but valid")

    def test_race_all_fail_raises(self) -> None:
        eng = self._eng(_store(ai_mode="race_both"))
        eng._via_api = _api_fail  # type: ignore[method-assign]

        async def web_fail(path):
            raise OSError("down")

        eng._via_web = web_fail  # type: ignore[method-assign]

        async def run():
            return await eng.decide("x.png")

        with self.assertRaises(RuntimeError):
            asyncio.run(run())


class TestLifecycleAndPrompt(unittest.TestCase):
    def test_aclose_idempotent(self) -> None:
        bridge = FakeBridge()
        eng, _, _ = _engine(_store(ai_mode="web_only"), bridge)

        async def run():
            await eng.ensure_ready()   # opens the bridge
            await eng.aclose()
            await eng.aclose()

        asyncio.run(run())
        self.assertTrue(bridge.closed)
        self.assertIsNone(eng._bridge)

    def test_prompt_chokepoint_uses_settings_prompt(self) -> None:
        s = M1Settings(custom_prompt="Say something kind.", structured_reply=True)
        eng, _, _ = _engine(FakeStore(s), FakeBridge())
        p = eng.build_prompt(s)
        self.assertIn("Say something kind.", p)
        self.assertIn('"reaction"', p)


class TestRestTransportV2(unittest.TestCase):
    def test_missing_key_raises_before_network(self) -> None:
        from services.module_1_feed_observer.gemini_api import generate_engagement_v2

        with tempfile.TemporaryDirectory() as td:
            shot = Path(td) / "s.png"
            shot.write_bytes(b"\x89PNG\r\n\x1a\n")

            async def run():
                with mock_env({"GEMINI_API_KEY": ""}):
                    await generate_engagement_v2(str(shot))

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(run())
            self.assertIn("API key", str(ctx.exception))

    def test_happy_path_parses_structured_reply(self) -> None:
        from services.module_1_feed_observer import gemini_api as g

        captured: dict = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": [{"content": {"parts": [
                    {"text": '{"reaction":"WOW","comment":"great shot"}'}
                ]}}]}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None):
                captured["url"] = url
                captured["payload"] = json
                return FakeResponse()

        orig_client = g.httpx.AsyncClient
        g.httpx.AsyncClient = FakeClient
        try:
            with tempfile.TemporaryDirectory() as td:
                shot = Path(td) / "s.png"
                shot.write_bytes(b"\x89PNG\r\n\x1a\n")

                async def run():
                    with mock_env({"GEMINI_API_KEY": "",
                                   "GEMINI_API_MODEL": ""}):
                        return await g.generate_engagement_v2(
                            str(shot),
                            api_key="sk-test-key",
                            model="gemini-test-model",
                            prompt="custom prompt here",
                        )

                d = asyncio.run(run())
        finally:
            g.httpx.AsyncClient = orig_client

        self.assertEqual(d["source"], "gemini_api")
        self.assertEqual(d["reaction"], "WOW")
        self.assertEqual(d["comment"], "great shot")
        self.assertIn("models/gemini-test-model:generateContent", captured["url"])
        self.assertIn("key=sk-test-key", captured["url"])
        self.assertEqual(captured["payload"]["contents"][0]["parts"][0]["text"],
                         "custom prompt here")


class TestRunnerTestAiEndpoint(unittest.TestCase):
    def test_missing_key_reports_not_ok_without_delay(self) -> None:
        from services.module_1_feed_observer.runner import Module1Runner

        with tempfile.TemporaryDirectory() as td:
            runner = Module1Runner(store=M1SettingsStore(path=Path(td) / "r.json"))

            async def run():
                with mock_env({"GEMINI_API_KEY": "", "MOD1_AI_API_KEY": ""}):
                    return await runner.test_ai_engine()

            res = asyncio.run(run())
        self.assertFalse(res["ok"])
        self.assertIn("API key", res["error"])
        self.assertFalse(res["key_configured"])
        # no stray temp file left behind
        self.assertNotIn("m1_ai_test_", json.dumps(res))


class mock_env:
    """Context manager patching os.environ keys (restore afterwards)."""

    def __init__(self, values: dict):
        self.values = values
        self._saved: dict = {}
        self._added: set = set()

    def __enter__(self):
        import os

        for k, v in self.values.items():
            if k in os.environ:
                self._saved[k] = os.environ[k]
            else:
                self._added.add(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        import os

        for k, v in self._saved.items():
            os.environ[k] = v
        for k in self._added:
            os.environ.pop(k, None)
        return False


if __name__ == "__main__":
    unittest.main(verbosity=2)
