"""Unit tests for Module 1 PHASE-05 — per-post action counting (D28).

per_post (default): one engaged post == exactly one cap unit, whether the
bot reacted, commented, or both. per_action: legacy per-sub-action counting.
A fully failed post counts nothing in either mode.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.worker import Module1Worker


def _worker() -> Module1Worker:
    w = Module1Worker.__new__(Module1Worker)
    w._action_times = []
    w._snapshot_updates = []  # type: ignore[attr-defined]
    w._set_snapshot = lambda **kw: w._snapshot_updates.append(kw)  # type: ignore[method-assign]
    return w


def _settings(mode: str) -> M1Settings:
    return M1Settings(action_count_mode=mode)


class TestPerPostCounting(unittest.TestCase):
    def test_react_and_comment_counts_once(self) -> None:
        w = _worker()
        w._record_engagement_actions(
            _settings("per_post"), did_react=True, did_comment=True
        )
        self.assertEqual(len(w._action_times), 1)

    def test_reaction_only_counts_once(self) -> None:
        w = _worker()
        w._record_engagement_actions(_settings("per_post"), did_react=True, did_comment=False)
        self.assertEqual(len(w._action_times), 1)

    def test_comment_only_counts_once(self) -> None:
        w = _worker()
        w._record_engagement_actions(_settings("per_post"), did_react=False, did_comment=True)
        self.assertEqual(len(w._action_times), 1)

    def test_total_failure_counts_nothing(self) -> None:
        w = _worker()
        w._record_engagement_actions(_settings("per_post"), did_react=False, did_comment=False)
        self.assertEqual(len(w._action_times), 0)


class TestPerActionLegacy(unittest.TestCase):
    def test_both_success_counts_twice_regression_safe(self) -> None:
        w = _worker()
        w._record_engagement_actions(
            _settings("per_action"), did_react=True, did_comment=True
        )
        self.assertEqual(len(w._action_times), 2)

    def test_single_success_counts_once(self) -> None:
        w = _worker()
        w._record_engagement_actions(_settings("per_action"), did_react=True, did_comment=False)
        self.assertEqual(len(w._action_times), 1)

    def test_total_failure_counts_nothing_legacy_too(self) -> None:
        w = _worker()
        w._record_engagement_actions(_settings("per_action"), did_react=False, did_comment=False)
        self.assertEqual(len(w._action_times), 0)

    def test_default_mode_is_per_post(self) -> None:
        self.assertEqual(M1Settings().action_count_mode, "per_post")


class TestCapIntegration(unittest.TestCase):
    def _capped_worker(self, hourly: int):
        w = _worker()
        s = M1Settings(action_count_mode="per_post", max_actions_per_hour=hourly)
        return w, s

    def test_three_posts_consume_exactly_three_units_and_trip_hourly_wait(self) -> None:
        w, s = self._capped_worker(hourly=3)
        for _ in range(3):
            w._record_engagement_actions(s, did_react=True, did_comment=True)
        self.assertEqual(len(w._action_times), 3)
        state, wait, reason = w._cap_status(s)
        self.assertEqual(state, "wait")
        self.assertGreater(wait, 0)
        self.assertIn("Hourly cap", reason)

    def test_midrun_mode_flip_applies_next_post(self) -> None:
        w = _worker()
        legacy = _settings("per_action")
        modern = _settings("per_post")
        w._record_engagement_actions(legacy, did_react=True, did_comment=True)
        w._record_engagement_actions(modern, did_react=True, did_comment=True)
        self.assertEqual(len(w._action_times), 3)  # 2 + 1 mixed accounting

    def test_snapshot_reports_units_not_subactions(self) -> None:
        w, s = self._capped_worker(hourly=0)
        w._record_engagement_actions(s, did_react=True, did_comment=True)
        update = w._snapshot_updates[-1]
        self.assertEqual(update["hourly_used"], 1)
        self.assertEqual(update["daily_used"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
