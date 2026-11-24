"""Unit tests for Module 1 PHASE-02 — indefinite login waits.

Verifies decision D6: Facebook and Google sign-in walls block FOREVER for
the human, with only the dashboard Stop (threading.Event) aborting; the
positive-millisecond env values remain honored as CI/headless timeouts.
No real browser is launched — Playwright page objects are faked.
"""

from __future__ import annotations

import asyncio
import os
import threading
import unittest
from unittest import mock

from services.module_1_feed_observer.bot import FacebookAutoEngagementBot
from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.fb import FacebookAutomation
from services.module_1_feed_observer.gemini_bridge import GeminiWebBridge
from services.module_1_feed_observer.worker import Module1Worker


# ---------------------------------------------------------------- fakes
class FakeLocator:
    def __init__(self, page):
        self._page = page

    async def count(self) -> int:
        counts = self._page.counts
        if len(counts) > 1:
            return counts.pop(0)
        return counts[0]


class FakeFbPage:
    """count() consumes scripted values; last value repeats forever.
    NOTE: Playwright's Page.is_closed() is SYNC — keep this fake sync too."""

    def __init__(self, counts):
        self.counts = list(counts)

    def is_closed(self) -> bool:
        return False

    def locator(self, selector):
        return FakeLocator(self)


class FakeSubLocator:
    def __init__(self, visible):
        self._visible = visible

    async def count(self) -> int:
        return 1

    def nth(self, i):
        return self

    async def is_visible(self) -> bool:
        return self._visible


class FakeGeminiPage:
    """Always shows a visible email input => _needs_sign_in stays True."""

    def __init__(self):
        self.url = ""

    def locator(self, selector):
        return FakeSubLocator(True)


# ------------------------------------------------------- facebook waits
class TestFacebookLoginWait(unittest.TestCase):
    def _make(self, counts) -> FacebookAutomation:
        fb = FacebookAutomation(M1Settings())
        fb.page = FakeFbPage(counts)
        fb.cancel_event = threading.Event()
        return fb

    def test_cancel_event_breaks_indefinite_wait(self) -> None:
        fb = self._make([0])  # endless "no feed"
        ev: threading.Event = fb.cancel_event
        ev.set()

        async def run():
            await fb._wait_login_if_needed()

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(run())
        self.assertIn("Cancelled", str(ctx.exception))

    def test_login_detection_resumes_indefinite_wait(self) -> None:
        # pre-check(0) -> poll(0) -> poll(1 = feed found)
        fb = self._make([0, 0, 1])

        async def run():
            await fb._wait_login_if_needed()

        asyncio.run(run())  # must return, never raise

    def test_positive_env_ms_still_times_out(self) -> None:
        fb = self._make([0])  # endless "no feed"

        async def run():
            with mock.patch.dict(os.environ, {"FB_LOGIN_WAIT_MS": "30"}):
                await fb._wait_login_if_needed()

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(run())
        self.assertIn("wait window", str(ctx.exception))

    def test_default_env_means_forever_no_deadline_raise(self) -> None:
        # With env unset and cancel set on a later thread tick, the loop
        # MUST still be inside the wait (no immediate timeout raise).
        fb = self._make([0])
        ev: threading.Event = fb.cancel_event

        def stop_soon():
            ev.set()

        t = threading.Timer(0.2, stop_soon)

        async def run():
            t.start()
            try:
                await fb._wait_login_if_needed()
            finally:
                t.cancel()

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(run())
        self.assertIn("Cancelled", str(ctx.exception))


