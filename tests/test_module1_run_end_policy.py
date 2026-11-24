"""Unit tests for Module 1 PHASE-14 — run-end policy (D31).

stay_open parks the worker with the browser open ONLY on natural ends
(daily cap / max-posts / time-boxed) while errors, user Stops and safety
auto-stops always close. Covers the pure decision matrix, the park loop's
entry flags and exit paths, the fb.page_closed probe, and the runner's
adoption path that stops a parked worker before a new Start.
"""

from __future__ import annotations

import asyncio
import threading
import types
import unittest
from typing import Any, Dict, List, Optional
from unittest import mock

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.fb import FacebookAutomation
from services.module_1_feed_observer.worker import (
    NATURAL_END_REASONS,
    PHASES,
    Module1Worker,
)


# ---------------------------------------------------------------- helpers
def _worker(statuses: Optional[List[Dict[str, Any]]] = None) -> Module1Worker:
    w = Module1Worker.__new__(Module1Worker)
    w.store = None
    w.on_status = (
        lambda payload: statuses.append(dict(payload)) if statuses is not None else None
    )
    w._lock = threading.Lock()
    w._stop = threading.Event()
    w._pause_gate = threading.Event()
    w._pause_gate.set()
    w._paused_reason = ""
    w._snapshot = {
        "running": True,
        "phase": "running",
        "phase_detail": "",
        "last_error": "",
        "stop_reason": "",
        "posts_engaged": 0,
        "checkpoint_since": 0.0,
        "browser_left_open": False,
    }
    return w


async def _nosleep(_seconds: float) -> None:
    pass


class FakeParkedFb:
    """Scripted page_closed probe; last value repeats."""

    def __init__(self, closed_script):
        self.script = list(closed_script)
        self.closed_called = False

    def page_closed(self) -> bool:
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0]

    async def close(self) -> None:
        self.closed_called = True


class SyncPage:
    def __init__(self, closed: bool = False, raise_: bool = False):
        self._closed = closed
        self._raise = raise_

    def is_closed(self) -> bool:  # Playwright sync — keep fake sync too
        if self._raise:
            raise RuntimeError("detached")
        return self._closed


# ------------------------------------------------------- decision matrix
class TestShouldStayOpen(unittest.TestCase):
    def test_natural_reasons_registry(self) -> None:
        self.assertEqual(
            NATURAL_END_REASONS,
            {"daily_cap_reached", "max_posts_reached", "time_limit_reached"},
        )

    def test_stay_open_plus_natural_end_parks(self) -> None:
        s = M1Settings(run_end_policy="stay_open")
        for reason in sorted(NATURAL_END_REASONS):
            self.assertTrue(Module1Worker._should_stay_open(s, reason), reason)

    def test_errors_and_stops_always_close_even_under_stay_open(self) -> None:
        s = M1Settings(run_end_policy="stay_open")
        for reason in ("stopped", "error", "", "checkpoint_unresolved"):
            self.assertFalse(Module1Worker._should_stay_open(s, reason), reason)

    def test_default_policy_always_closes(self) -> None:
        s = M1Settings()  # close_browser default
        for reason in sorted(NATURAL_END_REASONS):
            self.assertFalse(Module1Worker._should_stay_open(s, reason), reason)

    def test_unknown_reason_never_parks(self) -> None:
        s = M1Settings(run_end_policy="stay_open")
        self.assertFalse(Module1Worker._should_stay_open(s, "hourly cap wait ended"))


# ------------------------------------------------------------ park loop
class TestParkWithBrowserOpen(unittest.TestCase):
    def _run_park(self, w: Module1Worker, fb: FakeParkedFb) -> None:
        with mock.patch(
            "services.module_1_feed_observer.worker.asyncio.sleep", _nosleep
        ):
            asyncio.run(w._park_with_browser_open(fb))

    def test_entry_flags_show_finished_browser_open(self) -> None:
        statuses: List[Dict[str, Any]] = []
        w = _worker(statuses)
        stop_timer = threading.Timer(0.03, w.request_stop)
        stop_timer.start()
        try:
            self._run_park(w, FakeParkedFb([False]))
        finally:
            stop_timer.cancel()

        held = [p for p in statuses if p.get("phase") == "browser_open"]
        self.assertTrue(held)
        self.assertFalse(held[0]["running"])          # panel shows Finished
        self.assertTrue(held[0]["browser_left_open"]) # badge visible while parked
        self.assertIn("left open", held[0]["phase_detail"])

    def test_dashboard_stop_releases_and_closes(self) -> None:
        w = _worker()
        fb = FakeParkedFb([False])  # window never closes itself
        timer = threading.Timer(0.03, w.request_stop)
        timer.start()
        try:
            self._run_park(w, fb)
        finally:
            timer.cancel()
        self.assertTrue(fb.closed_called)

    def test_user_closing_window_releases_without_stop(self) -> None:
        w = _worker()  # no Stop pressed
        fb = FakeParkedFb([False, True])
        self._run_park(w, fb)
        self.assertTrue(fb.closed_called)

    def test_already_closed_window_closes_immediately(self) -> None:
        w = _worker()
        fb = FakeParkedFb([True])
        self._run_park(w, fb)
        self.assertTrue(fb.closed_called)

    def test_close_failure_swallowed_on_exit(self) -> None:
        class BrokenClose(FakeParkedFb):
            async def close(self):
                self.closed_called = True
                raise RuntimeError("context already dead")

        w = _worker()
        w._stop.set()
        fb = BrokenClose([True])
        self._run_park(w, fb)  # must not raise
        self.assertTrue(fb.closed_called)


