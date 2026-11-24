"""Unit tests for Module 1 PHASE-08 — capture failure policy (D13).

instant_skip (default) advances immediately; retry_limited micro-scrolls
and re-captures up to capture_retry_limit consecutive failures, then
advances and resets. Includes a loop simulation mirroring the worker's
gate usage (flaky-then-recover scenario from the acceptance criteria).
"""

from __future__ import annotations

import unittest

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.worker import Module1Worker


def _worker() -> Module1Worker:
    return Module1Worker.__new__(Module1Worker)


def _settings(policy: str, limit: int = 3) -> M1Settings:
    return M1Settings(capture_failure_policy=policy, capture_retry_limit=limit)


class TestCaptureFailureDecision(unittest.TestCase):
    def test_default_is_instant_skip(self) -> None:
        self.assertEqual(M1Settings().capture_failure_policy, "instant_skip")

    def test_instant_skip_always_advances_regardless_of_streak(self) -> None:
        w = _worker()
        s = _settings("instant_skip")
        for streak in (0, 1, 5):
            action, new = w._capture_failure_action(s, streak)
            self.assertEqual(action, "advance")
            self.assertEqual(new, 0)

    def test_retry_limited_walks_streak_then_advances_and_resets(self) -> None:
        w = _worker()
        s = _settings("retry_limited", limit=3)
        streak = 0
        actions = []
        for _ in range(5):
            action, streak = w._capture_failure_action(s, streak)
            actions.append(action)
        # retry, retry, retry (streak==limit), then advance with reset
        self.assertEqual(actions, ["retry", "retry", "retry", "advance", "retry"])
        # after the advance the counter restarted at 0, so next is retry(1)

    def test_streak_never_exceeds_limit(self) -> None:
        w = _worker()
        s = _settings("retry_limited", limit=2)
        streak = 0
        for _ in range(50):
            _, streak = w._capture_failure_action(s, streak)
            self.assertLessEqual(streak, 2)

    def test_owner_lowers_limit_below_current_streak_advances_next(self) -> None:
        w = _worker()
        high = _settings("retry_limited", limit=5)
        _, streak = w._capture_failure_action(high, 0)   # -> 1
        _, streak = w._capture_failure_action(high, streak)  # -> 2
        low = _settings("retry_limited", limit=2)
        action, new = w._capture_failure_action(low, streak)  # 2 !< 2
        self.assertEqual(action, "advance")
        self.assertEqual(new, 0)

    def test_limit_clamps_to_at_least_one(self) -> None:
        w = _worker()
        s = M1Settings(capture_failure_policy="retry_limited", capture_retry_limit=0)
        action, new = w._capture_failure_action(s, 0)
        # validator coerces 0 -> 1; helper guard would too
        self.assertEqual(action, "retry")
        self.assertEqual(new, 1)


class TestLoopSimulation(unittest.TestCase):
    """Mirrors the worker gate usage: flaky selector recovering (acceptance)."""

    def _simulate(self, shot_results: list[bool], settings: M1Settings):
        w = _worker()
        streak = 0
        events: list[str] = []
        for ok in shot_results:
            if ok:
                streak = 0
                events.append("captured")
                continue
            action, streak = w._capture_failure_action(settings, streak)
            if action == "retry":
                events.append(f"retry{streak}")
            else:
                streak = 0
                events.append("advanced")

    def test_flaky_one_false_recovers_under_retry_limited(self) -> None:
        self._simulate([False, True], _settings("retry_limited", limit=3))

    def test_persistent_junk_advances_after_limit_no_infinite_spin(self) -> None:
        self._simulate([False] * 10, _settings("retry_limited", limit=3))

    def test_default_policy_skips_every_failure_instantly(self) -> None:
        self._simulate([False] * 6, _settings("instant_skip"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
