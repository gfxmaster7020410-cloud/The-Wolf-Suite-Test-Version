"""
Module 1 — Manual Worker state machine.

One browser session: Facebook tab + one reused Gemini tab. The worker
screenshots each feed post, asks Gemini Web for the single best comment +
reaction, then engages on Facebook with human-like mechanics.

Controls (thread-safe): start / stop (instant) / pause / resume / skip-post.
Safety: hourly & daily caps, work hours window, checkpoint detection,
already-commented dedup, configurable minimum confidence.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from services.module_1_feed_observer.ai_engine import AIEngine
from services.module_1_feed_observer.config import (
    M1Settings,
    M1SettingsStore,
    VALID_REACTIONS,
    enforce_comment_quality,
    maybe_strip_tags,
)
from services.module_1_feed_observer.fb import FacebookAutomation
from services.module_1_feed_observer.gemini_bridge import GeminiWebBridge
from services.module_1_feed_observer.rotation import RotationController
from services.module_1_feed_observer.seen_store import SeenStore
from services.shared.humanization import humanization

StatusCallback = Optional[Callable[[Dict[str, Any]], None]]

# PHASE-14 (decision D31) — stop reasons that count as a NATURAL end; only
# these may leave the browser open. User Stops, errors and safety auto-stops
# (checkpoint timeout) ALWAYS close. Note: the hourly cap WAITS rather than
# stops in the current gate design, so it produces no stop_reason here.
NATURAL_END_REASONS = {"daily_cap_reached", "max_posts_reached", "time_limit_reached"}

PHASES = {
    "idle": "Waiting for you to start",
    "starting": "Launching browser",
    "fb_loading": "Opening Facebook",
    "fb_login_wait": "Facebook login needed — sign in in the browser",
    "gemini_login_wait": "Gemini login needed — sign in to Google in the browser",
    "running": "Watching your feed",
    "scanning": "Looking at a post",
    "reading": "Reading the post (human pause)",
    "skipping_video": "Video/reel handled by policy",
    "gemini": "Asking Gemini for the best comment",
    "engaging": "Commenting & reacting",
    "waiting": "Waiting before the next post",
    "outside_work_hours": "Outside work hours — waiting for the window to open",
    "cap_paused": "Daily/hourly cap reached — waiting",
    "checkpoint_paused": "Facebook security check — action paused, waiting for you",
    "ai_paused": "AI engine failing — paused",
    "switching_source": "Switching feed source",
    "browser_open": "Finished — browser left open",
    "paused": "Paused by you",
    "stopping": "Stopping…",
    "stopped": "Stopped",
    "error": "Error",
}


class _WorkerStopped(Exception):
    """Internal control-flow signal — normal stop, not a failure."""


class Module1Worker:
    def __init__(self, store: M1SettingsStore, on_status: StatusCallback = None):
        self.store = store
        self.on_status = on_status

        # Controls (thread-safe across FastAPI loop ↔ worker thread)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pause_gate = threading.Event()  # set = actions allowed
        self._pause_gate.set()
        self._paused_reason = ""
        self._skip_requested = threading.Event()
        self._phase_before_pause = "running"

        # Live state
        self._snapshot: Dict[str, Any] = self._idle_snapshot()
        # Phase to restore after a gemini_login_wait resolves (PHASE-02).
        self._phase_before_gemini_login = ""
        # One-shot notice for the legacy max_posts shim (PHASE-04).
        self._legacy_mp_logged = False
        # Sticky panel message for the checkpoint auto-stop (PHASE-11);
        # survives the stopping-phase transition into the final snapshot.
        self._terminal_note = ""

    # =================================================================== controls
    def request_stop(self) -> None:
        self._stop.set()
        self._pause_gate.set()  # never deadlock a paused worker on stop

    def request_pause(self, reason: str = "user") -> None:
        if not self._stop.is_set() and self._pause_gate.is_set():
            with self._lock:
                self._paused_reason = reason
            self._pause_gate.clear()

    def request_resume(self) -> None:
        with self._lock:
            self._paused_reason = ""
        self._pause_gate.set()

    def request_skip_post(self) -> None:
        if not self._stop.is_set():
            self._skip_requested.set()

    def is_running(self) -> bool:
        return bool(self._snapshot.get("running")) and not self._stop.is_set()

    # =================================================================== snapshot
    def _idle_snapshot(self) -> Dict[str, Any]:
        return {
            "running": False,
            "phase": "idle",
            "phase_detail": PHASES["idle"],
            "paused": False,
            "pause_reason": "",
            "stop_reason": "",
            "posts_seen": 0,
            "posts_engaged": 0,
            "posts_skipped": 0,
            "comments_done": 0,
            "likes_done": 0,
            "last_comment": "",
            "last_reaction": "",
            "last_url": "",
            "last_error": "",
            "last_ai_source": "",
            "last_ai_attempts": [],
            "gemini_errors": 0,
            "ai_fail_streak": 0,
            "ai_breaker_tripped": False,
            "hourly_used": 0,
            "hourly_cap": 0,
            "daily_used": 0,
            "daily_cap": 0,
            "seen_count": 0,
            "profile": "",
            "started_at": 0.0,
            "uptime_sec": 0,
            "run_time_used_sec": 0,
            "run_time_limit_sec": 0,
            "checkpoint_since": 0.0,
            "browser_left_open": False,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            snap = dict(self._snapshot)
        if snap.get("started_at"):
            snap["uptime_sec"] = int(time.time() - snap["started_at"])
        # PHASE-04 progress fields (computed live, zero extra writes).
        try:
            live = self.store.get()
            limit_min = int(live.run_time_limit_min) if live.run_length_mode == "time_boxed" else 0
        except Exception:
            limit_min = 0
        snap["run_time_limit_sec"] = limit_min * 60 if limit_min > 0 else 0
        started = snap.get("started_at", 0.0)
        snap["run_time_used_sec"] = max(0, int(time.time() - started)) if started else 0
        snap["paused"] = snap["phase"] in {
            "paused",
            "cap_paused",
            "checkpoint_paused",
            "ai_paused",  # PHASE-12: breaker hold gates the worker like a pause
        }
        return snap

    def _set_snapshot(self, **kwargs: Any) -> None:
        with self._lock:
            self._snapshot.update(kwargs)
            payload = dict(self._snapshot)
        if self.on_status is not None:
            try:
                self.on_status(payload)
            except Exception:
                pass

    async def _set_phase(self, phase: str, detail: Optional[str] = None) -> None:
        self._set_snapshot(
            phase=phase,
            phase_detail=detail or PHASES.get(phase, phase),
            # ai_paused (PHASE-12) carries its reason in last_error like
            # error/stopped do — wiping it would blank the panel badge text.
            last_error=(
                "" if phase not in {"error", "stopped", "ai_paused"} else self._snapshot["last_error"]
            ),
        )

    # =================================================================== gates
    async def _checkpoint(self) -> None:
        """Stop/pause gate — called before every discrete action."""
        if self._stop.is_set():
            raise _WorkerStopped()
        if not self._pause_gate.is_set():
            with self._lock:
                reason = self._paused_reason or "user"
            self._phase_before_pause = self._snapshot.get("phase", "running")
            await self._set_phase("paused", detail=f"Paused ({reason}) — click Resume to continue")
            while not self._pause_gate.is_set():
                if self._stop.is_set():
                    raise _WorkerStopped()
                await asyncio.sleep(0.2)
            await self._set_phase(self._phase_before_pause or "running")

    async def _sleep(self, seconds: float) -> None:
        """Interruptible, pause-aware sleep."""
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self._stop.is_set():
                raise _WorkerStopped()
            if not self._pause_gate.is_set():
                await self._checkpoint()
            await asyncio.sleep(min(0.25, end - time.time()))

    # =================================================================== main
    async def run(self) -> Dict[str, Any]:
        self._stop.clear()
        self._skip_requested.clear()
        self._pause_gate.set()
        self._paused_reason = ""
        settings = self.store.get()
        self._settings = settings

        self._set_snapshot(
            running=True,
            stop_reason="",
            last_error="",
            posts_seen=0,
            posts_engaged=0,
            posts_skipped=0,
            comments_done=0,
            likes_done=0,
            last_comment="",
            last_reaction="",
            last_url="",
            gemini_errors=0,
            last_ai_source="",
            last_ai_attempts=[],
            ai_fail_streak=0,
            ai_breaker_tripped=False,
            hourly_used=0,
            daily_used=0,
            hourly_cap=settings.max_actions_per_hour,
            daily_cap=settings.max_actions_per_day,
            seen_count=0,
            profile=settings.feed_url,
            started_at=time.time(),
            checkpoint_since=0.0,
            browser_left_open=False,
        )
        await self._set_phase("starting")

        self._action_times: list[float] = []
        self._capture_fail_streak = 0  # PHASE-08 consecutive bad captures
        self._ai_fail_streak = 0  # PHASE-12 consecutive AI decision failures
        self._terminal_note = ""  # PHASE-11 checkpoint auto-stop message
        stop_reason = "stopped"
        fb = FacebookAutomation(
            settings, on_phase=self._on_fb_phase, cancel_event=self._stop
        )
        engine: Optional[AIEngine] = None
        seen: Optional[SeenStore] = None

        try:
            await self._checkpoint()
            await fb.start()
            assert fb.context is not None
            await self._checkpoint()

            def _bridge_factory():
                # Fresh settings each bridge build (factory may fire lazily
                # mid-run when api_primary first falls back to Web).
                s = self.store.get()
                return GeminiWebBridge(
                    fb.context,
                    structured=True,
                    response_wait_ms=int(s.gemini_reply_wait_sec * 1000),
                    gemini_url=s.gemini_url,
                    fresh_chat=s.fresh_chat_per_post,
                    cancel_event=self._stop,
                    on_phase=self._on_gemini_phase,
                )

            engine = AIEngine(
                self.store,
                web_bridge_factory=_bridge_factory,
                cancel_event=self._stop,
            )
            if settings.ai_mode != "api_only":
                await self._set_phase(
                    "starting",
                    detail="Preparing AI engine — opening Gemini tab (reused for the whole run)",
                )
                await engine.ensure_ready()
            else:
                await self._set_phase("starting", detail="AI engine ready — REST API mode (no Gemini tab)")

            if settings.skip_already_commented:
                seen = SeenStore(
                    max_entries=settings.max_seen_posts,
                    expiry_days=settings.seen_expiry_days,
                )
                purged = seen.purge()
                if purged:
                    print(f"[M1 Seen] Purged {purged} expired entries")
                self._set_snapshot(seen_count=seen.count())

            # --- feed rotation (PHASE-13) -----------------------------
            rot = RotationController(self.store)
            first_src = rot.current()
            if first_src is not None and first_src.kind == "home":
                # fb.start() already landed us on the home feed — seed the
                # arrival so the run never double-navigates. A manual queue
                # starting with a group still navigates on the first cycle.
                rot.mark_arrived(first_src)

            await self._set_phase("running")
            await fb.scroll_to_next_post()

            while not self._stop.is_set():
                await self._checkpoint()
                settings = self.store.get()  # live config — apply immediately
                fb.settings = settings  # PHASE-06: scroll persona follows live edits
                self._apply_typing_profile(settings)

                # --- schedule gates -------------------------------------
                wh_wait = self._work_hours_wait(settings)
                if wh_wait > 0:
                    await self._set_phase("outside_work_hours")
                    await self._sleep(wh_wait)
                    continue

                cap_state, cap_wait, cap_reason = self._cap_status(settings)
                if cap_state == "stop":
                    stop_reason = cap_reason
                    break
                if cap_state == "wait":
                    await self._set_phase("cap_paused", detail=cap_reason)
                    await self._sleep(cap_wait)
                    continue

                # --- run-length gate (PHASE-04: unlimited/max_posts/time_boxed)
                length_stop = self._run_length_stop(settings)
                if length_stop:
                    stop_reason = length_stop
                    break

                # --- feed rotation (PHASE-13) -----------------------------
                # Placed AFTER the safety gates (deliberate refinement over
                # the sketch's "before schedule gates"): caps / work-hours /
                # run-length keep precedence, so a capped or exhausted run
                # never burns a navigation. Empty group list == single home
                # source -> quota flips cursor but same source, no nav.
                if rot.quota_exhausted(settings):
                    rot.advance()
                src = rot.current()
                if src is not None and rot.needs_navigation(src):
                    await self._set_phase(
                        "switching_source",
                        detail=(
                            "Moving to your news feed"
                            if src.kind == "home"
                            else f"Moving to group: {src.url}"
                        ),
                    )
                    await fb.goto_feed(src.url)
                    await self._sleep(max(2.0, float(settings.source_switch_cooldown_sec)))
                    rot.mark_arrived(src)
                    # Groups are a common checkpoint trigger — scan right
                    # after arrival (roadmap P13 requirement).
                    if settings.pause_on_checkpoint and await fb.checkpoint_detected():
                        await self._handle_checkpoint(fb, settings)

                # --- user skip -------------------------------------------
                if self._skip_requested.is_set():
                    self._skip_requested.clear()
                    await fb.scroll_to_next_post()
                    continue

                # --- security checkpoint ---------------------------------
                if settings.pause_on_checkpoint and await fb.checkpoint_detected():
                    await self._handle_checkpoint(fb, settings)

                # --- capture (PHASE-08 failure policy) --------------------
                await self._set_phase("scanning")
                shot = await fb.capture_post()
                self._set_snapshot(posts_seen=self._snapshot["posts_seen"] + 1)
                if not shot["ok"]:
                    action, self._capture_fail_streak = self._capture_failure_action(
                        settings, self._capture_fail_streak
                    )
                    if action == "retry":
                        limit = max(1, int(settings.capture_retry_limit))
                        await self._set_phase(
                            "scanning",
                            detail=(
                                "Capture failed — micro-scroll retry "
                                f"{self._capture_fail_streak}/{limit}"
                            ),
                        )
                        await fb.slow_scroll(random.randint(240, 360))
                        await self._sleep(random.uniform(1.5, 3.0))
                        continue  # loop top re-checks gates; next pass re-captures
                    await fb.scroll_to_next_post()
                    continue
                self._capture_fail_streak = 0  # success resets the streak

                url = shot.get("url")
                if url:
                    self._set_snapshot(last_url=url)
                if seen and url and seen.contains(url):
                    self._set_snapshot(posts_skipped=self._snapshot["posts_skipped"] + 1)
                    try:
                        Path(shot["screenshot"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                    await fb.scroll_to_next_post()
                    continue

                # --- video / reel policy gate (PHASE-07) -----------------
                # Dedup runs FIRST (a seen video must not be Liked again);
                # a skipped video never pollutes the seen-store.
                if shot.get("kind", "text") == "video":
                    consumed = await self._handle_video_policy(
                        fb, settings, seen, url, shot, rot=rot
                    )
                    if consumed:
                        continue
                    # policy == engage -> fall through to the AI flow below

                # --- human reading dwell --------------------------------
                await self._set_phase("reading")
                dwell = (
                    random.uniform(settings.reading_dwell_min, settings.reading_dwell_max)
                    if settings.humanization_enabled
                    else 0.3
                )
                await self._sleep(dwell)
                await self._checkpoint()

                # --- AI decision (PHASE-03 engine facade) -----------------
                await self._set_phase("gemini")
                try:
                    decision = await engine.decide(shot["screenshot"])
                    self._set_snapshot(
                        last_ai_source=decision.get("source", ""),
                        last_ai_attempts=list(decision.get("attempts", [])),
                    )
                except Exception as exc:
                    if self._stop.is_set():
                        raise _WorkerStopped()  # clean user stop, not an error
                    self._set_snapshot(
                        last_error=f"Gemini: {type(exc).__name__}: {exc}",
                        gemini_errors=self._snapshot["gemini_errors"] + 1,
                        posts_skipped=self._snapshot["posts_skipped"] + 1,
                    )
                    # PHASE-12: availability failures only (low-confidence
                    # skips are quality, never counted by the breaker).
                    self._ai_fail_streak += 1
                    self._set_snapshot(ai_fail_streak=self._ai_fail_streak)
                    await self._maybe_trip_breaker(settings)  # may hold
                    try:
                        Path(shot["screenshot"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                    await self._sleep(random.uniform(2.0, 4.0))
                    await fb.scroll_to_next_post()
                    continue
                else:
                    # PHASE-12: ANY successful decision resets the streak.
                    self._ai_fail_streak = 0
                    self._set_snapshot(ai_breaker_tripped=False)
                finally:
                    try:
                        Path(shot["screenshot"]).unlink(missing_ok=True)
                    except OSError:
                        pass

                # PHASE-10 quality pipeline: tag gating + emoji ban +
                # word ceiling — the single hard-enforcement choke point.
                comment = maybe_strip_tags(
                    decision.get("comment", ""), settings.allow_hashtags
                ).strip()
                comment = enforce_comment_quality(
                    comment,
                    emojis_allowed=settings.emoji_enabled,
                    max_words=settings.comment_max_words,
                )
                confidence = float(decision.get("confidence", 0.9))
                reaction = self._resolve_reaction(settings, decision.get("reaction", "LIKE"))

                if settings.comment_enabled and (
                    not comment or confidence < settings.min_confidence
                ):
                    self._set_snapshot(
                        posts_skipped=self._snapshot["posts_skipped"] + 1,
                        last_error=f"Low confidence ({confidence:.2f}) — skipped",
                    )
                    await fb.scroll_to_next_post()
                    continue

                # --- engage ----------------------------------------------
                # PHASE-05 cap accounting: per_post (default) counts ONE
                # action per engaged post regardless of sub-action outcomes;
                # per_action preserves the legacy per-sub-action counting.
                await self._set_phase("engaging")
                post = fb.current_post()

                did_react = False
                if reaction not in {"NONE", ""}:
                    await self._checkpoint()
                    ok_r = await fb.react(post, reaction)
                    if ok_r:
                        self._set_snapshot(likes_done=self._snapshot["likes_done"] + 1)
                    did_react = bool(ok_r)

                did_comment = False
                if settings.comment_enabled:
                    await self._checkpoint()
                    ok_c = await fb.comment(post, comment, style=settings.typing_style)
                    if ok_c:
                        self._set_snapshot(comments_done=self._snapshot["comments_done"] + 1)
                        if seen and url:
                            seen.add(url)
                            self._set_snapshot(seen_count=seen.count())
                    did_comment = bool(ok_c)

                self._record_engagement_actions(
                    settings, did_react=did_react, did_comment=did_comment
                )

                self._set_snapshot(
                    posts_engaged=self._snapshot["posts_engaged"] + 1,
                    last_comment=comment,
                    last_reaction=reaction,
                )
                rot.record_engagement()  # PHASE-13 per-source quota bookkeeping

                # --- post-checkpoint -------------------------------------
                if settings.pause_on_checkpoint and await fb.checkpoint_detected():
                    await self._handle_checkpoint(fb, settings)

                # --- long human break ------------------------------------
                if (
                    settings.long_break_every > 0
                    and self._snapshot["posts_engaged"] % settings.long_break_every == 0
                ):
                    await self._set_phase("waiting", detail="Long human-like break")
                    await self._sleep(
                        random.uniform(settings.long_break_min, settings.long_break_max)
                    )

                await self._set_phase("waiting")
                await self._sleep(
                    random.uniform(settings.gap_between_posts_min, settings.gap_between_posts_max)
                )
                await self._checkpoint()
                await fb.scroll_to_next_post()

        except _WorkerStopped:
            stop_reason = self._stop_reason_if_set() or "stopped"
        except Exception as exc:
            stop_reason = "error"
            self._set_snapshot(last_error=f"{type(exc).__name__}: {exc}")
            await self._set_phase("error")
            print(f"[M1 Worker] Fatal: {type(exc).__name__}: {exc}")
        finally:
            await self._set_phase("stopping")
            if engine is not None:
                try:
                    await engine.aclose()
                except Exception:
                    pass
            # PHASE-14 (decision D31): the Gemini tab always closes; the
            # Facebook browser may be parked open on NATURAL ends only when
            # the live policy says stay_open. Errors / Stops / safety stops
            # close regardless of policy.
            try:
                live = self.store.get()
            except Exception:
                live = settings
            if self._should_stay_open(live, stop_reason):
                await self._park_with_browser_open(fb)
            else:
                try:
                    await fb.close()
                except Exception:
                    pass
            self._set_snapshot(
                running=False,
                phase="stopped",
                phase_detail=PHASES["stopped"],
                stop_reason=stop_reason,
                # PHASE-11: the checkpoint auto-stop reason must still be
                # visible on the panel after the run has ended.
                last_error=self._terminal_note,
                browser_left_open=False,  # badge cleared once teardown lands
            )
            await self._emit_final()

        return self.snapshot()

    # =================================================================== helpers
    def _apply_typing_profile(self, settings: M1Settings) -> None:
        """Push user key-delay settings into the shared humanization engine."""
        try:
            humanization.update_profile(
                key_delay_min=max(0.0, settings.key_delay_min_ms / 1000.0),
                key_delay_max=max(0.0, settings.key_delay_max_ms / 1000.0),
            )
        except Exception:
            pass

    async def _on_fb_phase(self, phase: str) -> None:
        if phase == "fb_login_wait":
            await self._set_phase(
                phase,
                detail=(
                    "Sign in to Facebook in the browser window — "
                    "Module 1 starts automatically after login (Stop cancels)"
                ),
            )
        elif phase in {"fb_loading"}:
            await self._set_phase(phase)

    async def _on_gemini_phase(self, phase: str) -> None:
        """Bridge signal: blocked-on-Google vs resolved (PHASE-02)."""
        if phase == "gemini_login_wait":
            with self._lock:
                self._phase_before_gemini_login = self._snapshot.get("phase", "running")
            await self._set_phase(
                "gemini_login_wait",
                detail=(
                    "Sign in to Google/Gemini in the browser tab — "
                    "work resumes automatically after login (Stop cancels)"
                ),
            )
        elif phase == "gemini_ok":
            prev = self._phase_before_gemini_login
            self._phase_before_gemini_login = ""
            # Restore only when a login_wait actually preceded this signal;
            # never resurrect terminal phases.
            if prev and prev not in {"idle", "stopped", "error", "gemini_login_wait"}:
                await self._set_phase(prev)

    @staticmethod
    def _checkpoint_deadline(
        settings: M1Settings, now: Optional[float] = None
    ) -> Optional[float]:
        """
        PHASE-11 (decision D29) — absolute deadline for the checkpoint
        wait, or None when the policy says wait indefinitely.
          wait_indefinitely (default) -> None (only Stop cancels)
          wait_then_stop + sec > 0    -> now + checkpoint_wait_sec
          wait_then_stop + sec == 0   -> None (instant-stop footgun guard)
        Pure; unit-testable.
        """
        if settings.checkpoint_policy != "wait_then_stop":
            return None
        budget = max(0, int(settings.checkpoint_wait_sec))
        if budget <= 0:
            return None
        return (now if now is not None else time.time()) + budget

    async def _handle_checkpoint(self, fb: FacebookAutomation, settings: M1Settings) -> None:
        """
        PHASE-11 (decision D29) — parameterized checkpoint hold.

        wait_indefinitely (default): poll checkpoint_detected() every 3 s
        until the human clears the check or presses Stop; no deadline.
        wait_then_stop: same polling plus an auto-stop budget of
        checkpoint_wait_sec; on expiry the run ends with the exact panel
        message "Checkpoint unresolved — auto-stopped" (kept sticky through
        the stopped state via _terminal_note).

        pause_on_checkpoint=False never reaches this handler (legacy gate).
        Pause/Resume interplay preserved: a user pause inside this loop is
        honored by _checkpoint() and wall-clock keeps ticking (documented).
        """
        deadline = self._checkpoint_deadline(settings)
        self._set_snapshot(checkpoint_since=time.time())
        await self._set_phase("checkpoint_paused", detail=self._checkpoint_detail(deadline))
        try:
            while True:
                if self._stop.is_set():
                    raise _WorkerStopped()
                if not self._pause_gate.is_set():
                    await self._checkpoint()  # waits for user resume
                    continue
                if not await fb.checkpoint_detected():
                    self._set_snapshot(checkpoint_since=0.0)
                    await self._set_phase("running")
                    return
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        self._terminal_note = "Checkpoint unresolved — auto-stopped"
                        self._set_snapshot(
                            checkpoint_since=0.0,
                            last_error=self._terminal_note,
                            stop_reason="checkpoint_unresolved",
                        )
                        raise _WorkerStopped()
                    # Roadmap P11: refresh the remaining budget every poll
                    # so the dashboard timer stays live while blocked.
                    await self._set_phase(
                        "checkpoint_paused", detail=self._checkpoint_detail(deadline)
                    )
                await asyncio.sleep(3.0)
        finally:
            # Resolved, stopped or timed out — the elapsed timer never leaks.
            self._set_snapshot(checkpoint_since=0.0)

    @staticmethod
    def _checkpoint_detail(deadline: Optional[float]) -> str:
        """Panel detail text; includes remaining minutes under wait_then_stop."""
        base = "Facebook security check detected — verify in browser"
        if deadline is None:
            return base
        left = max(0.0, deadline - time.time())
        return f"{base} — auto-stop in ~{int(left // 60)} min {int(left % 60):02d}s"

    # =================================================================== PHASE-12 circuit breaker
    @staticmethod
    def _breaker_should_trip(settings: M1Settings, fail_streak: int) -> bool:
        """
        PHASE-12 (decision D30) — True when the AI failure breaker must
        fire right now. Pure; unit-testable.
          disabled                    -> never
          streak >= max(2, threshold) -> trip (floor 2: one flaky failure
                                         must not pause a run)
        """
        if not settings.circuit_breaker_enabled:
            return False
        return fail_streak >= max(2, int(settings.circuit_breaker_threshold))

    async def _maybe_trip_breaker(self, settings: M1Settings) -> None:
        """PHASE-12 — guard wrapper; holds the worker only on a real trip."""
        if not self._breaker_should_trip(settings, self._ai_fail_streak):
            return
        await self._trip_breaker(settings)

    async def _trip_breaker(self, settings: M1Settings) -> None:
        """
        PHASE-12 (decision D30) — pause the run after N consecutive AI
        decision failures and hold until:
          cooldown_min > 0  wall-clock cooldown elapses (auto-resume; an
                            explicit user Resume ends the window early)
          cooldown_min == 0 dashboard Resume pressed (manual gate)
        Stop always aborts the hold. Reuses the existing pause machinery
        (roadmap-preferred design): request_pause("ai_breaker") +
        _checkpoint() blocking, so Pause/Resume plumbing stays single-path.
        Re-trips later if AI is still broken (streak rebuilds from 0).
        """
        n = int(self._ai_fail_streak)
        cd = max(0.0, float(settings.circuit_breaker_cooldown_min)) * 60.0
        self._set_snapshot(
            ai_breaker_tripped=True,
            last_error=f"AI failed {n}x consecutively — paused",
        )
        await self._set_phase(
            "ai_paused",
            detail=(
                f"Circuit breaker: {n} consecutive AI failures — "
                + (
                    f"cooling down ~{max(1, int(round(cd / 60.0)))} min"
                    if cd > 0
                    else "fix the AI engine, then press Resume"
                )
            ),
        )
        self.request_pause("ai_breaker")  # clears the gate; Resume button works
        try:
            if cd > 0:
                deadline = time.time() + cd
                while time.time() < deadline:
                    if self._stop.is_set():
                        raise _WorkerStopped()
                    if self._pause_gate.is_set():
                        break  # owner resumed mid-cooldown
                    await asyncio.sleep(0.5)
                if not self._pause_gate.is_set():
                    self.request_resume()  # cooldown expired — release ourselves
            await self._checkpoint()  # blocks while paused; resumes/stops cleanly
        finally:
            self._ai_fail_streak = 0
            self._set_snapshot(ai_breaker_tripped=False)
        await self._set_phase("running")

    def _capture_failure_action(
        self, settings: M1Settings, fail_streak: int
    ) -> tuple[str, int]:
        """
        PHASE-08 (decision D13) — decide what happens after a FAILED
        capture. Pure; returns (action, new_streak):
          retry   micro-scroll + short wait, then re-capture the same
                  viewport region (streak incremented, capped at limit)
          advance full scroll_to_next_post; streak resets to 0

        instant_skip (default) always advances immediately.
        Owner lowering capture_retry_limit below the current streak makes
        the NEXT failure advance at once (strict `<` comparison).
        """
        limit = max(1, int(settings.capture_retry_limit))
        if (
            settings.capture_failure_policy == "retry_limited"
            and fail_streak < limit
        ):
            return "retry", fail_streak + 1
        return "advance", 0

    async def _handle_video_policy(
        self,
        fb: FacebookAutomation,
        settings: M1Settings,
        seen: Optional[SeenStore],
        url: Optional[str],
        shot: Dict[str, Any],
        rot: Optional[RotationController] = None,
    ) -> bool:
        """
        PHASE-07 — apply settings.video_policy to a classified video post.
        Returns True when the post was CONSUMED (skip / like_only) so the
        caller moves on; False when policy == engage (fall through to AI).

        like_only + reaction_mode=none is a contradiction -> the stronger
        restriction wins and the video behaves as skip.
        like_only with a failed Like click -> no seen-marking, counted as a
        skip, scroll on (roadmap edge case).
        """
        policy = settings.video_policy
        if policy == "engage":
            return False

        shot_path = Path(shot.get("screenshot") or "")
        consumed_as_skip = policy == "skip" or (
            policy == "like_only" and settings.reaction_mode == "none"
        )

        if consumed_as_skip:
            await self._set_phase("skipping_video")
            self._set_snapshot(posts_skipped=self._snapshot["posts_skipped"] + 1)
            try:
                shot_path.unlink(missing_ok=True)
            except OSError:
                pass
            await fb.scroll_to_next_post()
            return True

        # --- like_only: tap-and-go Like, no AI call, no comment ----------
        await self._set_phase("reading", detail="Video/reel — quick look before reacting")
        await self._sleep(random.uniform(1.0, 2.5))

        liked = False
        post = fb.current_post()
        if post is not None:
            await self._checkpoint()
            liked = bool(await fb.react(post, "LIKE"))

        if not liked:
            # Roadmap edge case: button missing / click failed.
            self._set_snapshot(posts_skipped=self._snapshot["posts_skipped"] + 1)
            try:
                shot_path.unlink(missing_ok=True)
            except OSError:
                pass
            await fb.scroll_to_next_post()
            return True

        self._set_snapshot(likes_done=self._snapshot["likes_done"] + 1)
        self._record_engagement_actions(settings, did_react=True, did_comment=False)

        if seen and url:
            seen.add(url)
            self._set_snapshot(seen_count=seen.count())

        self._set_snapshot(
            posts_engaged=self._snapshot["posts_engaged"] + 1,
            last_reaction="LIKE",
        )
        if rot is not None:
            rot.record_engagement()  # PHASE-13: liked videos consume quota too

        # Post-action checkpoint scan (same safety net as the main flow).
        if settings.pause_on_checkpoint and await fb.checkpoint_detected():
            await self._handle_checkpoint(fb, settings)

        # Long human break counter (mirrors main loop).
        if (
            settings.long_break_every > 0
            and self._snapshot["posts_engaged"] % settings.long_break_every == 0
        ):
            await self._set_phase("waiting", detail="Long human-like break")
            await self._sleep(
                random.uniform(settings.long_break_min, settings.long_break_max)
            )

        # Pacing gap, then advance.
        await self._set_phase("waiting")
        await self._sleep(
            random.uniform(settings.gap_between_posts_min, settings.gap_between_posts_max)
        )
        await self._checkpoint()
        await fb.scroll_to_next_post()

        try:
            shot_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    # =================================================================== PHASE-14 run-end policy
    @staticmethod
    def _should_stay_open(settings: M1Settings, stop_reason: str) -> bool:
        """
        PHASE-14 (decision D31) — True ONLY for a NATURAL end (cap /
        max-posts / time-boxed) while the live policy is stay_open.
        Pure; unit-testable. Everything else closes the browser.
        """
        if (getattr(settings, "run_end_policy", "") or "close_browser") != "stay_open":
            return False
        return stop_reason in NATURAL_END_REASONS

    async def _park_with_browser_open(self, fb: FacebookAutomation) -> None:
        """
        PHASE-14 — keep the worker thread ALIVE in a light loop so the
        Playwright objects are not torn down under a user still poking at
        the parked browser. Exits when: dashboard Stop pressed, OR the user
        closes the window themselves (page_closed probe). Always closes
        cleanly on the way out. Crash-during-park: the runner's job wrapper
        catches; worst case the browser leaks until process exit (accepted,
        documented).
        """
        self._set_snapshot(
            running=False,  # panel shows "Finished" immediately
            phase="browser_open",
            phase_detail=(
                "Run complete — browser left open on your feed. "
                "Press Stop or close the window to release it."
            ),
            browser_left_open=True,
        )
        print("[M1 Worker] Natural end — browser left open (run_end_policy=stay_open).")
        try:
            while not self._stop.is_set():
                if fb.page_closed():
                    break  # user closed the window themselves
                await asyncio.sleep(1.0)
        finally:
            try:
                await fb.close()
            except Exception:
                pass

    def _stop_reason_if_set(self) -> str:
        return str(self._snapshot.get("stop_reason") or "")

    def _resolve_reaction(self, settings: M1Settings, ai_reaction: str) -> str:
        mode = settings.reaction_mode
        if mode == "none":
            return "NONE"
        if mode == "fixed":
            return settings.fixed_reaction
        r = str(ai_reaction or "LIKE").upper().strip()
        return r if r in VALID_REACTIONS else "LIKE"

    def _record_engagement_actions(
        self, settings: M1Settings, *, did_react: bool, did_comment: bool
    ) -> None:
        """
        PHASE-05 (decision D28) — cap accounting for ONE engaged post.
          per_post  (default): one post == one action; a reaction, a comment,
                      or both on the same post consumes exactly 1 unit.
          per_action         : legacy semantics — each successful react and
                      each successful comment counts separately.
        A post where everything failed counts nothing in either mode (and is
        deliberately NOT marked seen, so a later run may retry it).
        """
        if settings.action_count_mode == "per_action":
            if did_react:
                self._record_action(settings)
            if did_comment:
                self._record_action(settings)
            return
        # per_post (default)
        if did_react or did_comment:
            self._record_action(settings)

    def _record_action(self, settings: M1Settings) -> None:
        self._action_times.append(time.time())
        self._action_times = [
            t for t in self._action_times if time.time() - t < 86400
        ]
        hourly = len([t for t in self._action_times if time.time() - t < 3600])
        self._set_snapshot(
            hourly_used=hourly,
            daily_used=len(self._action_times),
            hourly_cap=settings.max_actions_per_hour,
            daily_cap=settings.max_actions_per_day,
        )

    def _cap_status(self, settings: M1Settings) -> tuple[str, float, str]:
        now = time.time()
        self._action_times = [t for t in self._action_times if now - t < 86400]
        daily = len(self._action_times)
        hourly = len([t for t in self._action_times if now - t < 3600])

        if settings.max_actions_per_day > 0 and daily >= settings.max_actions_per_day:
            return "stop", 0.0, "daily_cap_reached"

        if settings.max_actions_per_hour > 0 and hourly >= settings.max_actions_per_hour:
            oldest = min(self._action_times) if self._action_times else now
            wait = max(0.0, oldest + 3600 - now)
            mins = int(wait // 60)
            return "wait", wait, f"Hourly cap reached — resuming in ~{mins} min"

        return "ok", 0.0, ""

    def _run_length_stop(self, settings: M1Settings) -> Optional[str]:
        """
        PHASE-04 — returns a stop_reason when the run-length mode is
        exhausted, else None. Pure (no IO) so it is unit-testable.

        Modes:
          unlimited   no stop from this gate
                      (legacy shim: max_posts_per_run>0 still honored so
                      pre-v2 configs keep their behavior; logged once)
          max_posts   stop at N engaged posts; N==0 treated as unlimited
                      regardless of mode (instant-stop footgun guard)
          time_boxed  stop when wall-clock elapsed >= run_time_limit_min;
                      pauses count toward the window (documented)
        Cap gates above keep precedence over any run-length stop.
        """
        engaged = self._snapshot.get("posts_engaged", 0)

        if settings.run_length_mode == "max_posts":
            if settings.max_posts_per_run > 0 and engaged >= settings.max_posts_per_run:
                return "max_posts_reached"
            return None

        if settings.run_length_mode == "time_boxed":
            started = self._snapshot.get("started_at", 0.0)
            if started > 0:
                limit_sec = max(1, int(settings.run_time_limit_min)) * 60
                if time.time() - started >= limit_sec:
                    return "time_limit_reached"
            return None

        # unlimited (+ backward-compatibility shim)
        if settings.max_posts_per_run > 0 and engaged >= settings.max_posts_per_run:
            if not self._legacy_mp_logged:
                self._legacy_mp_logged = True
                print(
                    f"[M1 Worker] Legacy compat: max_posts_per_run="
                    f"{settings.max_posts_per_run} honored under "
                    f"run_length_mode=unlimited — set mode to 'max_posts' to make it explicit."
                )
            return "max_posts_reached"
        return None

    @staticmethod
    def _work_hours_wait(settings: M1Settings) -> float:
        start, end = settings.work_hours_start, settings.work_hours_end
        if not start or not end:
            return 0.0
        try:
            now = datetime.now()
            cur = now.hour * 60 + now.minute
            st = int(start.split(":")[0]) * 60 + int(start.split(":")[1])
            en = int(end.split(":")[0]) * 60 + int(end.split(":")[1])
        except (ValueError, IndexError):
            return 0.0
        if st == en:
            return 0.0
        if st < en:
            if st <= cur < en:
                return 0.0
            if cur < st:
                return (st - cur) * 60
            return (24 * 60 - cur + st) * 60
        # overnight window (e.g. 22:00 → 06:00)
        if cur >= st or cur < en:
            return 0.0
        return (st - cur) * 60

    async def _emit_final(self) -> None:
        # Final callback so the runner's cached snapshot is always fresh
        pass