# -------------------------------------------------------- gemini waits
class TestGeminiLoginWait(unittest.TestCase):
    def test_positive_env_ms_times_out_and_emits_login_wait_only(self) -> None:
        events: list[str] = []

        async def on_phase(p: str) -> None:
            events.append(p)

        bridge = GeminiWebBridge(context=None, on_phase=on_phase)
        page = FakeGeminiPage()

        async def run():
            with mock.patch.dict(os.environ, {"GEMINI_LOGIN_WAIT_MS": "50"}):
                await bridge._wait_for_google_login_if_needed(page)

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(run())
        self.assertIn("timed out", str(ctx.exception))
        self.assertIn("gemini_login_wait", events)
        self.assertNotIn("gemini_ok", events)  # never resolved

    def test_zero_env_means_unlimited_deadline_disabled(self) -> None:
        # Cancel immediately after entering the core loop: proves no
        # deadline path raises a *timeout* error when env is "0"/unset.
        cancel = threading.Event()
        bridge = GeminiWebBridge(context=None, cancel_event=cancel)
        bridge.on_phase = None
        page = FakeGeminiPage()
        cancel.set()

        async def run():
            with mock.patch.dict(os.environ, {"GEMINI_LOGIN_WAIT_MS": "0"}):
                await bridge._wait_for_google_login_if_needed(page)

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(run())
        self.assertIn("cancelled", str(ctx.exception).lower())


# -------------------------------------------------- worker phase wiring
class TestWorkerPhaseMessaging(unittest.TestCase):
    def _worker(self) -> Module1Worker:
        w = Module1Worker.__new__(Module1Worker)
        w._lock = threading.Lock()
        w._snapshot = {"phase": "gemini", "phase_detail": ""}
        w._phase_before_gemini_login = ""
        recorded = []

        async def recorder(phase, detail=None):
            recorded.append((phase, detail))

        w._set_phase = recorder  # type: ignore[method-assign]
        return w, recorded

    def test_fb_login_phase_detail(self) -> None:
        w, rec = self._worker()

        async def run():
            await w._on_fb_phase("fb_loading")
            await w._on_fb_phase("fb_login_wait")

        asyncio.run(run())
        self.assertEqual(rec[0][0], "fb_loading")
        self.assertEqual(rec[1][0], "fb_login_wait")
        self.assertIn("Sign in to Facebook", rec[1][1])

    def test_gemini_login_roundtrip_restores_phase(self) -> None:
        w, rec = self._worker()
        self.assertEqual(w._snapshot["phase"], "gemini")

        async def run():
            await w._on_gemini_phase("gemini_login_wait")
            self.assertEqual(w._phase_before_gemini_login, "gemini")
            await w._on_gemini_phase("gemini_ok")

        asyncio.run(run())
        self.assertEqual(rec[0][0], "gemini_login_wait")
        self.assertIn("Google/Gemini", rec[0][1])
        self.assertEqual(rec[-1][0], "gemini")  # restored

    def test_gemini_ok_never_restores_terminal_phases(self) -> None:
        w, rec = self._worker()
        w._snapshot["phase"] = "stopped"

        async def run():
            await w._on_gemini_phase("gemini_ok")

        asyncio.run(run())
        self.assertEqual(rec, [])  # nothing restored


# ------------------------------------------------------------ legacy bot
class TestLegacyBotParity(unittest.TestCase):
    def _bot(self, counts) -> FacebookAutoEngagementBot:
        b = FacebookAutoEngagementBot.__new__(FacebookAutoEngagementBot)
        b.page = FakeFbPage(counts)
        b._stop = threading.Event()
        b.on_status = None
        b.wait_time = 3.0
        b.running = False
        b.comments_done = 0
        b.likes_done = 0
        b.posts_seen = 0
        b.last_comment = ""
        b.last_error = ""
        b.user_data_dir = "x"
        b.facebook_url = "https://www.facebook.com"
        b._emit = lambda **kw: None  # type: ignore[method-assign]
        return b

    def test_stop_flag_exits_cleanly_without_raise(self) -> None:
        b = self._bot([0])
        b._stop.set()

        async def run():
            await b._wait_login_if_needed()

        asyncio.run(run())  # clean return

    def test_positive_env_ms_timeout_still_works(self) -> None:
        b = self._bot([0])

        async def run():
            with mock.patch.dict(os.environ, {"FB_LOGIN_WAIT_MS": "30"}):
                await b._wait_login_if_needed()

        with self.assertRaises(RuntimeError):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
