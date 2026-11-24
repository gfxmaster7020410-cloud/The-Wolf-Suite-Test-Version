"""Unit tests for Module 1 PHASE-11 — checkpoint policy selector (D29).

wait_indefinitely (default): the worker polls checkpoint_detected() forever
until the human clears the check or presses Stop. wait_then_stop: same
polling plus an auto-stop budget of checkpoint_wait_sec ending the run with
the exact panel message "Checkpoint unresolved — auto-stopped".
No browser is launched — FacebookAutomation is faked and the clock is a
deterministic per-call ticker, so no test depends on wall-clock timing.
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from typing import Any, Dict, List, Optional
from unittest import mock

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.worker import Module1Worker, _WorkerStopped


# ---------------------------------------------------------------- fakes
class FakeFb:
    """checkpoint_detected() consumes scripted values; last one repeats."""

    def __init__(self, detections):
        self.detections = list(detections)
        self.calls = 0

    async def checkpoint_detected(self) -> bool:
        self.calls += 1
        if len(self.detections) > 1:
            return self.detections.pop(0)
        return self.detections[0]


class TickingClock:
    """Deterministic time.time replacement: every call advances `step` sec."""

    def __init__(self, start: float = 1000.0, step: float = 5.0):
        self.t = start - step  # first call returns exactly `start`
        self.step = step

    def time(self) -> float:
        self.t += self.step
        return self.t


async def _nosleep(_seconds: float) -> None:
    pass


def _worker(statuses: Optional[List[Dict[str, Any]]] = None) -> Module1Worker:
    w = Module1Worker.__new__(Module1Worker)
    w.store = None  # not touched by the paths under test
    w.on_status = (
        lambda payload: statuses.append(dict(payload)) if statuses is not None else None
    )
    w._lock = threading.Lock()
    w._stop = threading.Event()
    w._pause_gate = threading.Event()
    w._pause_gate.set()
    w._paused_reason = ""
    w._phase_before_pause = "running"
    w._terminal_note = ""
    w._snapshot = {
        "running": True,
        "phase": "running",
        "phase_detail": "",
        "last_error": "",
        "stop_reason": "",
        "posts_engaged": 0,
        "checkpoint_since": 0.0,
    }
    return w


# ------------------------------------------------------- pure deadline
class TestCheckpointDeadline(unittest.TestCase):
    def test_default_policy_is_wait_indefinitely(self) -> None:
        self.assertEqual(M1Settings().checkpoint_policy, "wait_indefinitely")

    def test_unknown_policy_coerces_to_default(self) -> None:
        self.assertEqual(
            M1Settings(checkpoint_policy="bogus").checkpoint_policy,
            "wait_indefinitely",
        )

    def test_indefinite_policy_has_no_deadline_despite_budget(self) -> None:
        s = M1Settings(checkpoint_policy="wait_indefinitely", checkpoint_wait_sec=1800)
        self.assertIsNone(Module1Worker._checkpoint_deadline(s, now=100.0))

    def test_wait_then_stop_deadline_is_now_plus_budget(self) -> None:
        s = M1Settings(checkpoint_policy="wait_then_stop", checkpoint_wait_sec=120)
        self.assertEqual(Module1Worker._checkpoint_deadline(s, now=100.0), 220.0)

    def test_zero_budget_guard_never_instant_stops(self) -> None:
        s = M1Settings(checkpoint_policy="wait_then_stop", checkpoint_wait_sec=0)
        self.assertIsNone(Module1Worker._checkpoint_deadline(s, now=100.0))


# --------------------------------------------------- async hold behavior
class TestHandleCheckpointIndefinite(unittest.TestCase):
    def _run(self, w: Module1Worker, fb: FakeFb, settings: M1Settings) -> None:
        with mock.patch(
            "services.module_1_feed_observer.worker.asyncio.sleep", _nosleep
        ):
            asyncio.run(w._handle_checkpoint(fb, settings))

    def test_holds_through_detections_then_clears_into_running(self) -> None:
        statuses: List[Dict[str, Any]] = []
        w = _worker(statuses)
        fb = FakeFb([True, True, False])

        self._run(w, fb, M1Settings())  # default policy

        self.assertEqual(fb.calls, 3)
        self.assertEqual(w._snapshot["phase"], "running")
        self.assertEqual(w._snapshot["checkpoint_since"], 0.0)
        self.assertEqual(w._snapshot["last_error"], "")

    def test_checkpoint_since_visible_while_blocked_and_cleared_after(self) -> None:
        statuses: List[Dict[str, Any]] = []
        w = _worker(statuses)

        self._run(w, FakeFb([True, False]), M1Settings())

        held = [p for p in statuses if p.get("phase") == "checkpoint_paused"]
        self.assertTrue(held)
        # acceptance: dashboard can render a growing elapsed timer while blocked
        self.assertGreater(held[0]["checkpoint_since"], 0.0)
        self.assertEqual(w._snapshot["checkpoint_since"], 0.0)

    def test_stop_pressed_mid_hold_aborts_cleanly(self) -> None:
        w = _worker()
        w._stop.set()

        with self.assertRaises(_WorkerStopped):
            self._run(w, FakeFb([True]), M1Settings())
        self.assertEqual(w._snapshot["checkpoint_since"], 0.0)


class TestHandleCheckpointTimed(unittest.TestCase):
    def _run_ticking(self, w: Module1Worker, fb: FakeFb, settings: M1Settings) -> None:
        clock = TickingClock(start=1000.0, step=5.0)

        async def run():
            await w._handle_checkpoint(fb, settings)

        with mock.patch(
            "services.module_1_feed_observer.worker.asyncio.sleep", _nosleep
        ), mock.patch("services.module_1_feed_observer.worker.time") as mtime:
            mtime.time = clock.time
            asyncio.run(run())

    def test_expiry_auto_stops_with_exact_panel_message(self) -> None:
        w = _worker()
        # budget 30s vs 5s-per-call clock: expires within a few poll iterations
        s = M1Settings(checkpoint_policy="wait_then_stop", checkpoint_wait_sec=30)

        with self.assertRaises(_WorkerStopped):
            self._run_ticking(w, FakeFb([True]), s)

        snap = w._snapshot
        self.assertEqual(snap["last_error"], "Checkpoint unresolved — auto-stopped")
        self.assertEqual(snap["stop_reason"], "checkpoint_unresolved")
        self.assertEqual(snap["phase"], "checkpoint_paused")
        self.assertEqual(snap["checkpoint_since"], 0.0)
        self.assertEqual(w._terminal_note, "Checkpoint unresolved — auto-stopped")

    def test_clearing_before_deadline_resumes_normally(self) -> None:
        w = _worker()
        s = M1Settings(checkpoint_policy="wait_then_stop", checkpoint_wait_sec=600)

        self._run_ticking(w, FakeFb([True, True, False]), s)

        self.assertEqual(w._snapshot["phase"], "running")
        self.assertEqual(w._snapshot["last_error"], "")
        self.assertEqual(w._snapshot["checkpoint_since"], 0.0)

    def test_detail_shows_remaining_minutes_each_poll(self) -> None:
        statuses: List[Dict[str, Any]] = []
        w = _worker(statuses)
        s = M1Settings(checkpoint_policy="wait_then_stop", checkpoint_wait_sec=600)

        self._run_ticking(w, FakeFb([True, True, True, True, False]), s)

        details = [
            p.get("phase_detail", "")
            for p in statuses
            if p.get("phase") == "checkpoint_paused"
        ]
        self.assertTrue(details)
        self.assertIn("Facebook security check detected", details[0])
        self.assertGreaterEqual(len(details), 2, "timed detail must refresh per poll")
        self.assertIn("auto-stop in ~", details[-1])
        # remaining budget shrinks across refreshed details
        self.assertNotEqual(details[1], details[-1])

    def test_zero_budget_under_timed_policy_behaves_indefinite(self) -> None:
        w = _worker()
        s = M1Settings(checkpoint_policy="wait_then_stop", checkpoint_wait_sec=0)

        self._run_ticking(w, FakeFb([True, True, False]), s)

        self.assertEqual(w._snapshot["phase"], "running")
        self.assertEqual(w._snapshot["last_error"], "")


class TestPauseGateInterplay(unittest.TestCase):
    def test_user_pause_inside_checkpoint_blocks_until_resume(self) -> None:
        statuses: List[Dict[str, Any]] = []
        w = _worker(statuses)
        w._pause_gate.clear()  # owner pressed Pause right before the check
        w._paused_reason = "user"

        resume = threading.Timer(0.05, w.request_resume)
        resume.start()
        try:
            with mock.patch(
                "services.module_1_feed_observer.worker.asyncio.sleep", _nosleep
            ):
                asyncio.run(w._handle_checkpoint(FakeFb([True, False]), M1Settings()))
        finally:
            resume.cancel()

        phases = [p.get("phase") for p in statuses]
        self.assertIn("paused", phases)
        self.assertEqual(w._snapshot["phase"], "running")


if __name__ == "__main__":
    unittest.main(verbosity=2)
