"""
Wolf Suite — Humanization Engine
Central configuration so all modules behave with human-like pacing,
cursor motion, typing cadence, reading dwell, warm-up, and Spintax variation.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class HumanizationProfile:
    """Tunable human-behavior profile (defaults match SOFTWARE_SPECIFICATION)."""

    # Mouse / Bezier
    bezier_points: int = 28
    bezier_offset_min: float = 0.15
    bezier_offset_max: float = 0.40
    bezier_angle_jitter: float = 0.65
    mouse_jitter_px: float = 2.0
    mouse_step_delay_min: float = 0.008
    mouse_step_delay_max: float = 0.022
    reaction_decision_pause_min: float = 0.40
    reaction_decision_pause_max: float = 0.90

    # Typing (seconds)
    key_delay_mean: float = 0.14
    key_delay_std: float = 0.035
    key_delay_min: float = 0.08
    key_delay_max: float = 0.22
    typo_probability: float = 0.03
    punctuation_pause_min: float = 0.40
    punctuation_pause_max: float = 0.85
    reflection_pause_min: float = 1.2
    reflection_pause_max: float = 2.8

    # Reading / dwell
    base_read_delay_min: float = 1.5
    base_read_delay_max: float = 3.5
    ms_per_word: float = 0.28
    image_complexity_bonus_min: float = 0.8
    image_complexity_bonus_max: float = 2.5
    post_scroll_dwell_min: float = 3.5
    post_scroll_dwell_max: float = 7.0
    scroll_delta_min: int = 420
    scroll_delta_max: int = 780

    # Inter-action micro delays (Module 3 / API)
    action_delay_min: float = 1.5
    action_delay_max: float = 5.5
    dm_to_reply_delay_min: float = 2.0
    dm_to_reply_delay_max: float = 4.0

    # Group share pacing (Module 2 Phase 4)
    group_share_delay_min: float = 45.0
    group_share_delay_max: float = 120.0

    # Rate limits (human-like daily ceilings)
    rate_bucket_capacity: int = 20
    rate_refill_per_sec: float = 0.15
    max_actions_per_hour: int = 35
    max_actions_per_day: int = 180

    # Warm-up (14-day curve)
    warmup_days: int = 14
    warmup_start_fraction: float = 0.15
    account_start_date: str = field(default_factory=lambda: date.today().isoformat())

    # Profiles
    profile_name: str = "natural"
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "HumanizationProfile":
        p = cls()
        p.profile_name = os.getenv("HUMAN_PROFILE", "natural")
        p.enabled = os.getenv("HUMANIZATION_ENABLED", "true").lower() in {"1", "true", "yes"}
        p.account_start_date = os.getenv("ACCOUNT_WARMUP_START", p.account_start_date)

        # Presets
        if p.profile_name == "cautious":
            p.key_delay_mean = 0.18
            p.action_delay_min = 2.5
            p.action_delay_max = 7.0
            p.max_actions_per_hour = 18
            p.max_actions_per_day = 90
            p.group_share_delay_min = 60.0
            p.group_share_delay_max = 150.0
            p.rate_bucket_capacity = 12
            p.rate_refill_per_sec = 0.08
        elif p.profile_name == "active":
            p.key_delay_mean = 0.11
            p.action_delay_min = 1.2
            p.action_delay_max = 3.5
            p.max_actions_per_hour = 50
            p.max_actions_per_day = 250
            p.group_share_delay_min = 35.0
            p.group_share_delay_max = 90.0
            p.rate_bucket_capacity = 30
            p.rate_refill_per_sec = 0.25

        return p


class HumanizationEngine:
    """
    Shared runtime helpers: delays, Bezier paths, typing cadence,
    reading dwell, Spintax, warm-up scaling.
    """

    SPINTAX_PUBLIC_REPLIES = [
        "Hey {name}, just sent the details to your inbox! Check your messages.",
        "Sent you a direct message, {name}! Let me know if you received it.",
        "Check your DMs, {name}! Sent over the link you requested.",
        "Just dropped it in your inbox, {name} — take a look when you can.",
        "{name}, messaged you the info. Ping me if it doesn't show up.",
    ]

    def __init__(self, profile: Optional[HumanizationProfile] = None):
        self.profile = profile or HumanizationProfile.from_env()
        self._hourly_actions: Dict[str, List[float]] = {}
        self._daily_actions: Dict[str, List[float]] = {}
        self._cooling_multiplier: float = 1.0

    def snapshot(self) -> Dict[str, Any]:
        data = asdict(self.profile)
        data["warmup_multiplier"] = round(self.warmup_multiplier(), 3)
        data["effective_max_actions_per_hour"] = self.effective_hourly_cap()
        data["effective_max_actions_per_day"] = self.effective_daily_cap()
        return data

    def update_profile(self, **kwargs) -> Dict[str, Any]:
        for key, value in kwargs.items():
            if hasattr(self.profile, key) and value is not None:
                setattr(self.profile, key, value)
        return self.snapshot()

    # ------------------------------------------------------------------ Warm-up
    def warmup_day_index(self) -> int:
        try:
            start = date.fromisoformat(self.profile.account_start_date)
        except ValueError:
            start = date.today()
        return max(0, (date.today() - start).days)

    def warmup_multiplier(self) -> float:
        """Linear ramp from start_fraction → 1.0 over warmup_days (default 14-day curve)."""
        if not self.profile.enabled:
            return 1.0
        day = self.warmup_day_index()
        if day >= self.profile.warmup_days:
            return 1.0
        progress = day / max(1, self.profile.warmup_days)
        return self.profile.warmup_start_fraction + (1.0 - self.profile.warmup_start_fraction) * progress

    def warmup_status(self, account_id: str = "default") -> Dict[str, Any]:
        day = self.warmup_day_index()
        total = int(getattr(self.profile, "warmup_days", 14) or 14)
        return {
            "account_id": account_id,
            "warmup_day": day,
            "warmup_days_total": total,
            "multiplier": self.warmup_multiplier(),
            "complete": day >= total,
        }

    def effective_hourly_cap(self) -> int:
        return max(1, int(self.profile.max_actions_per_hour * self.warmup_multiplier()))

    def effective_daily_cap(self) -> int:
        return max(1, int(self.profile.max_actions_per_day * self.warmup_multiplier()))

    def can_act(self, account_id: str) -> Dict[str, Any]:
        now = time.time()
        hour_key = account_id
        day_key = account_id
        self._hourly_actions.setdefault(hour_key, [])
        self._daily_actions.setdefault(day_key, [])

        self._hourly_actions[hour_key] = [t for t in self._hourly_actions[hour_key] if now - t < 3600]
        self._daily_actions[day_key] = [t for t in self._daily_actions[day_key] if now - t < 86400]

        hourly = len(self._hourly_actions[hour_key])
        daily = len(self._daily_actions[day_key])
        allowed = hourly < self.effective_hourly_cap() and daily < self.effective_daily_cap()
        return {
            "allowed": allowed,
            "hourly_used": hourly,
            "hourly_cap": self.effective_hourly_cap(),
            "daily_used": daily,
            "daily_cap": self.effective_daily_cap(),
            "warmup_multiplier": self.warmup_multiplier(),
            "warmup_day": self.warmup_day_index(),
        }

    def record_action(self, account_id: str) -> None:
        now = time.time()
        self._hourly_actions.setdefault(account_id, []).append(now)
        self._daily_actions.setdefault(account_id, []).append(now)

    # ------------------------------------------------------------------ Delays
    def gaussian_delay(self, mean: float, std: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, random.gauss(mean, std)))

    def action_delay(self) -> float:
        p = self.profile
        if not p.enabled:
            return 0.05
        base = random.uniform(p.action_delay_min, p.action_delay_max)
        # Module 5 cooling multiplier stretches delays after 429 storms
        mult = float(getattr(self, "_cooling_multiplier", 1.0) or 1.0)
        return base * max(1.0, mult)

    def set_cooling_multiplier(self, multiplier: float) -> None:
        self._cooling_multiplier = max(1.0, float(multiplier))

    def sleep_action(self) -> float:
        d = self.action_delay()
        time.sleep(d)
        return d

    async def async_sleep_action(self) -> float:
        d = self.action_delay()
        await asyncio.sleep(d)
        return d

    def keystroke_delay(self) -> float:
        p = self.profile
        return self.gaussian_delay(p.key_delay_mean, p.key_delay_std, p.key_delay_min, p.key_delay_max)

    def reading_dwell(self, word_count: int = 40, has_image: bool = True) -> float:
        p = self.profile
        if not p.enabled:
            return 0.2
        base = random.uniform(p.base_read_delay_min, p.base_read_delay_max)
        words = max(0, word_count) * p.ms_per_word
        image = random.uniform(p.image_complexity_bonus_min, p.image_complexity_bonus_max) if has_image else 0.0
        return base + words + image

    def post_scroll_dwell(self) -> float:
        p = self.profile
        return random.uniform(p.post_scroll_dwell_min, p.post_scroll_dwell_max)

    def group_share_delay(self) -> float:
        p = self.profile
        if not p.enabled:
            return 0.2
        return random.uniform(p.group_share_delay_min, p.group_share_delay_max)

    def reaction_decision_pause(self) -> float:
        p = self.profile
        return random.uniform(p.reaction_decision_pause_min, p.reaction_decision_pause_max)

    def inter_platform_delay(self) -> float:
        """Pause between Facebook / Instagram / Threads publishes."""
        p = self.profile
        if not p.enabled:
            return 0.05
        return random.uniform(8.0, 25.0) * (1.4 if p.profile_name == "cautious" else 1.0)

    def seed_comment_delay(self) -> float:
        """Natural pause after publish before seed comment."""
        p = self.profile
        if not p.enabled:
            return 0.05
        return random.uniform(4.0, 12.0)

    def agent_think_delay(self) -> float:
        """Cognitive pause between multi-agent graph nodes."""
        p = self.profile
        if not p.enabled:
            return 0.02
        return random.uniform(0.35, 1.4)

    def webhook_jitter_sec(self) -> float:
        """Small human-like jitter before outbound webhook HTTP POST."""
        p = self.profile
        if not p.enabled:
            return 0.0
        return random.uniform(0.05, 0.85)

    def retry_jitter_sec(self, base: float) -> float:
        """Spec-style random jitter on top of exponential backoff."""
        return max(0.0, base + random.uniform(0.0, min(30.0, base * 0.25 + 1.0)))

    def simulate_fast(self) -> bool:
        return os.getenv("HUMAN_SIMULATE_FAST", "true").lower() in {"1", "true", "yes"}

    def apply_delay(self, seconds: float, fast_cap: float = 0.35) -> float:
        """Sleep `seconds`, or a capped demo delay when HUMAN_SIMULATE_FAST=true."""
        if not self.profile.enabled:
            time.sleep(0.01)
            return 0.01
        actual = min(seconds, fast_cap) if self.simulate_fast() else seconds
        time.sleep(actual)
        return actual

    async def async_apply_delay(self, seconds: float, fast_cap: float = 0.35) -> float:
        if not self.profile.enabled:
            await asyncio.sleep(0.01)
            return 0.01
        actual = min(seconds, fast_cap) if self.simulate_fast() else seconds
        await asyncio.sleep(actual)
        return actual

    def estimated_typing_duration(self, text: str) -> float:
        """How long a human would take to type `text` (for pacing / telemetry)."""
        if not text:
            return 0.0
        total = 0.0
        for ch in text:
            total += self.keystroke_delay()
            if ch in ".,!?;":
                total += random.uniform(
                    self.profile.punctuation_pause_min, self.profile.punctuation_pause_max
                )
        total += random.uniform(
            self.profile.reflection_pause_min, self.profile.reflection_pause_max
        )
        return total

    # ------------------------------------------------------------------ Mouse
    def bezier_path(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        num_points: Optional[int] = None,
    ) -> List[Tuple[float, float]]:
        p = self.profile
        n = num_points or p.bezier_points
        x0, y0 = start
        x3, y3 = end
        dx, dy = x3 - x0, y3 - y0
        dist = math.hypot(dx, dy) or 1.0
        offset = dist * random.uniform(p.bezier_offset_min, p.bezier_offset_max)
        angle = math.atan2(dy, dx) + random.choice([-1, 1]) * random.uniform(0.15, p.bezier_angle_jitter)
        x1 = x0 + offset * math.cos(angle)
        y1 = y0 + offset * math.sin(angle)
        x2 = x3 - offset * math.cos(angle)
        y2 = y3 - offset * math.sin(angle)

        path: List[Tuple[float, float]] = []
        for i in range(n + 1):
            t = i / n
            xt = (1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t**2 * x2 + t**3 * x3
            yt = (1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t**2 * y2 + t**3 * y3
            j = p.mouse_jitter_px
            path.append((xt + random.uniform(-j, j), yt + random.uniform(-j, j)))
        return path

    def mouse_step_delay(self) -> float:
        p = self.profile
        return random.uniform(p.mouse_step_delay_min, p.mouse_step_delay_max)

    async def move_mouse_bezier(
        self,
        page: Any,
        x: float,
        y: float,
        start: Optional[Tuple[float, float]] = None,
        num_points: Optional[int] = None,
    ) -> None:
        """
        Move Playwright mouse along a cubic Bezier trajectory to (x, y).
        Used by Module 1 engagement / click paths.
        """
        import asyncio as _asyncio

        if start is None:
            start = (180.0 + self.profile.mouse_jitter_px, 220.0)
        path = self.bezier_path(start, (float(x), float(y)), num_points=num_points)
        for px, py in path:
            await page.mouse.move(px, py)
            delay = self.mouse_step_delay()
            if self.simulate_fast():
                delay = min(delay, 0.006)
            await _asyncio.sleep(delay)

    async def type_text_humanized(
        self,
        page: Any,
        text: str,
        typo_probability: Optional[float] = None,
    ) -> None:
        """
        Type `text` char-by-char with Gaussian keystroke delays.
        Longer pauses at punctuation; optional typo + immediate backspace.
        Default engage typo rate is 20% when typo_probability is omitted for M1.
        """
        import asyncio as _asyncio

        p = self.profile
        typo_p = p.typo_probability if typo_probability is None else float(typo_probability)
        for char in text:
            kd = self.keystroke_delay()
            if self.simulate_fast():
                kd = min(kd, 0.04)
            await _asyncio.sleep(kd)

            if random.random() < typo_p and char.isalpha():
                wrong = chr(ord(char) + random.choice([-1, 1]))
                await page.keyboard.type(wrong)
                await _asyncio.sleep(random.uniform(0.08, 0.2))
                await page.keyboard.press("Backspace")
                await _asyncio.sleep(random.uniform(0.05, 0.15))

            await page.keyboard.type(char)
            if char in ".,!?;":
                pause = random.uniform(p.punctuation_pause_min, p.punctuation_pause_max)
                if self.simulate_fast():
                    pause = min(pause, 0.12)
                await _asyncio.sleep(pause)

        reflection = random.uniform(p.reflection_pause_min, p.reflection_pause_max)
        if self.simulate_fast():
            reflection = min(reflection, 0.25)
        await _asyncio.sleep(reflection)

    # ------------------------------------------------------------------ Spintax
    SPINTAX_SEED_COMMENTS = [
        "Curious — {what resonates most here?|which part stands out for you?|anyone trying this already?}",
        "{Would love your take.|Drop your thoughts below.|Curious what your team thinks.}",
        "If you had to pick one takeaway, {what would it be?|how would you use this?}",
    ]

    SPINTAX_DM_TEMPLATES = [
        "Hi {name}, {noticed your work around enterprise automation.|saw your recent growth push.} "
        "{Would love to share a short case study.|Happy to send a relevant example.} "
        "{relevant to your team.|that might help your stack.}",
        "Hey {name} — {quick note from Wolf Suite.|quick idea for your pipeline.} "
        "{Open to a short share?|Worth a 2-min look?}",
    ]

    def spintax_reply(self, name: str = "there") -> str:
        template = random.choice(self.SPINTAX_PUBLIC_REPLIES)
        return template.format(name=name or "there")

    def spintax_expand(self, template: str, **kwargs: str) -> str:
        """Expand `{a|b|c}` Spintax segments, then format named placeholders."""

        def pick(m: re.Match) -> str:
            options = m.group(1).split("|")
            return random.choice(options)

        text = re.sub(r"\{([^{}|]+(?:\|[^{}|]+)+)\}", pick, template)
        try:
            return text.format(**{k: (v or "there") for k, v in kwargs.items()})
        except (KeyError, ValueError):
            return text.replace("{name}", kwargs.get("name", "there"))

    def spintax_seed_comment(self) -> str:
        return self.spintax_expand(random.choice(self.SPINTAX_SEED_COMMENTS))

    def spintax_dm(self, name: str = "there") -> str:
        return self.spintax_expand(random.choice(self.SPINTAX_DM_TEMPLATES), name=name)

    def rate_limiter_config(self) -> Dict[str, float]:
        return {
            "capacity": float(self.profile.rate_bucket_capacity),
            "refill_rate": float(self.profile.rate_refill_per_sec) * self.warmup_multiplier(),
        }


# Process-wide singleton
humanization = HumanizationEngine()
