"""Unit tests for Module 1 PHASE-15 — scheduler (manual / daily_at / interval).

The scheduler loop is driven against stub stores/recorders with an
exhaustion-sentinel sleeper, so scenarios are deterministic and instant:
manual never auto-starts; daily_at fires once per day and skips when a
run is active or the time field is empty; interval alternates
start -> run-window -> graceful stop -> sleep; any schedule-setting edit
aborts in-flight sleeps within one tick via the generation counter.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest import mock

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.runner import SCHED_KEYS, Module1Runner


class Done(Exception):
    """Sentinel: fake sleeper exhaustion -> loop unwinds through its guard."""


class StubStore:
    def __init__(self, **kw):
        self.data = dict(kw)

    def get(self) -> M1Settings:
        return M1Settings(**self.data)

    def update(self, patch: Optional[Dict[str, Any]]):
        self.data.update(patch or {})
        return {"ok": True, "warnings": []}

    def dump(self) -> Dict[str, Any]:
        return {}


class FakeWorker:
    def __init__(self, running: bool = False):
        self.running = running

    def snapshot(self) -> Dict[str, Any]:
        return {"running": self.running}


def make_runner(settings: M1Settings) -> Module1Runner:
    return Module1Runner(StubStore(**settings.model_dump()))


def exhaust_sleeper(recorder: List[float], fail_after: int):
    state = {"n": 0}

    async def sleeper(seconds: float) -> bool:
        recorder.append(float(seconds))
        state["n"] += 1
        if state["n"] >= fail_after:
            raise Done()
        return True

    return sleeper


class SchedulerScenario(unittest.TestCase):
    def _run_loop(self, r: Module1Runner, fail_after: int = 3) -> List[float]:
        delays: List[float] = []
        r._sleep_or_reconfig = exhaust_sleeper(delays, fail_after)  # type: ignore[method-assign]
        try:
            asyncio.run(r._scheduler_loop())
        except Done:
            pass
        return delays

    # ------------------------------------------------------------ manual
    def test_manual_mode_never_auto_starts_anything(self) -> None:
        r = make_runner(M1Settings())  # manual default
        starts: List[str] = []

        async def fake_auto_start(source: str) -> bool:
            starts.append(source)
            return True

        r._auto_start = fake_auto_start  # type: ignore[method-assign]
        delays = self._run_loop(r, fail_after=3)

        self.assertEqual(starts, [])
        self.assertTrue(all(d == 30.0 for d in delays))
        self.assertEqual(r._next_action_iso, "")

    # ------------------------------------------------------------ interval
    def test_interval_cycles_start_window_stop_sleep(self) -> None:
        s = M1Settings(
            schedule_mode="interval", interval_run_hours=0.25, interval_sleep_hours=0.5
        )
        r = make_runner(s)
        r._worker = FakeWorker(False)
        starts: List[str] = []
        stops: List[int] = []

        async def fake_auto_start(source: str) -> bool:
            starts.append(source)
            r._worker.running = True
            from datetime import datetime

            r._last_auto_start = f"{datetime.now().isoformat(timespec='seconds')} ({source})"
            return True

        async def fake_stop() -> Dict[str, Any]:
            stops.append(1)
            r._worker.running = False
            return {"ok": True}

        r._auto_start = fake_auto_start  # type: ignore[assignment]
        r.stop = fake_stop  # type: ignore[method-assign]

        delays = self._run_loop(r, fail_after=5)

        # acceptance: continuous alternation without overlap —
        # start -> run window -> graceful stop -> sleep window -> repeat
        self.assertEqual(delays[:4], [0.25 * 3600, 0.5 * 3600, 0.25 * 3600, 0.5 * 3600])
        self.assertTrue(all(s == "interval" for s in starts))
        self.assertEqual(len(starts), len(stops) + 1)  # each cycle pairs them
        self.assertIn("(interval)", r._last_auto_start)

    def test_real_auto_start_records_timestamp_on_success(self) -> None:
        async def scenario():
            r = Module1Runner(StubStore())

            async def fake_start() -> Dict[str, Any]:
                return {"ok": True}

            r.start = fake_start  # type: ignore[method-assign]
            ok = await r._auto_start("daily_at")
            self.assertTrue(ok)
            self.assertIn("(daily_at)", r._last_auto_start)

            async def failing_start() -> Dict[str, Any]:
                return {"ok": False, "error": "already running"}

            r.start = failing_start  # type: ignore[method-assign]
            ok2 = await r._auto_start("interval")
            self.assertFalse(ok2)

        asyncio.run(scenario())

    def test_interval_never_hot_loops_below_quarter_hour(self) -> None:
        s = M1Settings(schedule_mode="interval")
        self.assertGreaterEqual(s.interval_run_hours, 0.25)
        self.assertGreaterEqual(s.interval_sleep_hours, 0.25)

    # ------------------------------------------------------------ daily_at
    def test_daily_at_fires_once_per_day_then_holds(self) -> None:
        s = M1Settings(schedule_mode="daily_at", daily_start_time="23:59")
        r = make_runner(s)
        r._worker = FakeWorker(False)
        r._seconds_until = lambda hhmm: 3600.0  # type: ignore[method-assign]
        starts: List[str] = []

        async def fake_auto_start(source: str) -> bool:
            starts.append(source)
            return True

        r._auto_start = fake_auto_start  # type: ignore[method-assign]

        delays = self._run_loop(r, fail_after=3)

        self.assertEqual(starts, ["daily_at"])  # acceptance: ONE fire
        # wait for target -> post-fire guard nap -> next-day delay recompute
        self.assertEqual(delays[:2], [3600.0, 60.0])
        self.assertIsNotNone(r._last_fire_date)

    def test_daily_at_skips_when_run_already_active(self) -> None:
        s = M1Settings(schedule_mode="daily_at", daily_start_time="23:59")
        r = make_runner(s)
        r._worker = FakeWorker(True)  # mid-run
        r._seconds_until = lambda hhmm: 60.0  # type: ignore[method-assign]
        starts: List[str] = []

        async def fake_auto_start(source: str) -> bool:
            starts.append(source)
            return True

        r._auto_start = fake_auto_start  # type: ignore[method-assign]

        self._run_loop(r, fail_after=2)
        self.assertEqual(starts, [])

    def test_daily_at_empty_time_idles_safely(self) -> None:
        r = make_runner(M1Settings(schedule_mode="daily_at", daily_start_time=""))
        starts: List[str] = []

        async def fake_auto_start(source: str) -> bool:
            starts.append(source)
            return True

        r._auto_start = fake_auto_start  # type: ignore[method-assign]
        delays = self._run_loop(r, fail_after=2)
        self.assertEqual(starts, [])
        self.assertEqual(delays, [30.0, 30.0])
        self.assertEqual(r._next_action_iso, "")

    def test_mode_flip_during_daily_wait_aborts_without_firing(self) -> None:
        store = StubStore(schedule_mode="daily_at", daily_start_time="05:00")
        r = Module1Runner(store)
        starts: List[str] = []
        calls = {"n": 0}

        async def fake_auto_start(source: str) -> bool:
            starts.append(source)
            return True

        async def reconfig_sleeper(seconds: float) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                # owner edits schedule settings while the daily wait runs:
                # generation bump aborts the sleep AND the store now reads manual
                store.data["schedule_mode"] = "manual"
                return False
            raise Done()

        r._auto_start = fake_auto_start  # type: ignore[method-assign]
        r._sleep_or_reconfig = reconfig_sleeper  # type: ignore[method-assign]

        try:
            asyncio.run(r._scheduler_loop())
        except Done:
            pass

        self.assertEqual(starts, [])  # never fired despite the elapsed window

    # ------------------------------------------------------------ pure bits


class TestSecondsUntil(unittest.TestCase):
    def test_future_time_today_positive_under_24h(self) -> None:
        r = Module1Runner(StubStore())
        sec = r._seconds_until("23:59")
        self.assertGreater(sec, 0)
        self.assertLessEqual(sec, 24 * 3600)

    def test_past_time_rolls_to_tomorrow(self) -> None:
        r = Module1Runner(StubStore())
        # deterministic: a time 2 minutes ago must land ~24h minus 4 min out
        past = (datetime.now() - timedelta(minutes=2)).strftime("%H:%M")
        sec = r._seconds_until(past)
        self.assertGreater(sec, 23 * 3600)
        self.assertLessEqual(sec, 24 * 3600 + 120)

    def test_future_time_two_minutes_out_is_exactish(self) -> None:
        r = Module1Runner(StubStore())
        soon = (datetime.now() + timedelta(minutes=2)).strftime("%H:%M")
        sec = r._seconds_until(soon)
        self.assertGreater(sec, 0)
        self.assertLessEqual(sec, 130)

    def test_invalid_inputs_retry_in_an_hour(self) -> None:
        r = Module1Runner(StubStore())
        for bad in ("", "abc", "25:00", "12:99", "12"):
            self.assertEqual(r._seconds_until(bad), 3600.0, bad)


class TestSleepOrReconfig(unittest.TestCase):
    def test_full_sleep_returns_true(self) -> None:
        r = Module1Runner(StubStore())
        self.assertTrue(asyncio.run(r._sleep_or_reconfig(0.15)))

    def test_generation_bump_aborts_within_a_tick(self) -> None:
        import threading

        r = Module1Runner(StubStore())

        def bump():
            with r._lock:
                r._sched_gen += 1

        timer = threading.Timer(0.03, bump)
        timer.start()
        try:
            aborted = not asyncio.run(r._sleep_or_reconfig(5.0))
        finally:
            timer.cancel()
        self.assertTrue(aborted)


class TestBootstrapAndSurface(unittest.TestCase):
    def test_update_config_bumps_generation_and_stays_sync(self) -> None:
        r = Module1Runner(StubStore())
        gen0 = r._sched_gen
        result = r.update_config({"schedule_mode": "daily_at"})
        self.assertTrue(result["ok"])
        self.assertEqual(r._sched_gen, gen0 + 1)
        self.assertIsNone(r._sched_task)  # no running loop here — lazy only

    def test_status_bootstraps_task_on_running_loop(self) -> None:
        async def scenario():
            r = Module1Runner(StubStore())
            r.status()  # sync call inside a live loop must create the task
            task = r._sched_task
            self.assertIsNotNone(task)
            self.assertFalse(task.done())
            task.cancel()

        asyncio.run(scenario())

    def test_compose_status_relays_schedule_fields_defensively(self) -> None:
        r = Module1Runner.__new__(Module1Runner)  # bare double, no __init__
        r.store = StubStore()
        out = r._compose_status(None, {})
        self.assertIn("schedule_mode", out)
        self.assertEqual(out["next_scheduled_action"], "")
        self.assertEqual(out["last_auto_start"], "")

    def test_sched_keys_registry(self) -> None:
        self.assertEqual(
            SCHED_KEYS,
            {"schedule_mode", "daily_start_time", "interval_run_hours", "interval_sleep_hours"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
