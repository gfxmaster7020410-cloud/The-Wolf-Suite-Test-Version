"""Unit tests for Module 1 PHASE-04 — run-length modes (unlimited /
max_posts / time_boxed) and live progress snapshot fields.

Pure logic tests: no browser, no sleeps longer than milliseconds.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from services.module_1_feed_observer.config import M1Settings, M1SettingsStore
from services.module_1_feed_observer.worker import Module1Worker


def _worker(posts_engaged=0, started_at=0.0) -> Module1Worker:
    w = Module1Worker.__new__(Module1Worker)
    w._legacy_mp_logged = False
    w._snapshot = {
        "posts_engaged": posts_engaged,
        "started_at": started_at,
        "phase": "running",
    }
    return w


class TestRunLengthGate(unittest.TestCase):
    def test_unlimited_never_stops(self) -> None:
        s = M1Settings(run_length_mode="unlimited", max_posts_per_run=0)
        self.assertIsNone(_worker(posts_engaged=999)._run_length_stop(s))

    def test_max_posts_mode_stops_at_limit(self) -> None:
        s = M1Settings(run_length_mode="max_posts", max_posts_per_run=5)
        self.assertIsNone(_worker(posts_engaged=4)._run_length_stop(s))
        self.assertEqual(
            _worker(posts_engaged=5)._run_length_stop(s), "max_posts_reached"
        )
        self.assertEqual(
            _worker(posts_engaged=50)._run_length_stop(s), "max_posts_reached"
        )

    def test_max_posts_zero_is_unlimited_footgun_guard(self) -> None:
        s = M1Settings(run_length_mode="max_posts", max_posts_per_run=0)
        self.assertIsNone(_worker(posts_engaged=10_000)._run_length_stop(s))

    def test_time_boxed_fires_only_after_window(self) -> None:
        s = M1Settings(run_length_mode="time_boxed", run_time_limit_min=5)
        fresh = _worker(started_at=time.time() - 60)
        self.assertIsNone(fresh._run_length_stop(s))

        expired = _worker(started_at=time.time() - (5 * 60) - 2)
        self.assertEqual(expired._run_length_stop(s), "time_limit_reached")

    def test_time_boxed_ignores_post_counts(self) -> None:
        s = M1Settings(run_length_mode="time_boxed", run_time_limit_min=60)
        self.assertIsNone(_worker(posts_engaged=500, started_at=time.time() - 30)._run_length_stop(s))

    def test_time_boxed_without_started_at_is_safe(self) -> None:
        s = M1Settings(run_length_mode="time_boxed", run_time_limit_min=5)
        self.assertIsNone(_worker(started_at=0.0)._run_length_stop(s))

    def test_midrun_mode_switch_applies_next_cycle(self) -> None:
        s_unlimited = M1Settings(run_length_mode="unlimited", max_posts_per_run=0)
        s_timed = M1Settings(run_length_mode="time_boxed", run_time_limit_min=5)
        w = _worker(posts_engaged=100, started_at=time.time() - 3600)
        self.assertIsNone(w._run_length_stop(s_unlimited))   # before switch
        self.assertEqual(w._run_length_stop(s_timed), "time_limit_reached")  # after

    def test_legacy_shim_honors_old_max_posts_under_unlimited(self) -> None:
        s = M1Settings(run_length_mode="unlimited", max_posts_per_run=7)
        w = _worker(posts_engaged=7)
        self.assertEqual(w._run_length_stop(s), "max_posts_reached")
        self.assertTrue(w._legacy_mp_logged)  # one-shot notice fired
        # and a modern unlimited config with N=0 is unaffected
        s0 = M1Settings(run_length_mode="unlimited", max_posts_per_run=0)
        self.assertIsNone(_worker(posts_engaged=99)._run_length_stop(s0))


class TestProgressSnapshot(unittest.TestCase):
    def test_live_progress_fields_when_time_boxed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = M1SettingsStore(path=Path(td) / "s.json")
            store.update({"run_length_mode": "time_boxed", "run_time_limit_min": 5})
            w = Module1Worker(store, on_status=None)
            with w._lock:
                w._snapshot.update(started_at=time.time() - 90, phase="running")

            snap = w.snapshot()
            self.assertGreaterEqual(snap["run_time_limit_sec"], 299)
            self.assertAlmostEqual(snap["run_time_used_sec"], 90, delta=3)

    def test_no_limit_when_not_time_boxed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = M1SettingsStore(path=Path(td) / "s.json")  # default unlimited
            w = Module1Worker(store, on_status=None)
            with w._lock:
                w._snapshot.update(started_at=time.time() - 90, phase="running")

            snap = w.snapshot()
            self.assertEqual(snap["run_time_limit_sec"], 0)
            self.assertGreaterEqual(snap["run_time_used_sec"], 89)

    def test_idle_snapshot_has_progress_keys_and_runner_relays_them(self) -> None:
        from services.module_1_feed_observer.runner import Module1Runner

        with tempfile.TemporaryDirectory() as td:
            store = M1SettingsStore(path=Path(td) / "s.json")
            w = Module1Worker(store, on_status=None)
            snap = w.snapshot()
            self.assertIn("run_time_used_sec", snap)
            self.assertIn("run_time_limit_sec", snap)

            status = Module1Runner(store=store).status()
            self.assertEqual(status["run_time_used_sec"], 0)
            self.assertEqual(status["run_time_limit_sec"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