# ------------------------------------------------------------ fb probe
class TestPageClosedProbe(unittest.TestCase):
    def _fb(self, page: Any) -> FacebookAutomation:
        fb = FacebookAutomation.__new__(FacebookAutomation)
        fb.page = page
        return fb

    def test_live_page_reports_open(self) -> None:
        self.assertFalse(self._fb(SyncPage(False)).page_closed())

    def test_closed_page_reports_closed(self) -> None:
        self.assertTrue(self._fb(SyncPage(True)).page_closed())

    def test_none_page_reports_closed(self) -> None:
        self.assertTrue(self._fb(None).page_closed())

    def test_raising_probe_fails_closed(self) -> None:
        self.assertTrue(self._fb(SyncPage(raise_=True)).page_closed())


# ------------------------------------------------------------ surface
class TestSurfaceContract(unittest.TestCase):
    def test_phase_string_registered(self) -> None:
        self.assertIn("browser_open", PHASES)
        self.assertEqual(PHASES["browser_open"], "Finished — browser left open")

    def test_idle_snapshot_defaults_badge_off(self) -> None:
        snap = Module1Worker.__new__(Module1Worker)._idle_snapshot()
        self.assertFalse(snap["browser_left_open"])

    def test_runner_fallback_relays_badge(self) -> None:
        from services.module_1_feed_observer.runner import Module1Runner

        r = Module1Runner.__new__(Module1Runner)
        r.store = types.SimpleNamespace(dump=lambda: {})
        status = r._compose_status(None, {"browser_left_open": True})
        self.assertTrue(status["browser_left_open"])


# ------------------------------------------------------------ adoption
class StubOldWorker:
    def __init__(self, phase: str = "browser_open"):
        self._phase = phase
        self.stop_requested = False

    def is_running(self) -> bool:
        return False

    def request_stop(self) -> None:
        self.stop_requested = True

    def snapshot(self) -> Dict[str, Any]:
        return {"running": False, "phase": self._phase}


class TestRunnerAdoptionPath(unittest.TestCase):
    def _make_runner(self, old: StubOldWorker, task):
        import services.module_1_feed_observer.runner as runner_mod

        r = runner_mod.Module1Runner.__new__(runner_mod.Module1Runner)
        r.store = types.SimpleNamespace(dump=lambda: {})
        r._lock = threading.Lock()
        r._latest = {}
        r._worker = old  # type: ignore[assignment]
        r._task = task
        return r

    def test_start_adopts_parked_worker_before_spawning_new(self) -> None:
        import services.module_1_feed_observer.runner as runner_mod

        async def scenario():
            old = StubOldWorker("browser_open")

            async def still_parking():
                await asyncio.sleep(0.05)  # simulates the teardown tail

            task = asyncio.get_running_loop().create_task(still_parking())
            r = self._make_runner(old, task)

            created: Dict[str, Any] = {}

            class FakeNewWorker:
                def __init__(self, store, on_status=None):
                    created["instance"] = self
                    self.snap = {
                        "running": False,
                        "phase": "idle",
                        "phase_detail": "",
                        "stop_reason": "",
                        "last_error": "",
                    }

                def snapshot(self):
                    return dict(self.snap)

                def is_running(self):
                    return False

                async def run(self):
                    return self.snapshot()

            async def fake_job(fn):
                await fn()

            with mock.patch.object(runner_mod, "Module1Worker", FakeNewWorker), mock.patch.object(
                runner_mod, "await_playwright_job", fake_job
            ):
                result = await r.start()

            self.assertTrue(old.stop_requested)      # parked worker stopped first
            self.assertIn("instance", created)       # new worker spawned
            self.assertTrue(result["ok"])

        asyncio.run(scenario())

    def test_finished_run_does_not_trigger_adoption_wait(self) -> None:
        import services.module_1_feed_observer.runner as runner_mod

        async def scenario():
            old = StubOldWorker("stopped")

            async def already_done():
                pass

            task = asyncio.get_running_loop().create_task(already_done())
            await task  # ensure done() is True before start()
            r = self._make_runner(old, task)

            class FakeNewWorker:
                def __init__(self, store, on_status=None):
                    self.snap = {"running": False, "phase": "idle", "phase_detail": "",
                                 "stop_reason": "", "last_error": ""}

                def snapshot(self):
                    return dict(self.snap)

                def is_running(self):
                    return False

                async def run(self):
                    return self.snapshot()

            async def fake_job(fn):
                await fn()

            with mock.patch.object(runner_mod, "Module1Worker", FakeNewWorker), mock.patch.object(
                runner_mod, "await_playwright_job", fake_job
            ):
                result = await r.start()

            self.assertFalse(old.stop_requested)     # nothing to adopt
            self.assertTrue(result["ok"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
