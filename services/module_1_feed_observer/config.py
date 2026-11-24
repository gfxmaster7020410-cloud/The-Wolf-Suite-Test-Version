"""
Module 1 — Settings model & persistence.

Single source of truth for the Manual Worker. Defaults come from .env;
user overrides persist to data/module1_settings.json and apply live at
runtime (no restart needed). Every behavior of the worker is exposed here.

Schema v2 (IMPLEMENTATION_ROADMAP.md PHASE-01):
  - Adds AI-engine, feed-rotation, post-policy, breaker and run-shape
    groups. Old JSON files migrate transparently: missing keys fall back
    to defaults, keys with unusable values are pruned individually
    (_sanitize_raw) instead of invalidating the whole file.
  - Secret masking: dump()/compact() never leak gemini_api_key; the raw
    value lives only inside the persisted JSON and in-memory snapshots
    obtained via get().
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# --------------------------------------------------------------------------
# Enums / vocabularies (single source of truth for validators + dashboards)
# --------------------------------------------------------------------------

VALID_REACTIONS = {"LIKE", "LOVE", "CARE", "HAHA", "WOW"}
TYPO_STYLES = {"paste", "type"}
REACTION_MODES = {"auto", "fixed", "none"}

AI_MODES = {
    "api_only",
    "web_only",
    "api_primary_web_fallback",
    "web_primary_api_fallback",
    "race_both",
}
ROTATION_MODES = {"ordered", "random", "home_first", "manual_queue"}
SOURCE_QUOTA_UNITS = {"posts", "minutes"}
SCROLL_PROFILES = {"lazy", "slow", "fast"}
# PHASE-06 — named scroll personalities: (delta_min, delta_max,
# inter_chunk_sleep_multiplier). Selecting a profile writes these pixels
# through to scroll_delta_min/max (see M1SettingsStore.update) so the JSON
# always reflects reality and consumers stay dumb.
SCROLL_PRESETS: Dict[str, tuple[int, int, float]] = {
    "lazy": (420, 780, 1.0),
    "slow": (200, 400, 1.5),
    "fast": (800, 1400, 0.7),
}
VIDEO_POLICIES = {"engage", "skip", "like_only"}
CAPTURE_POLICIES = {"instant_skip", "retry_limited"}
LANG_MODES = {"match_post", "urdu", "english", "custom"}
CHECKPOINT_POLICIES = {"wait_indefinitely", "wait_then_stop"}
RUN_LENGTH_MODES = {"unlimited", "max_posts", "time_boxed"}
RUN_END_POLICIES = {"close_browser", "stay_open"}
SCHEDULE_MODES = {"manual", "daily_at", "interval"}
COUNT_MODES = {"per_post", "per_action"}

COMMENT_STYLES = {
    "casual",
    "friendly",
    "professional",
    "promotional",
    "engagement",
    "custom",
}

# Secrets that must never leave the process unmasked (PHASE-01 masking rule).
SECRET_FIELDS = frozenset({"gemini_api_key"})
MASK_TEXT = "configured"

DEFAULT_AI_MODE = "api_primary_web_fallback"
DEFAULT_ROTATION_MODE = "ordered"
DEFAULT_SCROLL_PROFILE = "lazy"
DEFAULT_VIDEO_POLICY = "like_only"
DEFAULT_CAPTURE_POLICY = "instant_skip"
DEFAULT_LANG_MODE = "match_post"
DEFAULT_CHECKPOINT_POLICY = "wait_indefinitely"
DEFAULT_RUN_LENGTH_MODE = "unlimited"
DEFAULT_RUN_END_POLICY = "close_browser"
DEFAULT_SCHEDULE_MODE = "manual"
DEFAULT_COUNT_MODE = "per_post"
DEFAULT_QUOTA_UNIT = "posts"

SETTINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "module1_settings.json"


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def _norm_hhmm(v: Any) -> str:
    """Return 'HH:MM' when v is a valid clock time, else ''."""
    if not v:
        return ""
    v = str(v).strip()
    try:
        import datetime as _dt

        _dt.datetime.strptime(v, "%H:%M")
        return v
    except ValueError:
        return ""


def _mask_secrets(payload: Dict[str, Any]) -> Dict[str, Any]:
    """UI-safe copy: secrets become 'configured'/'' — never the raw value."""
    out = dict(payload)
    for key in SECRET_FIELDS:
        if key in out:
            out[key] = MASK_TEXT if out[key] else ""
    return out


# --------------------------------------------------------------------------
# Comment text quality helpers (PHASE-10) — shared by parser/worker/fb.
# Dependency direction stays clean: everything imports FROM config.
# --------------------------------------------------------------------------

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # main pictographs
    "\U00002700-\U000027BF"  # dingbats
    "\U0001F000-\U0001F02F"  # mahjong/domino tiles
    "\U00002600-\U000026FF"  # misc symbols (sun, umbrella, ...)
    "]+",
    flags=re.UNICODE,
)


def maybe_strip_tags(text: str, allowed: bool) -> str:
    """D23: remove #hashtags unless the owner lets the AI keep them."""
    return (text or "") if allowed else re.sub(r"#\w+", "", text or "").strip()


