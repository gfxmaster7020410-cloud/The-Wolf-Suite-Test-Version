"""Unit tests for Module 1 PHASE-12 — Gemini circuit breaker (D30).

After N consecutive AI decision AVAILABILITY failures the worker pauses
(ai_paused) instead of burning every post into a skip+error cycle:
cooldown_min > 0 auto-resumes after the window; 0 holds for dashboard
Resume; Stop always aborts. Success resets the streak; low-confidence
quality skips never count. Deterministic ticking clock — no wall timing.
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from typing import Any, Dict, List, Optional
from unittest import mock

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.worker import PHASES, Module1Worker, _WorkerStopped


# ---------------------------------------------------------------- fakes
class TickingClock:
    """Deterministic time.time replacement: every call advances `step` sec."""

    def __init__(self, start: float = 1000.0, step: float = 60.0):
        self.t = start - step  # first call returns exactly `start`
        self.step = step

    def time(self) -> float:
        self.t += self.step
        return self.t


async def _nosleep(_seconds: float) -> None:
    pass


def _worker(statuses: Optional[List[Dict[str, Any]]] = None) -> Module1Worker:
    w = Module1Worker.__new__(Module1Worker)
    w.store = None  # snapshot() tolerates None (guarded try/except inside)
    w.on_status = (
        lambda payload: statuses.append(dict(payload)) if statuses is not None else None
    )
    w._lock = threading.Lock()
    w._stop = threading.Event()
    w._pause_gate = threading.Event()
    w._pause_gate.set()
    w._paused_reason = ""
    w._phase_before_pause = "running"
    w._ai_fail_streak = 0
    w._snapshot = {
        "running": True,
        "phase": "running",
        "phase_detail": "",
        "last_error": "",
        "stop_reason": "",
        "posts_engaged": 0,
        "gemini_errors": 0,
        "ai_fail_streak": 0,
        "ai_breaker_tripped": False,
        "checkpoint_since": 0.0,
    }
    return w


# ------------------------------------------------------- pure trip rule
class TestBreakerShouldTrip(unittest.TestCase):
    def _s(self, enabled=True, threshold=5, cooldown=10.0) -> M1Settings:
        return M1Settings(
            circuit_breaker_enabled=enabled,
            circuit_breaker_threshold=threshold,
            circuit_breaker_cooldown_min=cooldown,
        )

    def test_defaults_are_off(self) -> None:
        s = M1Settings()
        self.assertFalse(s.circuit_breaker_enabled)
        self.assertEqual(s.circuit_breaker_threshold, 5)

    def test_disabled_never_trips(self) -> None:
        for streak in (0, 3, 99):
            self.assertFalse(Module1Worker._breaker_should_trip(self._s(enabled=False), streak))

    def test_threshold_boundary(self) -> None:
        s = self._s(threshold=5)
        self.assertFalse(Module1Worker._breaker_should_trip(s, 4))
        self.assertTrue(Module1Worker._breaker_should_trip(s, 5))
        self.assertTrue(Module1Worker._breaker_should_trip(s, 6))

    def test_floor_of_two_ignores_single_flaky_failure(self) -> None:
        s = self._s(threshold=1)
        self.assertFalse(Module1Worker._breaker_should_trip(s, 1))
        self.assertTrue(Module1Worker._breaker_should_trip(s, 2))

    def test_streak_semantics_acceptance_scenario(self) -> None:
        """2 fails, 1 success, then 3 more fails (threshold 3): trips ONLY
        on the 3rd failure of the second group."""
        s = self._s(threshold=3)
        streak = 0
        trips: List[int] = []
        for outcome in ["fail", "fail", "ok", "fail", "fail", "fail"]:
            if outcome == "fail":
                streak += 1
                if Module1Worker._breaker_should_trip(s, streak):
                    trips.append(streak)
                    streak = 0  # mirror post-trip reset
            else:
                streak = 0
        self.assertEqual(trips, [3])
        self.assertEqual(streak, 0)


# ------------------------------------------------------ hold behavior
class TestTripBreakerCooldown(unittest.TestCase):
    def _run_ticking(self, w: Module1Worker, settings: M1Settings, step: float = 60.0):
        clock = TickingClock(start=1000.0, step=step)

        async def run():
            await w._trip_breaker(settings)

        with mock.patch(
            "services.module_1_feed_observer.worker.asyncio.sleep", _nosleep
        ), mock.patch("services.module_1_feed_observer.worker.time") as mtime:
            mtime.time = clock.time
            asyncio.run(run())

    def test_cooldown_auto_resumes_without_user(self) -> None:
        statuses: List[Dict[str, Any]] = []
        w = _worker(statuses)
        w._ai_fail_streak = 4
        s = M1Settings(
            circuit_breaker_enabled=True,
            circuit_breaker_threshold=3,
            circuit_breaker_cooldown_min=10.0,
        )

        self._run_ticking(w, s)

        snap = w._snapshot
        self.assertEqual(snap["phase"], "running")
        self.assertFalse(snap["ai_breaker_tripped"])
        self.assertEqual(w._ai_fail_streak, 0)
        held = [p for p in statuses if p.get("phase") == "ai_paused"]
        self.assertTrue(held)
        self.assertTrue(held[0]["ai_breaker_tripped"])
        self.assertIn("AI failed 4x consecutively", held[0]["last_error"])
        self.assertIn("cooling down", held[0]["phase_detail"])

    def test_user_resume_mid_cooldown_ends_hold_early(self) -> None:
        w = _worker()
        s = M1Settings(
            circuit_breaker_enabled=True,
            circuit_breaker_threshold=3,
            circuit_breaker_cooldown_min=60.0,
        )
        resume = threading.Timer(0.03, w.request_resume)
        resume.start()
        try:
            self._run_ticking(w, s, step=1.0)
        finally:
            resume.cancel()

        self.assertEqual(w._snapshot["phase"], "running")
        self.assertFalse(w._snapshot["ai_breaker_tripped"])
        self.assertEqual(w._ai_fail_streak, 0)


class TestTripBreakerManual(unittest.TestCase):
    def _run(self, w: Module1Worker, settings: M1Settings):
        with mock.patch(
            "services.module_1_feed_observer.worker.asyncio.sleep", _nosleep
        ):
            asyncio.run(w._trip_breaker(settings))

    def test_zero_cooldown_holds_for_dashboard_resume(self) -> None:
        statuses: List[Dict[str, Any]] = []
        w = _worker(statuses)
        w._ai_fail_streak = 3
        s = M1Settings(circuit_breaker_enabled=True, circuit_breaker_cooldown_min=0.0)

        resume = threading.Timer(0.05, w.request_resume)
        resume.start()
        try:
            self._run(w, s)
        finally:
            resume.cancel()

        snap = w._snapshot
        self.assertEqual(snap["phase"], "running")
        self.assertFalse(snap["ai_breaker_tripped"])
        self.assertEqual(w._ai_fail_streak, 0)
        detail = next(
            p.get("phase_detail", "") for p in statuses if p.get("phase") == "ai_paused"
        )
        self.assertIn("press Resume", detail)

    def test_stop_during_hold_aborts_and_clears_flags(self) -> None:
        w = _worker()
        w._stop.set()
        w._ai_fail_streak = 7
        s = M1Settings(circuit_breaker_enabled=True, circuit_breaker_cooldown_min=0.0)

        with self.assertRaises(_WorkerStopped):
            self._run(w, s)

        # finally-path cleanup even on abort
        self.assertEqual(w._ai_fail_streak, 0)
        self.assertFalse(w._snapshot["ai_breaker_tripped"])


class TestMaybeTripGuard(unittest.TestCase):
    def _run(self, w: Module1Worker, settings: M1Settings) -> None:
        with mock.patch(
            "services.module_1_feed_observer.worker.asyncio.sleep", _nosleep
        ):
            asyncio.run(w._maybe_trip_breaker(settings))

    def test_disabled_never_blocks_gate_or_trips(self) -> None:
        w = _worker()
        w._ai_fail_streak = 50
        self._run(w, M1Settings(circuit_breaker_enabled=False))
        self.assertTrue(w._pause_gate.is_set())
        self.assertFalse(w._snapshot["ai_breaker_tripped"])

    def test_below_threshold_no_op(self) -> None:
        w = _worker()
        w._ai_fail_streak = 2
        self._run(w, M1Settings(circuit_breaker_enabled=True, circuit_breaker_threshold=3))
        self.assertTrue(w._pause_gate.is_set())
        self.assertFalse(w._snapshot["ai_breaker_tripped"])

    def test_at_threshold_trips_and_holds_until_resume(self) -> None:
        w = _worker()
        w._ai_fail_streak = 3
        s = M1Settings(
            circuit_breaker_enabled=True,
            circuit_breaker_threshold=3,
            circuit_breaker_cooldown_min=0.0,
        )
        resume = threading.Timer(0.05, w.request_resume)
        resume.start()
        try:
            self._run(w, s)
        finally:
            resume.cancel()
        self.assertEqual(w._snapshot["phase"], "running")


class TestSurfaceContract(unittest.TestCase):
    def test_phase_string_registered(self) -> None:
        self.assertIn("ai_paused", PHASES)
        self.assertEqual(PHASES["ai_paused"], "AI engine failing — paused")

    def test_idle_snapshot_defaults(self) -> None:
        snap = Module1Worker.__new__(Module1Worker)._idle_snapshot()
        self.assertEqual(snap["ai_fail_streak"], 0)
        self.assertFalse(snap["ai_breaker_tripped"])

    def test_ai_paused_reports_as_paused_on_panel(self) -> None:
        w = _worker()
        w._snapshot["phase"] = "ai_paused"
        self.assertTrue(w.snapshot()["paused"])

    def test_runner_fallback_relays_breaker_fields(self) -> None:
        from types import SimpleNamespace

        from services.module_1_feed_observer.runner import Module1Runner

        r = Module1Runner.__new__(Module1Runner)
        r.store = SimpleNamespace(dump=lambda: {})
        status = r._compose_status(None, {"ai_fail_streak": 4, "ai_breaker_tripped": True})
        self.assertEqual(status["ai_fail_streak"], 4)
        self.assertTrue(status["ai_breaker_tripped"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
