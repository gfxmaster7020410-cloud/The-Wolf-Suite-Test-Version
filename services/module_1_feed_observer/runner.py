"""
Module 1 — background runner (Start / Stop / Pause / Resume / Skip + config).

Thread-safe singleton used by the FastAPI endpoints. The Playwright worker
runs on a Proactor-safe worker thread (Windows + uvicorn compatible).
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from services.module_1_feed_observer.config import M1SettingsStore, settings_store
from services.module_1_feed_observer.worker import Module1Worker
from services.shared.async_runtime import await_playwright_job

# PHASE-15 (decision D32) — settings whose change must wake the scheduler.
SCHED_KEYS = {"schedule_mode", "daily_start_time", "interval_run_hours", "interval_sleep_hours"}
_SCHED_TICK_SEC = 10.0  # reconfig-responsiveness slice size


class Module1Runner:
    """Singleton controller exposed to main.py endpoints."""

    def __init__(self, store: Optional[M1SettingsStore] = None) -> None:
        self.store = store or settings_store
        self._lock = threading.Lock()
        self._worker: Optional[Module1Worker] = None
        self._task: Optional[asyncio.Task] = None
        self._latest: Dict[str, Any] = {}
        # --- PHASE-15 scheduler state ---
        self._sched_task: Optional[asyncio.Task] = None
        self._sched_gen = 0  # bumped on schedule-setting changes; aborts sleeps
        self._last_fire_date: Optional[Any] = None  # date of last daily_at fire
        self._last_auto_start = ""
        self._next_action_iso = ""

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, Any]:
        self._ensure_scheduler()  # PHASE-15 lazy bootstrap (dashboard poll)
        with self._lock:
            worker = self._worker
            cached = dict(self._latest)
        return self._compose_status(worker, cached)

    def _compose_status(self, worker: Optional[Module1Worker], cached: Dict[str, Any]) -> Dict[str, Any]:
        """Status dict from already-read values (never called while holding
        the runner lock — worker.snapshot() may block on the worker lock)."""
        if worker is not None:
            snap = worker.snapshot()
            self._latest = dict(snap)
            out: Dict[str, Any] = {**snap, "settings": self.store.dump()}
        else:
            out = {
            "running": False,
            "phase": cached.get("phase", "idle"),
            "phase_detail": cached.get("phase_detail", "Idle — configure and start"),
            "paused": False,
            "stop_reason": cached.get("stop_reason", ""),
            "posts_seen": cached.get("posts_seen", 0),
            "posts_engaged": cached.get("posts_engaged", 0),
            "posts_skipped": cached.get("posts_skipped", 0),
            "comments_done": cached.get("comments_done", 0),
            "likes_done": cached.get("likes_done", 0),
            "last_comment": cached.get("last_comment", ""),
            "last_reaction": cached.get("last_reaction", ""),
            "last_url": cached.get("last_url", ""),
            "last_error": cached.get("last_error", ""),
            "last_ai_source": cached.get("last_ai_source", ""),
            "last_ai_attempts": cached.get("last_ai_attempts", []),
            "gemini_errors": cached.get("gemini_errors", 0),
            "ai_fail_streak": cached.get("ai_fail_streak", 0),
            "ai_breaker_tripped": cached.get("ai_breaker_tripped", False),
            "hourly_used": cached.get("hourly_used", 0),
            "hourly_cap": cached.get("hourly_cap", 0),
            "daily_used": cached.get("daily_used", 0),
            "daily_cap": cached.get("daily_cap", 0),
            "seen_count": cached.get("seen_count", 0),
            "profile": cached.get("profile", ""),
            "uptime_sec": cached.get("uptime_sec", 0),
            "run_time_used_sec": cached.get("run_time_used_sec", 0),
            "run_time_limit_sec": cached.get("run_time_limit_sec", 0),
            "checkpoint_since": cached.get("checkpoint_since", 0.0),
            "browser_left_open": cached.get("browser_left_open", False),
            "settings": self.store.dump(),
        }
        # PHASE-15 scheduler surface (getattr-defensive: bare test doubles
        # constructed via __new__ may predate these attrs).
        try:
            out["schedule_mode"] = self.store.get().schedule_mode
        except Exception:
            pass
        out["next_scheduled_action"] = getattr(self, "_next_action_iso", "")
        out["last_auto_start"] = getattr(self, "_last_auto_start", "")
        return out

    def config(self) -> Dict[str, Any]:
        self._ensure_scheduler()
        return self.store.dump()

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        result = self.store.update(patch)
        # PHASE-15: schedule-related edits wake the sleeping scheduler so new
        # params apply within ~one 10 s slice (roadmap responsiveness rule).
        try:
            if SCHED_KEYS & set(patch or {}):
                self._kick_scheduler()
        except Exception:
            pass
        return result

    async def test_ai_engine(self) -> Dict[str, Any]:
        """
        PHASE-03 — verify the configured Gemini REST key WITHOUT starting a
        run or opening a browser. Sends a 1x1 PNG with a trivial structured
        prompt; returns {ok, latency_ms, key_configured, ...}.
        """
        import base64
        import time

        from services.module_1_feed_observer.gemini_api import generate_engagement_v2

        s = self.store.get()
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        tmp = Path(__file__).resolve().parents[2] / "temp"
        tmp.mkdir(parents=True, exist_ok=True)
        shot = tmp / f"m1_ai_test_{int(time.time() * 1000)}.png"
        shot.write_bytes(base64.b64decode(png_b64))
        started = time.perf_counter()
        try:
            await generate_engagement_v2(
                str(shot),
                api_key=s.gemini_api_key or None,
                model=s.gemini_api_model or None,
                prompt='Look at this image. Reply ONLY JSON: {"reaction":"LIKE","comment":"ok"}',
                structured=True,
            )
            return {
                "ok": True,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "source": "gemini_api",
                "key_configured": bool(s.gemini_api_key),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "key_configured": bool(s.gemini_api_key),
            }
        finally:
            try:
                shot.unlink(missing_ok=True)
            except OSError:
                pass

    def _on_status(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._latest = dict(payload)

    # ------------------------------------------------------------------ control
    async def start(self) -> Dict[str, Any]:
        self._ensure_scheduler()
        with self._lock:
            existing = self._worker
            task = self._task
            cached = dict(self._latest)
        if existing is not None and existing.is_running():
            # compose_status must run outside the runner lock (worker snapshot)
            return {
                "ok": False,
                "error": "Module 1 is already running",
                "status": self._compose_status(existing, cached),
            }

        # PHASE-14 adoption path: a parked worker (phase=browser_open after a
        # natural end under run_end_policy=stay_open) still owns the Playwright
        # objects. Stop it and give it <=5 s to tear down before a new run
        # adopts the slot — without this, two workers would fight over one
        # browser. Session reuse across runs BENEFITS login state.
        if (
            existing is not None
            and task is not None
            and not task.done()
            and existing.snapshot().get("phase") == "browser_open"
        ):
            existing.request_stop()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except Exception:
                pass  # proceed anyway; old thread finishes closing on its own

        worker = Module1Worker(self.store, on_status=self._on_status)
        with self._lock:
            self._worker = worker
        # snapshot() must never be called while holding the runner lock:
        # the worker thread may hold the worker lock and block on ours (deadlock).
        self._latest = worker.snapshot()

        async def _job() -> None:
            try:
                await await_playwright_job(lambda: worker.run())
            except Exception as exc:
                self._on_status(
                    {
                        "running": False,
                        "phase": "error",
                        "phase_detail": "Worker crashed",
                        "last_error": f"{type(exc).__name__}: {exc}",
                    }
                )

        self._task = asyncio.create_task(_job())
        return {"ok": True, "message": "Module 1 started (Gemini Web mode)", **self.status()}

    async def stop(self) -> Dict[str, Any]:
        self._ensure_scheduler()
        worker = self._worker
        if worker is None:
            self._on_status({"running": False, "phase": "idle"})
            return {"ok": True, "message": "Already stopped", **self.status()}

        worker.request_stop()
        # Give the loop a moment to exit cleanly (browser close included)
        for _ in range(120):
            if not self._snapshot_running(worker):
                break
            await asyncio.sleep(0.25)
        return {"ok": True, "message": "Stop requested", **self.status()}

    async def pause(self, reason: str = "user") -> Dict[str, Any]:
        worker = self._worker
        if worker is None or not worker.is_running():
            return {"ok": False, "error": "Module 1 is not running", **self.status()}
        worker.request_pause(reason)
        return {"ok": True, "message": "Paused — worker will stop acting", **self.status()}

    async def resume(self) -> Dict[str, Any]:
        worker = self._worker
        if worker is None or not worker.is_running():
            return {"ok": False, "error": "Module 1 is not running", **self.status()}
        worker.request_resume()
        return {"ok": True, "message": "Resumed", **self.status()}

    async def skip_post(self) -> Dict[str, Any]:
        worker = self._worker
        if worker is None or not worker.is_running():
            return {"ok": False, "error": "Module 1 is not running", **self.status()}
        worker.request_skip_post()
        return {"ok": True, "message": "Skipping the current post", **self.status()}

    def _snapshot_running(self, worker: Module1Worker) -> bool:
        return bool(worker.snapshot().get("running"))

    # ================================================================== PHASE-15 scheduler
    def _ensure_scheduler(self) -> None:
        """Boot the scheduler task on the running loop (lazy bootstrap).
        Sync-safe: silently skips when called from a thread without a loop —
        the next async entry point (or dashboard poll) boots it instead."""
        try:
            task = self._sched_task
        except AttributeError:
            return  # bare __new__ test double — no scheduler state at all
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._sched_task = loop.create_task(self._scheduler_loop())

    def _kick_scheduler(self) -> None:
        """Bump the generation so in-flight sleeps abort within one slice,
        then make sure a scheduler task exists."""
        with self._lock:
            self._sched_gen += 1
        self._ensure_scheduler()

    def _worker_running(self) -> bool:
        """True while a worker run is live (paused/breaker counts as active —
        cooldown and manual Resume govern it; roadmap P15 note)."""
        try:
            worker = self._worker
            return bool(worker is not None and worker.snapshot().get("running"))
        except Exception:
            return False

    def _set_next_action(self, when: Optional[datetime], label: str = "") -> None:
        try:
            self._next_action_iso = (
                f"{when.isoformat(timespec='seconds')}{(' ' + label) if label else ''}"
                if when is not None
                else ""
            )
        except Exception:
            self._next_action_iso = ""

    async def _sleep_or_reconfig(self, seconds: float) -> bool:
        """
        Interruptible sleep sliced to _SCHED_TICK_SEC: returns False as soon
        as a schedule-setting change bumped the generation, True after the
        full window. Never sleeps stale 4-hour windows past an owner edit.
        """
        deadline = time.time() + max(0.0, float(seconds))
        gen = self._sched_gen
        while True:
            if self._sched_gen != gen:
                return False
            remaining = deadline - time.time()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(_SCHED_TICK_SEC, remaining))

    def _seconds_until(self, hhmm: str) -> float:
        """Seconds until the next HH:MM local occurrence. Invalid input ->
        retry quietly in an hour (never fatal)."""
        try:
            h, m = map(int, str(hhmm or "").strip().split(":"))
            assert 0 <= h <= 23 and 0 <= m <= 59
        except Exception:
            return 3600.0
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _auto_start(self, source: str) -> bool:
        """Scheduler-initiated start; records last_auto_start on success."""
        try:
            result = await self.start()
            ok = bool(result.get("ok"))
            error = str(result.get("error", ""))
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"
        if ok:
            self._last_auto_start = (
                f"{datetime.now().isoformat(timespec='seconds')} ({source})"
            )
            print(f"[M1 Scheduler] auto-started run ({source})")
        else:
            print(f"[M1 Scheduler] {source} auto-start declined: {error}")
        return ok

    async def _scheduler_loop(self) -> None:
        """
        PHASE-15 (decision D32) — automation heart.
          manual      : never starts anything (acceptance criterion)
          daily_at    : fires once per local day at HH:MM if idle; missed
                        windows (PC asleep) are SKIPPED to the next day;
                        empty/invalid time idles safely
          interval    : start -> run interval_run_hours -> graceful stop ->
                        sleep interval_sleep_hours -> repeat (validator keeps
                        both >= 0.25h so this can never hot-loop); a manual
                        run mid-window IS stopped by natural expiry only
        P14 interplay: stop() + adoption path handle parked browsers; a
        paused/breaker-held run counts as active (no double-start).
        DST: naive local recomputation each cycle self-corrects (+-1h edge
        documented as accepted).
        """
        print("[M1 Scheduler] started")
        try:
            while True:
                s = self.store.get()
                mode = (getattr(s, "schedule_mode", "manual") or "manual").strip().lower()

                if mode == "manual":
                    self._set_next_action(None)
                    await self._sleep_or_reconfig(30)
                    continue

                if mode == "daily_at":
                    hhmm = (s.daily_start_time or "").strip()
                    if not hhmm:
                        self._set_next_action(None)
                        await self._sleep_or_reconfig(30)
                        continue
                    delay = self._seconds_until(hhmm)
                    self._set_next_action(
                        datetime.now() + timedelta(seconds=delay), "(daily_at)"
                    )
                    await self._sleep_or_reconfig(delay)
                    # Re-read: owner may have flipped modes during the wait.
                    cur = self.store.get()
                    if (cur.schedule_mode or "").strip().lower() != "daily_at":
                        continue
                    now = datetime.now()
                    today = now.date()
                    if self._last_fire_date == today:
                        # double-fire guard nap (reconfig-abortable)
                        await self._sleep_or_reconfig(60)
                        continue
                    if self._worker_running():
                        print("[M1 Scheduler] daily_at reached — run already active, skipping")
                        self._last_fire_date = today
                        await self._sleep_or_reconfig(60)
                        continue
                    self._last_fire_date = today
                    await self._auto_start("daily_at")
                    await self._sleep_or_reconfig(60)
                    continue

                # --- interval ---
                run_sec = max(0.25, float(s.interval_run_hours)) * 3600.0
                sleep_sec = max(0.25, float(s.interval_sleep_hours)) * 3600.0
                if not self._worker_running():
                    await self._auto_start("interval")
                else:
                    print("[M1 Scheduler] interval found an active run — timing it out")
                self._set_next_action(
                    datetime.now() + timedelta(seconds=run_sec), "(interval run end)"
                )
                aborted = not await self._sleep_or_reconfig(run_sec)
                if aborted:
                    continue  # settings changed — outer loop re-reads fresh values
                if self._worker_running():
                    print("[M1 Scheduler] interval run window expired — requesting stop")
                    try:
                        await self.stop()
                    except Exception as exc:
                        print(f"[M1 Scheduler] interval stop failed: {exc}")
                self._set_next_action(
                    datetime.now() + timedelta(seconds=sleep_sec), "(interval sleep)"
                )
                await self._sleep_or_reconfig(sleep_sec)
                # loop top re-evaluates everything against live settings
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[M1 Scheduler] crashed: {type(exc).__name__}: {exc}")


# Process-wide singleton
module1_runner = Module1Runner()