def enforce_comment_quality(
    comment: str,
    *,
    emojis_allowed: bool,
    max_words: int,
    hard_char_cap: int = 280,
) -> str:
    """
    Hard post-model enforcement (D21/D22): strip emojis when banned,
    clamp to the word ceiling with trailing-punctuation cleanup, then
    apply the platform sanity char cap. Single choke point — call sites
    must not duplicate these rules.
    """
    out = (comment or "").strip()
    if not emojis_allowed:
        out = EMOJI_RE.sub("", out).strip()
    if max_words and max_words > 0:
        words = out.split()
        if len(words) > max_words:
            out = " ".join(words[:max_words]).rstrip(",;:-— ")
    return out[:hard_char_cap].strip()


class M1Settings(BaseModel):
    """Every tunable knob of the Module 1 Manual Worker."""

    model_config = ConfigDict(extra="ignore")

    # --- target -----------------------------------------------------------
    feed_url: str = Field(
        default_factory=lambda: os.getenv("FB_HOME_URL", "https://www.facebook.com")
    )
    user_data_dir: str = Field(
        default_factory=lambda: (
            os.getenv("FB_USER_DATA_DIR")
            or str(Path(__file__).resolve().parents[2] / "data" / "browser_profiles" / "facebook")
        )
    )
    max_posts_per_run: int = Field(0, ge=0, description="0 = unlimited")

    # --- ai engine --------------------------------------------------------
    ai_mode: str = Field(
        DEFAULT_AI_MODE,
        description=(
            "api_only | web_only | api_primary_web_fallback | "
            "web_primary_api_fallback | race_both"
        ),
    )
    gemini_api_key: str = Field("", description="Dashboard-editable; overrides env when non-empty")
    gemini_api_model: str = Field("", description='empty = env GEMINI_API_MODEL else "gemini-2.0-flash"')
    gemini_reply_wait_sec: int = Field(60, ge=1, description="Web-mode reply wait")
    api_failure_is_fatal_for_post: bool = Field(True)

    # --- pacing -----------------------------------------------------------
    gap_between_posts_min: float = Field(5.0, ge=0)
    gap_between_posts_max: float = Field(15.0, ge=0)
    scroll_profile: str = Field(DEFAULT_SCROLL_PROFILE, description="lazy | slow | fast (preset; numbers below follow)")
    scroll_delta_min: int = Field(420, ge=0)
    scroll_delta_max: int = Field(780, ge=0)
    reading_dwell_min: float = Field(2.0, ge=0)
    reading_dwell_max: float = Field(6.0, ge=0)
    long_break_every: int = Field(0, ge=0, description="every N engaged posts take a longer break; 0 = off")
    long_break_min: float = Field(60.0, ge=0)
    long_break_max: float = Field(180.0, ge=0)
    humanization_enabled: bool = True

    # --- feed rotation ----------------------------------------------------
    feed_rotation_mode: str = Field(DEFAULT_ROTATION_MODE, description="ordered | random | home_first | manual_queue")
    feed_home_first_count: int = Field(20, description="posts on home before groups begin (home_first mode)")
    feed_group_urls: list[str] = Field(default_factory=list, description="saved Facebook group URLs")
    feed_manual_queue: list[str] = Field(default_factory=list, description='"home" or group URLs, exact order')
    source_quota_unit: str = Field(DEFAULT_QUOTA_UNIT, description="posts | minutes per source session")
    source_quota_value: int = Field(25, description="N posts or N minutes per source before switching")
    source_switch_cooldown_sec: int = Field(45, ge=0, description="settle delay after navigating")

    # --- engagement -------------------------------------------------------
    typing_style: str = Field("paste", description="paste | type")
    typo_probability: float = Field(0.20, ge=0.0, le=1.0)
    key_delay_min_ms: int = Field(80, ge=0)
    key_delay_max_ms: int = Field(220, ge=0)
    reaction_mode: str = Field("auto", description="auto | fixed | none")
    fixed_reaction: str = Field("LIKE")
    comment_enabled: bool = True
    min_confidence: float = Field(0.40, ge=0.0, le=1.0)

    # --- comment quality --------------------------------------------------
    comment_style: str = Field("casual", description="casual|friendly|professional|promotional|engagement|custom")
    custom_prompt: str = Field("", description="overrides comment_style when set")
    comment_language: str = Field(DEFAULT_LANG_MODE, description="match_post | urdu | english | custom")
    custom_language_instruction: str = Field("", description="used when comment_language == custom")
    allow_hashtags: bool = Field(True, description="False strips #tags from generated comments")
    emoji_enabled: bool = Field(False, description="allow at most one natural emoji")
    comment_max_words: int = Field(40, description="hard word ceiling for generated comments; 0 = off")

    # --- post-type & capture policies --------------------------------------
    video_policy: str = Field(DEFAULT_VIDEO_POLICY, description="engage | skip | like_only")
    capture_failure_policy: str = Field(DEFAULT_CAPTURE_POLICY, description="instant_skip | retry_limited")
    capture_retry_limit: int = Field(3, description="consecutive failed captures before advancing")

    # --- scheduling -------------------------------------------------------
    work_hours_start: str = Field("", description='local "HH:MM" or empty = always')
    work_hours_end: str = Field("", description='local "HH:MM" or empty = always')
    max_actions_per_hour: int = Field(0, ge=0, description="0 = off")
    max_actions_per_day: int = Field(0, ge=0, description="0 = off")

    # --- gemini web -------------------------------------------------------
    gemini_url: str = Field(
        default_factory=lambda: os.getenv("GEMINI_WEB_URL", "https://gemini.google.com/app")
    )
    gemini_login_wait_sec: int = Field(600, ge=0)
    fresh_chat_per_post: bool = True
    structured_reply: bool = True

    # --- safety -----------------------------------------------------------
    pause_on_checkpoint: bool = True
    checkpoint_policy: str = Field(DEFAULT_CHECKPOINT_POLICY, description="wait_indefinitely | wait_then_stop")
    checkpoint_wait_sec: int = Field(1800, ge=0, description="auto-stop budget when policy == wait_then_stop")
    skip_already_commented: bool = True
    max_seen_posts: int = Field(5000, ge=0)
    seen_expiry_days: int = Field(30, description="seen-list entries older than this re-engageable; 0 = never expire")
    action_count_mode: str = Field(DEFAULT_COUNT_MODE, description="per_post (1 post = 1 action) | per_action (legacy)")

    # --- ai failure breaker -------------------------------------------------
    circuit_breaker_enabled: bool = Field(False)
    circuit_breaker_threshold: int = Field(5, description="consecutive AI failures before pausing")
    circuit_breaker_cooldown_min: float = Field(10.0, ge=0, description="0 = stay paused until Resume")

    # --- run shape & lifecycle ---------------------------------------------
    run_length_mode: str = Field(DEFAULT_RUN_LENGTH_MODE, description="unlimited | max_posts | time_boxed")
    run_time_limit_min: int = Field(120, description="minutes; used when run_length_mode == time_boxed")
    run_end_policy: str = Field(DEFAULT_RUN_END_POLICY, description="close_browser | stay_open (natural ends only)")
    schedule_mode: str = Field(DEFAULT_SCHEDULE_MODE, description="manual | daily_at | interval")
    daily_start_time: str = Field("", description='"HH:MM" local; used when schedule_mode == daily_at')
    interval_run_hours: float = Field(2.0, description="run window length for interval mode")
    interval_sleep_hours: float = Field(4.0, description="rest window length for interval mode")

    # ----------------------------------------------------------------------
    # field validators — enum coercions
    # ----------------------------------------------------------------------
    @field_validator("typing_style")
    @classmethod
    def _v_typing(cls, v: str) -> str:
        v = (v or "paste").strip().lower()
        return v if v in TYPO_STYLES else "paste"

    @field_validator("reaction_mode")
    @classmethod
    def _v_reaction_mode(cls, v: str) -> str:
        v = (v or "auto").strip().lower()
        return v if v in REACTION_MODES else "auto"

    @field_validator("comment_style")
    @classmethod
    def _v_style(cls, v: str) -> str:
        v = (v or "casual").strip().lower()
        return v if v in COMMENT_STYLES else "casual"

    @field_validator("fixed_reaction")
    @classmethod
    def _v_reaction(cls, v: str) -> str:
        v = (v or "LIKE").strip().upper()
        return v if v in VALID_REACTIONS else "LIKE"

    @field_validator("ai_mode")
    @classmethod
    def _v_ai_mode(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in AI_MODES else DEFAULT_AI_MODE

    @field_validator("feed_rotation_mode")
    @classmethod
    def _v_rotation(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in ROTATION_MODES else DEFAULT_ROTATION_MODE

    @field_validator("scroll_profile")
    @classmethod
    def _v_scroll_profile(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in SCROLL_PROFILES else DEFAULT_SCROLL_PROFILE

    @field_validator("video_policy")
    @classmethod
    def _v_video_policy(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in VIDEO_POLICIES else DEFAULT_VIDEO_POLICY

    @field_validator("capture_failure_policy")
    @classmethod
    def _v_capture_policy(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in CAPTURE_POLICIES else DEFAULT_CAPTURE_POLICY

    @field_validator("comment_language")
    @classmethod
    def _v_lang(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in LANG_MODES else DEFAULT_LANG_MODE

    @field_validator("checkpoint_policy")
    @classmethod
    def _v_checkpoint_policy(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in CHECKPOINT_POLICIES else DEFAULT_CHECKPOINT_POLICY

    @field_validator("run_length_mode")
    @classmethod
    def _v_run_length(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in RUN_LENGTH_MODES else DEFAULT_RUN_LENGTH_MODE

    @field_validator("run_end_policy")
    @classmethod
    def _v_run_end(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in RUN_END_POLICIES else DEFAULT_RUN_END_POLICY

    @field_validator("schedule_mode")
    @classmethod
    def _v_schedule_mode(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in SCHEDULE_MODES else DEFAULT_SCHEDULE_MODE

    @field_validator("action_count_mode")
    @classmethod
    def _v_count_mode(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in COUNT_MODES else DEFAULT_COUNT_MODE

    @field_validator("source_quota_unit")
    @classmethod
    def _v_quota_unit(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in SOURCE_QUOTA_UNITS else DEFAULT_QUOTA_UNIT

    @field_validator("gemini_api_key", "gemini_api_model", "custom_language_instruction")
    @classmethod
    def _v_strip_text(cls, v: str) -> str:
        return (v or "").strip()

    # ----------------------------------------------------------------------
    # numeric clamps — coerce instead of rejecting (never-fatal golden rule)
    # ----------------------------------------------------------------------
    @field_validator("capture_retry_limit")
    @classmethod
    def _v_capture_retry(cls, v: int) -> int:
        return min(10, max(1, int(v or 1)))

    @field_validator("seen_expiry_days")
    @classmethod
    def _v_seen_expiry(cls, v: int) -> int:
        return min(365, max(0, int(v or 0)))

    @field_validator("comment_max_words")
    @classmethod
    def _v_comment_words(cls, v: int) -> int:
        return min(120, max(0, int(v or 0)))

    @field_validator("run_time_limit_min")
    @classmethod
    def _v_time_limit(cls, v: int) -> int:
        return min(24 * 60, max(5, int(v or 5)))

    @field_validator("interval_run_hours", "interval_sleep_hours")
    @classmethod
    def _v_interval_hours(cls, v: float) -> float:
        return min(72.0, max(0.25, float(v or 0.25)))

    @field_validator("source_quota_value")
    @classmethod
    def _v_quota_value(cls, v: int) -> int:
        return min(10_000, max(1, int(v or 1)))

    @field_validator("feed_home_first_count")
    @classmethod
    def _v_home_first(cls, v: int) -> int:
        return min(10_000, max(1, int(v or 1)))

    @field_validator("circuit_breaker_threshold")
    @classmethod
    def _v_cb_threshold(cls, v: int) -> int:
        return min(100, max(2, int(v or 2)))

    @field_validator("circuit_breaker_cooldown_min")
    @classmethod
    def _v_cb_cooldown(cls, v: float) -> float:
        return max(0.0, float(v or 0.0))

    # ----------------------------------------------------------------------
    # list sanitization — feed sources
    # ----------------------------------------------------------------------
    @field_validator("feed_group_urls")
    @classmethod
    def _v_group_urls(cls, v: Optional[list[str]]) -> list[str]:
        return _clean_url_list(v or [], allow_home=False)

    @field_validator("feed_manual_queue")
    @classmethod
    def _v_manual_queue(cls, v: Optional[list[str]]) -> list[str]:
        return _clean_url_list(v or [], allow_home=True)

    # ----------------------------------------------------------------------
    # time formats
    # ----------------------------------------------------------------------
    @field_validator("work_hours_start", "work_hours_end", "daily_start_time")
    @classmethod
    def _v_clock(cls, v: str) -> str:
        return _norm_hhmm(v)

    # ----------------------------------------------------------------------
    # range pairs — keep max >= min (existing behavior, preserved)
    # ----------------------------------------------------------------------
    @field_validator("gap_between_posts_max")
    @classmethod
    def _v_gap(cls, v: float, info: Any) -> float:
        return v if v >= info.data.get("gap_between_posts_min", 0) else info.data.get("gap_between_posts_min", v)

    @field_validator("scroll_delta_max")
    @classmethod
    def _v_scroll(cls, v: int, info: Any) -> int:
        return v if v >= info.data.get("scroll_delta_min", 0) else info.data.get("scroll_delta_min", v)

    @field_validator("key_delay_max_ms")
    @classmethod
    def _v_keys(cls, v: int, info: Any) -> int:
        return v if v >= info.data.get("key_delay_min_ms", 0) else info.data.get("key_delay_min_ms", v)

    @field_validator("reading_dwell_max")
    @classmethod
    def _v_dwell(cls, v: float, info: Any) -> float:
        return v if v >= info.data.get("reading_dwell_min", 0) else info.data.get("reading_dwell_min", v)

    @field_validator("long_break_max")
    @classmethod
    def _v_break(cls, v: float, info: Any) -> float:
        return v if v >= info.data.get("long_break_min", 0) else info.data.get("long_break_min", v)

    # ----------------------------------------------------------------------
    # model-level rules
    # ----------------------------------------------------------------------
    @model_validator(mode="after")
    def _m_custom_language_needs_instruction(self) -> "M1Settings":
        if self.comment_language == "custom" and not self.custom_language_instruction.strip():
            object.__setattr__(self, "comment_language", DEFAULT_LANG_MODE)
        return self

    # ------------------------------------------------------------------ helpers
    def effective_scroll(self) -> tuple[int, int, float]:
        """
        PHASE-06 — (delta_min, delta_max, chunk_sleep_multiplier) for the
        active scroll personality. Pixels come from the persisted fields:
        profile selection writes the preset numbers through (update()), so
        they always match the preset unless the owner hand-edited them
        afterwards ("custom" state) — hand edits stay live and win.
        """
        preset = SCROLL_PRESETS.get(self.scroll_profile)
        sleep_mul = preset[2] if preset else 1.0
        return self.scroll_delta_min, self.scroll_delta_max, sleep_mul

    def _language_clause(self) -> str:
        """PHASE-10 (D15): per-language instruction block."""
        if self.comment_language == "urdu":
            return (
                "Write the comment in Urdu (اردو). "
                "اس پوسٹ کے لیے اردو میں ایک انگیجمنٹ والا کمنٹ لکھو۔"
            )
        if self.comment_language == "english":
            return "Write the comment in English."
        if self.comment_language == "custom":
            return self.custom_language_instruction.strip()
        # match_post (default)
        return (
            "Detect the language of the post and write the comment in that "
            "SAME language (Urdu post => Urdu comment; English => English; "
            "mixed => dominant language). If the post has no readable text, "
            "infer from the imagery and write in English."
        )

    def prompt(self) -> str:
        """
        PHASE-10 — full assembly:
        base (custom override | style) + language + emoji + hashtag +
        length clauses, then the structured JSON suffix when enabled.
        """
        if self.custom_prompt and self.custom_prompt.strip():
            prompt = self.custom_prompt.strip()
        else:
            prompt = _STYLE_PROMPTS.get(self.comment_style, _STYLE_PROMPTS["casual"])

        clauses: list[str] = [self._language_clause()]
        if self.emoji_enabled:
            clauses.append("At most ONE emoji is allowed, only if it fits naturally.")
        else:
            clauses.append("No emojis.")
        if not self.allow_hashtags:
            clauses.append("No hashtags (#).")
        if self.comment_max_words > 0:
            clauses.append(f"Hard limit: {self.comment_max_words} words maximum.")

        prompt = " ".join([prompt] + [c for c in clauses if c]).strip()
        if self.structured_reply:
            prompt += (
                ' Return ONLY JSON, no markdown, no explanation: '
                '{"reaction":"LIKE|LOVE|CARE|HAHA|WOW","comment":"<the single best comment>"}'
            )
        return prompt

    def compact(self) -> Dict[str, Any]:
        """Tunable subset exposed for the Quick Settings panel (masked)."""
        return _mask_secrets(
            {
                # legacy quick-settings keys (unchanged contract)
                "feed_url": self.feed_url,
                "max_posts_per_run": self.max_posts_per_run,
                "gap_between_posts_min": self.gap_between_posts_min,
                "gap_between_posts_max": self.gap_between_posts_max,
                "typing_style": self.typing_style,
                "reaction_mode": self.reaction_mode,
                "fixed_reaction": self.fixed_reaction,
                "comment_enabled": self.comment_enabled,
                "min_confidence": self.min_confidence,
                "max_actions_per_hour": self.max_actions_per_hour,
                "max_actions_per_day": self.max_actions_per_day,
                "work_hours_start": self.work_hours_start,
                "work_hours_end": self.work_hours_end,
                "fresh_chat_per_post": self.fresh_chat_per_post,
                "structured_reply": self.structured_reply,
                "comment_style": self.comment_style,
                "pause_on_checkpoint": self.pause_on_checkpoint,
                "skip_already_commented": self.skip_already_commented,
                # --- schema v2 additions ---
                "ai_mode": self.ai_mode,
                "gemini_api_key": self.gemini_api_key,
                "scroll_profile": self.scroll_profile,
                "video_policy": self.video_policy,
                "capture_failure_policy": self.capture_failure_policy,
                "capture_retry_limit": self.capture_retry_limit,
                "seen_expiry_days": self.seen_expiry_days,
                "comment_language": self.comment_language,
                "emoji_enabled": self.emoji_enabled,
                "allow_hashtags": self.allow_hashtags,
                "comment_max_words": self.comment_max_words,
                "action_count_mode": self.action_count_mode,
                "checkpoint_policy": self.checkpoint_policy,
                "circuit_breaker_enabled": self.circuit_breaker_enabled,
                "circuit_breaker_threshold": self.circuit_breaker_threshold,
                "circuit_breaker_cooldown_min": self.circuit_breaker_cooldown_min,
                "run_length_mode": self.run_length_mode,
                "run_end_policy": self.run_end_policy,
                "schedule_mode": self.schedule_mode,
                "daily_start_time": self.daily_start_time,
                "interval_run_hours": self.interval_run_hours,
                "interval_sleep_hours": self.interval_sleep_hours,
            }
        )


def _clean_url_list(values: list[Any], *, allow_home: bool) -> list[str]:
    """Filter/normalize a feed-source list: facebook URLs only (+ 'home'),
    query/fragment + trailing slash stripped (PHASE-13), de-duplicated
    preserving order, sane 50-entry ceiling."""
    out: list[str] = []
    for item in values:
        s = str(item or "").strip()
        if not s:
            continue
        if allow_home and s.lower() == "home":
            s = "home"
        elif "facebook.com" in s.lower():
            s = s.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        else:
            continue
        if s and s not in out:
            out.append(s)
        if len(out) >= 50:
            break
    return out


_STYLE_PROMPTS: Dict[str, str] = {
    "casual": (
        "Act as a real, friendly Facebook user. Look at this post and write ONE short, "
        "natural, casual comment that makes the author feel engaged. "
        "One sentence max, 40 words max."
    ),
    "friendly": (
        "Act as a warm, polite Facebook friend. Look at this post and write ONE short, warm, "
        "natural comment showing genuine interest. "
        "One sentence max, 40 words max."
    ),
    "professional": (
        "Act as a professional but approachable Facebook user. Look at this post and write ONE "
        "short, insightful, professional comment. One or two sentences max, 45 words max."
    ),
    "promotional": (
        "Act as a smart marketer on Facebook. Look at this post and write ONE short, natural "
        "comment that adds value while subtly creating curiosity about a related product or "
        "service. Never spammy, never salesy. 40 words max."
    ),
    "engagement": (
        "Act as a real, socially savvy Facebook user whose goal is genuine engagement. Look at "
        "this post and write ONE short comment that sparks connection: react to the specific "
        "content, show warm interest, and where natural end with a light question or invitation "
        "that makes the author want to reply. Never generic, never salesy, never bot-like."
    ),
}


class M1SettingsStore:
    """Thread-safe settings holder with JSON persistence + env defaults."""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else SETTINGS_PATH
        self._lock = threading.Lock()
        self._settings = M1Settings()
        self._load()

    # ------------------------------------------------------------------ io
    def _sanitize_raw(self, data: Any) -> Dict[str, Any]:
        """
        Drop top-level entries Pydantic rejects so ONE bad/corrupt key can
        never invalidate a whole saved file (schema migration safety).
        Iteratively prunes whatever ValidationError reports; unknown keys
        are ignored downstream by extra="ignore".
        """
        if not isinstance(data, dict):
            return {}
        cleaned: Dict[str, Any] = dict(data)
        for _ in range(len(cleaned) + 1):
            try:
                M1Settings(**cleaned)
                return cleaned
            except ValidationError as exc:
                bad = {str(err.get("loc", ("",))[0]) for err in exc.errors()}
                bad.discard("")
                if not bad or not (bad & cleaned.keys()):
                    return {}
                for key in bad:
                    cleaned.pop(key, None)
            except Exception:
                return {}
        return {}

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[M1 Settings] Failed to load {self._path}: {exc}")
            return
        clean = self._sanitize_raw(data)
        dropped = sorted(set(data.keys()) - set(clean.keys())) if isinstance(data, dict) else []
        try:
            self._settings = M1Settings(**clean)
        except (ValueError, OSError) as exc:
            print(f"[M1 Settings] Failed to load {self._path}: {exc}")
            return
        if dropped:
            print(f"[M1 Settings] Pruned invalid keys while loading: {', '.join(dropped)}")

    def _save(self, settings: M1Settings) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(settings.model_dump(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[M1 Settings] Failed to save {self._path}: {exc}")

    # ------------------------------------------------------------------ api
    def get(self) -> M1Settings:
        """Immutable snapshot of current settings (contains real secrets)."""
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply validated changes. Returns {ok, settings, warnings}.
        Unknown/invalid keys are ignored (warning listed), never fatal.
        Secrets arrive masked from the UI; echoing the mask is a no-op.
        """
        with self._lock:
            current = self._settings.model_copy(deep=True)
            warnings: list[str] = []
            data = current.model_dump()
            changed = False
            for key, value in (patch or {}).items():
                if key in SECRET_FIELDS and value == MASK_TEXT:
                    continue  # UI round-tripped the mask — not a real edit
                if isinstance(value, tuple):
                    value = list(value)
                if not isinstance(value, (str, int, float, bool, list)):
                    warnings.append(f"{key}: unsupported value type")
                    continue
                if key not in data:
                    warnings.append(f"{key}: unknown setting")
                    continue
                data[key] = value
                changed = True
            try:
                updated = M1Settings(**data)
            except ValueError as exc:
                return {"ok": False, "error": str(exc), "settings": _mask_secrets(current.model_dump())}
            if changed:
                # PHASE-06 write-through: a patch that SELECTS a scroll
                # profile (without explicit deltas in the same transaction)
                # snaps the persisted pixel range to that preset. Explicit
                # deltas — with or without a profile — always win verbatim,
                # and unrelated edits never touch the numbers.
                if (
                    "scroll_profile" in (patch or {})
                    and updated.scroll_profile in SCROLL_PRESETS
                    and not {"scroll_delta_min", "scroll_delta_max"} & set(patch)
                ):
                    pmin, pmax, _ = SCROLL_PRESETS[updated.scroll_profile]
                    updated = updated.model_copy(
                        update={"scroll_delta_min": pmin, "scroll_delta_max": pmax}
                    )
                self._save(updated)
                self._settings = updated
                message = "settings updated"
            else:
                message = "no changes"
            return {
                "ok": True,
                "message": message,
                "warnings": warnings,
                "settings": _mask_secrets(updated.model_dump()),
            }

    def set_secret(self, name: str, value: str) -> Dict[str, Any]:
        """
        Write a secret (e.g. gemini_api_key). Raw value persists to disk;
        every public dump keeps it masked. Returns {ok, message/error}.
        """
        if name not in SECRET_FIELDS:
            return {"ok": False, "error": f"unknown secret '{name}'"}
        raw = (value or "").strip()
        with self._lock:
            data = self._settings.model_dump()
            data[name] = raw
            try:
                updated = M1Settings(**data)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            self._save(updated)
            self._settings = updated
        return {"ok": True, "message": f"{name} updated", "configured": bool(raw)}

    def dump(self) -> Dict[str, Any]:
        """Full settings for UI/API consumption — secrets masked."""
        return _mask_secrets(self.get().model_dump())

    @property
    def path(self) -> str:
        return str(self._path)


# Process-wide singleton
settings_store = M1SettingsStore()


# ---------------------------------------------------------------------------
# PHASE-16 — field help + enum metadata for the dashboard meta endpoint.
# Single source of truth so the UI never hardcodes drift-prone option lists.
# ---------------------------------------------------------------------------
FIELD_HELP: Dict[str, str] = {
    # --- AI engine -------------------------------------------------------
    "ai_mode": "API replies instantly; Web is slower but keyless. race_both burns double quota.",
    "gemini_api_key": "Paste a NEW key to replace. Shown as 'configured' only — never echoed.",
    "gemini_api_model": "Empty = gemini-2.0-flash (or env default).",
    "gemini_reply_wait_sec": "How long the Web bridge waits for Gemini to finish replying.",
    "api_failure_is_fatal_for_post": "ON: post skipped when every AI engine fails. OFF: engage without comment.",
    "circuit_breaker_enabled": "Pause the run after N consecutive AI failures instead of skipping forever.",
    "circuit_breaker_threshold": "Consecutive AI failures before the breaker trips (minimum 2).",
    "circuit_breaker_cooldown_min": "Auto-resume after this many minutes; 0 = stay paused until Resume.",
    # --- feeds & rotation -------------------------------------------------
    "feed_url": "Your home/news feed URL.",
    "feed_rotation_mode": "ordered: home then groups. random: pick each switch. home_first: N posts home, then groups. manual_queue: your exact order.",
    "feed_home_first_count": "Engaged posts spent on home before groups rotate (home_first mode).",
    "feed_group_urls": "One Facebook group URL per line.",
    "feed_manual_queue": "Exact source order — chips built from home + saved groups.",
    "source_quota_unit": "Measure each source session in posts or minutes.",
    "source_quota_value": "Leave the current source after this many posts/minutes.",
    "source_switch_cooldown_sec": "Settle pause after arriving at a new source before scanning.",
    # --- scroll & reading ---------------------------------------------------
    "scroll_profile": "Lazy = safe thumb-flick, Slow = deliberate reader, Fast = skimmer. Selecting snaps the pixel range below.",
    "scroll_delta_min": "Min wheel distance per scroll step (px).",
    "scroll_delta_max": "Max wheel distance per scroll step (px).",
    "reading_dwell_min": "Shortest human reading pause per post (s).",
    "reading_dwell_max": "Longest human reading pause per post (s).",
    # --- post handling ------------------------------------------------------
    "video_policy": "like_only: tap Like, never comment (default). skip: scroll past. engage: full AI flow.",
    "capture_failure_policy": "retry_limited micro-scrolls and re-reads a half-rendered feed unit before advancing.",
    "capture_retry_limit": "Consecutive failed captures before advancing past the region.",
    "seen_expiry_days": "Seen-list entries older than this re-engageable again; 0 = never expire.",
    "skip_already_commented": "Skip posts already engaged in earlier runs.",
    "max_seen_posts": "Seen-memory size ceiling (soft bound).",
    # --- comments -----------------------------------------------------------
    "comment_style": "engagement = curiosity-hook comments that invite replies.",
    "custom_prompt": "Used when style = custom.",
    "comment_language": "match_post mirrors the post's language; custom uses the instruction below.",
    "custom_language_instruction": "Verbatim instruction appended when language = custom.",
    "emoji_enabled": "Allow at most one natural emoji. OFF hard-strips emojis.",
    "allow_hashtags": "AI may keep hashtags it writes. OFF strips them.",
    "comment_max_words": "Hard word ceiling for posted comments; 0 = platform cap only.",
    "min_confidence": "Skip engagement below this AI confidence (0-100%).",
    "reaction_mode": "auto = AI picks LIKE/LOVE/CARE/HAHA/WOW; fixed uses the choice below; none disables reactions.",
    "fixed_reaction": "Reaction used when mode = fixed.",
    "typing_style": "paste is fast; type mimics keystrokes with typos.",
    "typo_probability": "Chance a typed character 'slips' (0-100%).",
    "key_delay_min_ms": "Fastest simulated keystroke delay (ms).",
    "key_delay_max_ms": "Slowest simulated keystroke delay (ms).",
    "humanization_enabled": "Master switch for human-like pacing/typing.",
    # --- pacing & caps --------------------------------------------------------
    "gap_between_posts_min": "Shortest wait between posts (s).",
    "gap_between_posts_max": "Longest wait between posts (s).",
    "long_break_every": "Take a long break after this many engaged posts; 0 = off.",
    "long_break_min": "Shortest long-break (minutes).",
    "long_break_max": "Longest long-break (minutes).",
    "max_actions_per_hour": "Hourly action cap; 0 = off. ~20/hr recommended.",
    "max_actions_per_day": "Daily action cap; 0 = unlimited.",
    "action_count_mode": "per_post recommended: reaction+comment on one post counts ONCE.",
    # --- safety ----------------------------------------------------------------
    "pause_on_checkpoint": "Detect Facebook security checks and pause instead of stumbling through.",
    "checkpoint_policy": "wait_indefinitely holds until you clear it; wait_then_stop auto-stops after the budget.",
    "checkpoint_wait_sec": "Auto-stop budget when policy = wait_then_stop.",
    "work_hours_start": "Only run inside this local window; empty = always.",
    "work_hours_end": "Window end ('HH:MM'); empty = always.",
    # --- run shape & lifecycle -----------------------------------------------------
    "run_length_mode": "unlimited until stopped / max_posts / time_boxed wall-clock window.",
    "max_posts_per_run": "Stop after N engaged posts (mode = max_posts); 0 treated as unlimited.",
    "run_time_limit_min": "Wall-clock run length in minutes (mode = time_boxed).",
    "run_end_policy": "stay_open leaves Chrome open on natural ends so you can inspect or reuse the session.",
    "schedule_mode": "manual: buttons only. daily_at: auto-start at HH:MM. interval: run X h, sleep Y h cycles.",
    "daily_start_time": "Local 'HH:MM' fire time (mode = daily_at).",
    "interval_run_hours": "Run-window length in hours (interval mode).",
    "interval_sleep_hours": "Rest-window length in hours (interval mode).",
}


def settings_meta() -> Dict[str, Any]:
    """Option lists + tooltips for the dashboard settings panel (PHASE-16).

    The UI renders selects/radios from `enums` and tooltips from `help`, so
    validator enums stay the single source of truth."""
    return {
        "enums": {
            "ai_mode": sorted(AI_MODES),
            "feed_rotation_mode": sorted(ROTATION_MODES),
            "source_quota_unit": sorted(SOURCE_QUOTA_UNITS),
            "scroll_profile": sorted(SCROLL_PROFILES),
            "video_policy": sorted(VIDEO_POLICIES),
            "capture_failure_policy": sorted(CAPTURE_POLICIES),
            "comment_style": sorted(COMMENT_STYLES),
            "comment_language": sorted(LANG_MODES),
            "reaction_mode": sorted(REACTION_MODES),
            "fixed_reaction": sorted(VALID_REACTIONS),
            "typing_style": sorted(TYPO_STYLES),
            "action_count_mode": sorted(COUNT_MODES),
            "checkpoint_policy": sorted(CHECKPOINT_POLICIES),
            "run_length_mode": sorted(RUN_LENGTH_MODES),
            "run_end_policy": sorted(RUN_END_POLICIES),
            "schedule_mode": sorted(SCHEDULE_MODES),
        },
        "help": FIELD_HELP,
        "secret_fields": sorted(SECRET_FIELDS),
    }
