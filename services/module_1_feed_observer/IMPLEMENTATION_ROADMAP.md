# MODULE 1 — FEED OBSERVER :: MASTER IMPLEMENTATION ROADMAP
# =====================================================================
# FILE ROLE:
#   This file IS the software context for implementing Module 1 upgrades.
#   It records everything agreed in the planning session (2026-08-23)
#   between the owner and the engineering agent.
#
# HOW TO USE THIS FILE (protocol for any agent / developer resuming work):
#   1. Read this file top-to-bottom BEFORE writing any code.
#   2. Find the FIRST phase whose "Status" line is NOT marked `[x] DONE`.
#   3. Implement exactly that phase. Do not skip ahead. Do not reorder.
#   4. While implementing, follow the phase's "Runtime Behavior",
#      "Settings Fields", and "Implementation Notes" sections verbatim.
#   5. When the phase passes its "Acceptance Criteria", change its status
#      line to `[x] DONE (date)` and add an entry in CHANGELOG (bottom).
#   6. Commit with message: `M1-PHASE-XX: <phase title>`.
#   7. Re-read this file for the next phase. Repeat until all done.
#
# GOLDEN RULES (apply to EVERY phase):
#   - Every tunable number is a Dashboard setting. No magic constants left
#     in code. Defaults below are starting points, user tunes live.
#   - Human-like behavior beats speed. When in doubt, slow down.
#   - Never break a running worker with a settings change: settings are
#     re-read at the top of every loop cycle and applied live.
#   - Unknown/invalid setting values are ignored with a warning, never fatal
#     (existing M1SettingsStore.update contract).
#   - Legacy files (bot.py, observer.py) are NOT touched unless a phase
#     explicitly says so. They stay backward-compatible.
# =====================================================================

---

## 0. CURRENT STATE AUDIT (what exists today, 2026-08-23)

### 0.1 File map and responsibilities

| File | Responsibility | Key symbols | Notes |
|---|---|---|---|
| `config.py` | Settings model + JSON persistence | `M1Settings`, `M1SettingsStore`, `settings_store`, `VALID_REACTIONS` | Pydantic BaseModel, `extra="ignore"`, saves to `data/module1_settings.json`, thread-safe, live-updatable |
| `runner.py` | Singleton controller for FastAPI endpoints | `Module1Runner`, `module1_runner` | start/stop/pause/resume/skip_post/status/config/update_config; spawns worker on Proactor-safe thread |
| `worker.py` | State machine main loop | `Module1Worker`, `PHASES` | gates (work-hours, caps, max-posts), capture -> dedup -> dwell -> Gemini -> engage -> pace; snapshots to dashboard |
| `fb.py` | Facebook DOM automation | `FacebookAutomation` | persistent Chrome launch, login idle-wait, `capture_post`, `comment`, `react`, `scroll_to_next_post`, `checkpoint_detected`, humanized clicks/scrolls |
| `gemini_bridge.py` | Gemini WEB UI automation | `GeminiWebBridge`, `GeminiWebAnalyzer`, `parse_gemini_reply` | uploads screenshot to gemini.google.com tab, structured JSON replies, sign-in walls handled, `GEMINI_MODE` env hook (`web|api|both`) in analyzer |
| `gemini_api.py` | Gemini REST API client | `generate_engagement`, `parse_api_reply` | vision REST call, hardcoded Urdu prompt (to be replaced), reads `GEMINI_API_KEY` from env only |
| `seen_store.py` | Duplicate-post memory | `SeenStore`, `seen_store` | JSON-backed url->timestamp dict at `data/module1_seen.json`, bounded by COUNT only (no expiry yet) |
| `bot.py` | LEGACY simple bot | `FacebookAutoEngagementBot` | screenshot -> API -> paste comment -> like loop; superseded by worker path, kept working |
| `observer.py` | LEGACY observer classes | `Module1FeedObserver`, `FeedObserver`, `BezierMouseController`, `GaussianTypingEngine` | thin wrappers over shared humanization; superseded, kept working |

### 0.2 Existing behaviors that ALREADY match the plan (verify-only, no rewrite)

- Visible browser by default (`FB_HEADLESS=false`).
- Indefinite-ish manual login wait on Facebook (`FB_LOGIN_WAIT_MS`, default 300s -> will become truly indefinite in PHASE-02) and Google sign-in walls mid-flow (Gemini bridge already retries after login).
- Reaction mode `auto|fixed|none` exists; default `auto` (AI picks) matches decision.
- Typing styles `paste|type` exist; typo probability + key delay ranges exist as settings.
- Gap-between-posts min/max, reading dwell min/max, long-break-every-N exist as settings.
- Work-hours window exists; plan requires it DEFAULT OFF (it already is: empty strings).
- Hourly/daily caps exist as numeric fields (counting semantics change in PHASE-05).
- Structured JSON Gemini replies exist (`structured_reply=True` default).
- Confidence floor `min_confidence` exists (expose as slider in UI).

### 0.3 Gaps that MOTIVATE this roadmap (current code vs agreed product)

1. No AI-mode selector: worker hardcodes `GeminiWebBridge` only; REST API path unused by worker. Owner wants API-primary with fallback matrix.
2. API key not editable from dashboard (env-only read in `gemini_api.py`).
3. No group feeds: worker only scrolls `feed_url` (home).
4. No scroll-profile presets (numbers exist but no named modes Lazy/Slow/Fast).
5. Videos/reels are treated identically to text posts; no policy.
6. Failed capture always means instant skip-and-scroll; no retry option.
7. Seen-store never expires entries (only count-trims).
8. Comment prompt fixed per style; no language matching, no engagement tone, hashtags always stripped, emojis never allowed, word limit implicit.
9. Caps count reaction and comment as two actions on the same post; owner wants 1 post = 1 action.
10. Checkpoint handling has a fixed timeout; owner wants selectable indefinite-vs-timer.
11. No circuit breaker on consecutive Gemini failures.
12. Run length supports unlimited/max_posts but not time-boxed hours.
13. Run end always closes the browser; owner wants leave-open option.
14. No scheduler (manual start only).

---

## 1. MASTER DECISION REGISTER (verbatim outcomes of planning Q&A)

This register is AUTHORITATIVE. If any phase description seems to conflict
with a row here, the row here wins.

| # | Topic | Decision |
|---|---|---|
| D1 | Deliverable | Runbook/SOP-style documentation of behavior; this roadmap is the implementation context that precedes it |
| D2 | AI paths | BOTH Gemini Web UI and REST API supported. Settings dropdown combines them: API-only / Web-only / API->Web-fallback / Web->API-fallback / Race-both |
| D3 | AI preference | REST API is PRIMARY default because it replies instantly; Web UI is the slower fallback |
| D4 | API key | Editable API-key FIELD in the Module 1 dashboard settings panel |
| D5 | Browser visibility | Always VISIBLE window (headless option stays in .env for power users, UI defaults visible) |
| D6 | Login policy | Wait INDEFINITELY for the owner to log into Facebook and Google; work starts only after login detected |
| D7 | Feed targets | Home feed PLUS a saved group list; admin chooses which source first/later and defines per-source session quota in POSTS or MINUTES |
| D8 | Rotation modes | Combine all: ordered queue / random pick each cycle / home-first-then-groups / manual queue set at run start — all selectable |
| D9 | Group storage | Saved group URL LIST in module settings, managed from dashboard |
| D10 | Scroll style | Three selectable presets in dashboard: Lazy-human / Slow-careful / Fast-scan; user switches to balance detection-risk vs throughput |
| D11 | Reading dwell | Default 2–6 seconds random (min/max fields remain) |
| D12 | Video/Reels policy | All three selectable: engage normally / skip entirely / like-only-no-comment |
| D13 | Bad capture policy | Both selectable: retry up to 3x then skip / instant skip |
| D14 | Duplicate handling | Seen-list EXPIRES after X days (configurable X) so old posts can be re-engaged later |
| D15 | Comment language | MATCH the post's detected language (Urdu post -> Urdu comment, English -> English) |
| D16 | Reactions | AUTO by AI among LIKE/LOVE/CARE/HAHA/WOW (fixed/none modes retained) |
| D17 | Comment tone | NEW "engagement" style added (engagement-driving, curiosity hooks) alongside casual/friendly/professional/promotional/custom; all selectable |
| D18 | Text entry | Paste (fast) and humanized typing BOTH available; selector in settings |
| D19 | Typo rate | User slider 0–100%, default 20% |
| D20 | Min confidence | User slider 0–100% (maps to 0.0–1.0), default 40% |
| D21 | Comment length | Configurable max-word-limit field |
| D22 | Emojis | ON/OFF toggle in settings (prompt-level enforcement) |
| D23 | Hashtags | ALLOWED — AI choice; REMOVE the unconditional hashtag-stripping rule (make it conditional on a toggle defaulting to allow) |
| D24 | Post gap | User-set min/max seconds fields |
| D25 | Long breaks | Configurable: every N posts + break min/max minutes; disableable (N=0 off) |
| D26 | Hourly cap | User number field, 0=off, UI shows recommended hint ~20/hr |
| D27 | Daily cap | User number field, 0=off |
| D28 | Action counting | ONE POST = ONE ACTION for cap purposes (reaction+comment on same post counts once) |
| D29 | Checkpoint policy | Selectable: wait-indefinitely OR wait-X-minutes-then-auto-stop (+ X field) |
| D30 | AI failure breaker | Selectable circuit breaker: OFF or pause-after-N-consecutive-Gemini-failures (N field) |
| D31 | Run end | Selectable: auto-close-browser OR stay-open-on-feed |
| D32 | Scheduling | All modes: manual-only / daily-auto-start-at-HH:MM / interval-cycles(run X hrs, sleep Y hrs) |
| D33 | Run length | ALL available: unlimited-until-stopped / fixed-N-posts / time-boxed-X-hours |
| D34 | Work hours | Option KEPT in settings but DEFAULT OFF (empty = always allowed) |
| D35 | Config source | Everything controlled LIVE from dashboard settings (JSON-backed, applied without restart) |

---

## 2. PHASE INDEX (implementation order — STRICTLY SEQUENTIAL)

| Phase | Title | Depends on | Risk | Est. effort |
|---|---|---|---|---|
| PHASE-01 | Settings Schema v2 + persistence migration | none | low | 0.5 day |
| PHASE-02 | Login-wait: truly indefinite + visible-window guarantee | P01 | low | 2 h |
| PHASE-03 | AI Engine Selector (5 modes) + dashboard API key | P01 | medium | 1.5 days |
| PHASE-04 | Run-length modes incl. time-boxed | P01 | low | 3 h |
| PHASE-05 | Per-post action counting for caps | P01 | low | 2 h |
| PHASE-06 | Scroll profile presets | P01 | low | 2 h |
| PHASE-07 | Video/Reel policy | P06 | low | 3 h |
| PHASE-08 | Capture failure policy (retry/instant) | P07 | low | 2 h |
| PHASE-09 | Seen-store expiry (X days) | P01 | low | 2 h |
| PHASE-10 | Comment quality suite (language/tone/emoji/hashtag/length) | P03 | medium | 1 day |
| PHASE-11 | Checkpoint policy selector | P01 | low | 2 h |
| PHASE-12 | Gemini circuit breaker | P03 | low | 3 h |
| PHASE-13 | Feed rotation: home + groups, quotas, 4 modes | P06,P08,P09 | high | 2–3 days |
| PHASE-14 | Run-end policy (close vs stay-open) | P04 | medium | 0.5 day |
| PHASE-15 | Scheduler (manual/daily/interval) | P14 | medium | 1 day |
| PHASE-16 | Dashboard settings panel completion + hints | P01..P15 | low | 1 day |
| PHASE-17 | End-to-end verification runbook execution | all | low | 0.5 day |

Total estimate: ~8–10 focused working days.

---
---

# PHASE-01 — SETTINGS SCHEMA v2 + PERSISTENCE MIGRATION

- Status: `[x] DONE (2026-08-23)`
- Priority: 1 (foundation — every other phase writes into this schema)
- Depends on: none
- Files touched: `config.py`, (optionally) dashboard settings route docs

## Purpose

Add every new setting field the later phases need, in ONE schema revision,
so subsequent phases only consume fields instead of repeatedly migrating
persistence. This keeps `data/module1_settings.json` forward-compatible:
old files load fine, missing fields fall back to Pydantic defaults.

## New settings fields (complete list, exact names)

Group: **AI engine**
```
ai_mode: str = "api_primary_web_fallback"
    # one of {"api_only","web_only","api_primary_web_fallback",
    #         "web_primary_api_fallback","race_both"}
    # D2/D3. Validator coerces unknown -> default.
gemini_api_key: str = ""
    # D4. Empty string = fall back to env GEMINI_API_KEY /
    #     MOD1_AI_API_KEY. Non-empty OVERRIDES env everywhere.
gemini_api_model: str = ""
    # empty = env GEMINI_API_MODEL else "gemini-2.0-flash"
gemini_reply_wait_sec: int = 60   # EXISTS already; keep, used by Web mode
api_failure_is_fatal_for_post: bool = True
    # if ALL configured engines fail for a post -> skip post (True)
    # or treat as soft error and still try engagement without comment (False,
    # reserved; default True keeps current semantics)
```

Group: **Feed targets & rotation** (consumed fully by PHASE-13)
```
feed_rotation_mode: str = "ordered"
    # {"ordered","random","home_first","manual_queue"}   (D8)
feed_home_first_count: int = 20
    # posts spent on home before groups begin, when mode == home_first
feed_group_urls: list[str] = []
    # D9 saved group list; validated: must look like facebook.com/groups/...
feed_manual_queue: list[str] = []
    # explicit source order for manual_queue mode; entries are
    # "home" or a group URL; dashboard builds it
source_quota_unit: str = "posts"
    # {"posts","minutes"}   (D7)
source_quota_value: int = 25
    # meaning depends on unit: N posts per source, or N minutes per source
source_switch_cooldown_sec: int = 45
    # settle delay after navigating to a new source before scanning
```

Group: **Scroll & reading**
```
scroll_profile: str = "lazy"
    # {"lazy","slow","fast"}  (D10)
scroll_delta_min: int = 420   # EXISTS; becomes effective value for profile
scroll_delta_max: int = 780   # EXISTS; see PHASE-06 resolution rules
reading_dwell_min: float = 2.0   # EXISTS (D11)
reading_dwell_max: float = 6.0   # EXISTS (D11)
```

Group: **Post-type & capture policies**
```
video_policy: str = "like_only"
    # {"engage","skip","like_only"}   (D12)
capture_failure_policy: str = "instant_skip"
    # {"instant_skip","retry_limited"}  (D13)
capture_retry_limit: int = 3      # used when policy == retry_limited
```

Group: **Duplicate memory**
```
skip_already_commented: bool = True        # EXISTS
max_seen_posts: int = 5000                 # EXISTS
seen_expiry_days: int = 30                 # NEW (D14); 0 = never expire
```

Group: **Comment quality**
```
comment_style: str = "casual"      # EXISTS; gains "engagement" member
comment_language: str = "match_post"
    # {"match_post","urdu","english","custom"}   (D15)
custom_language_instruction: str = ""
    # used when comment_language == "custom"; appended to prompt verbatim
allow_hashtags: bool = True        # NEW (D23) — replaces unconditional strip
emoji_enabled: bool = False        # NEW (D22)
comment_max_words: int = 40        # NEW (D21); 0 = no explicit limit
min_confidence: float = 0.40       # EXISTS (D20 slider maps x/100)
typo_probability: float = 0.20     # EXISTS (D19 slider)
typing_style: str = "paste"        # EXISTS (D18)
reaction_mode: str = "auto"        # EXISTS (D16)
fixed_reaction: str = "LIKE"       # EXISTS
```

Group: **Pacing**
```
gap_between_posts_min: float = 5.0    # EXISTS (D24)
gap_between_posts_max: float = 15.0   # EXISTS (D24)
long_break_every: int = 0             # EXISTS (D25; 0 = off)
long_break_min: float = 60.0          # EXISTS
long_break_max: float = 180.0         # EXISTS
humanization_enabled: bool = True     # EXISTS
```

Group: **Caps & safety**
```
action_count_mode: str = "per_post"
    # {"per_post","per_action"}  (D28; default CHANGES semantics)
max_actions_per_hour: int = 0        # EXISTS (D26; UI adds hint)
max_actions_per_day: int = 0         # EXISTS (D27)
pause_on_checkpoint: bool = True     # EXISTS
checkpoint_policy: str = "wait_indefinitely"
    # {"wait_indefinitely","wait_then_stop"}   (D29)
checkpoint_wait_sec: int = 1800      # EXISTS; used only in wait_then_stop
work_hours_start: str = ""           # EXISTS (D34 default off)
work_hours_end: str = ""             # EXISTS
```

Group: **AI failure breaker**
```
circuit_breaker_enabled: bool = False        # D30
circuit_breaker_threshold: int = 5           # consecutive failures
circuit_breaker_cooldown_min: float = 10.0   # auto-resume after cooldown;
                                             # 0 = stay paused until Resume
```

Group: **Run shape & lifecycle**
```
run_length_mode: str = "unlimited"
    # {"unlimited","max_posts","time_boxed"}   (D33)
run_time_limit_min: int = 120        # used when time_boxed
run_end_policy: str = "close_browser"
    # {"close_browser","stay_open"}            (D31)
schedule_mode: str = "manual"
    # {"manual","daily_at","interval"}          (D32)
daily_start_time: str = ""           # "HH:MM"; used when daily_at
interval_run_hours: float = 2.0      # used when interval
interval_sleep_hours: float = 4.0    # used when interval
max_posts_per_run: int = 0           # EXISTS; used when max_posts (0=all)
```

## How it works (runtime)

No behavior changes in this phase alone. After merge:
1. App starts, `M1SettingsStore.__init__` runs `_load()` on existing JSON.
2. Pydantic fills every NEW field with its default because old JSON lacks them.
3. First `update()` from the dashboard persists the complete new schema to disk.
4. Old -> new is seamless; no manual migration script needed.

## Implementation notes

In `config.py`:

1. Extend module constants:
```python
AI_MODES = {
    "api_only", "web_only", "api_primary_web_fallback",
    "web_primary_api_fallback", "race_both",
}
ROTATION_MODES = {"ordered", "random", "home_first", "manual_queue"}
SCROLL_PROFILES = {"lazy", "slow", "fast"}
VIDEO_POLICIES = {"engage", "skip", "like_only"}
CAPTURE_POLICIES = {"instant_skip", "retry_limited"}
LANG_MODES = {"match_post", "urdu", "english", "custom"}
CHECKPOINT_POLICIES = {"wait_indefinitely", "wait_then_stop"}
RUN_LENGTH_MODES = {"unlimited", "max_posts", "time_boxed"}
RUN_END_POLICIES = {"close_browser", "stay_open"}
SCHEDULE_MODES = {"manual", "daily_at", "interval"}
COUNT_MODES = {"per_post", "per_action"}
COMMENT_STYLES = {"casual","friendly","professional","promotional","custom","engagement"}
```

2. Add every field above to `M1Settings` following the existing
   `Field(...)` style, grouped with the same comment-banner layout.

3. Add `field_validator`s mirroring existing coercion patterns
   (strip/lower, membership check, fallback to safe default):
```python
@field_validator("ai_mode")
@classmethod
def _v_ai_mode(cls, v: str) -> str:
    v = (v or "").strip().lower()
    return v if v in AI_MODES else "api_primary_web_fallback"

@field_validator("feed_group_urls", "feed_manual_queue")
@classmethod
def _v_url_list(cls, v: list[str]) -> list[str]:
    out = []
    for u in v or []:
        s = str(u).strip()
        if s == "home" or ("facebook.com" in s):
            out.append(s)
    return out[:50]  # sane ceiling

@field_validator("checkpoint_wait_sec", "circuit_breaker_threshold")
@classmethod
def _v_positive(cls, v: int) -> int:
    return max(0, int(v))
```

4. Cross-field validation additions (pattern exists: `_v_gap`):
   - `interval_sleep_hours >= 0.25` (never hot-loop).
   - `run_time_limit_min >= 5`.
   - when `checkpoint_policy == "wait_indefinitely"`,
     `checkpoint_wait_sec` is retained but unused (document in help text).
   - `comment_max_words`: 0..120 clamp.
   - `seen_expiry_days`: 0..365 clamp.

5. Update `compact()` (Quick Settings subset) to expose the user-facing
   knobs decided in planning: `ai_mode`, `gemini_api_key` (MASKED — see
   below), `scroll_profile`, `video_policy`, `capture_failure_policy`,
   `seen_expiry_days`, `comment_style`, `comment_language`,
   `emoji_enabled`, `allow_hashtags`, `comment_max_words`,
   `action_count_mode`, `checkpoint_policy`, `circuit_breaker_*`,
   `run_length_mode`, `run_end_policy`, `schedule_mode` + its params.

6. MASKING RULE for the API key (security golden rule):
   - `dump()` and `compact()` NEVER return the raw key. They return
     `"configured"` when non-empty, `""` when empty.
   - Add store method `set_secret(name, value)` that writes the real value
     into the persisted file but the public dumps keep masking.
   - `M1Settings.gemini_api_key` therefore stores truth internally;
     masking happens only at serialization-for-UI boundaries.
   - Rationale: dashboard is localhost-trusted, but screenshots/logs of
     the panel must not leak secrets.

7. Persistence: no format change (still one flat JSON object) -> zero
   migration code required.

## Edge cases

- JSON contains a key that no longer exists -> Pydantic `extra="ignore"`
  silently drops it (already true).
- JSON value wrong type (e.g. `seen_expiry_days: "soon"`) ->
  `M1Settings(**data)` raises ValueError inside `_load()`'s try-block ->
  whole file rejected, defaults used, warning printed. ACCEPTABLE but
  improve: catch per-field by loading into a dict, dropping bad-typed keys
  against the model's annotations, then constructing. Implement helper
  `_sanitize_raw(data: dict) -> dict` in the store and use it in `_load()`.
- Two processes writing settings concurrently -> existing threading.Lock
  covers in-process; cross-process risk unchanged (single dashboard
  assumed) — note only, no lockfile in scope.

## Acceptance criteria

- [ ] Fresh checkout with OLD `module1_settings.json` boots with zero errors
      and `GET /module1/config` returns all new fields with defaults.
- [ ] `POST` patch containing `{"ai_mode":"bogus"}` results in
      `warnings: ["ai_mode: ..."]`-style behavior or silent coercion per
      validator, and stored value equals the safe default.
- [ ] `gemini_api_key` set via dashboard then `GET config` shows
      `"configured"`, raw value appears ONLY inside the JSON file.
- [ ] Unit test: `test_settings_v2_defaults_and_masks` covering the above.

---
---

# PHASE-02 — LOGIN WAIT: TRULY INDEFINITE + VISIBLE-WINDOW GUARANTEE

- Status: `[x] DONE (2026-08-23)`
- Priority: 2
- Depends on: PHASE-01
- Files touched: `fb.py` (`_wait_login_if_needed`), `gemini_bridge.py`
  (login wait paths), `bot.py` (legacy parity, one-line), `worker.py`
  (phase messaging)

## Purpose

Decision D6: the owner logs in by hand, whenever they get to it. Today both
Facebook and Google waits are capped (300 s / 180 s env defaults) and then
RAISE, killing the run. New behavior: wait forever, showing a clear
dashboard phase, until login is detected OR the user presses Stop.
Stopping the run must always remain possible from the dashboard.

## Options

| Option | Setting | Behavior |
|---|---|---|
| Wait indefinitely | hard behavior (no setting) | loops until feed/login detected; only Stop cancels |
| (env escape hatch) | `FB_LOGIN_WAIT_MS=0` meaning infinite | keep env override for headless CI use; UI never sets it |

## Runtime behavior (step-by-step)

Facebook side:
1. Worker phase = `fb_login_wait`, detail text: "Sign in to Facebook in the
   browser window — Module 1 will start automatically after login."
2. Loop every 1.5 s:
   a. If stop-event set -> raise `_WorkerStopped` (clean shutdown path).
   b. Poll `_has_feed()`; when true -> print + emit "Login detected",
       sleep 1.0 s for render, return.
3. No deadline. No RuntimeError on timeout.

Google/Gemini side (`GeminiWebBridge._wait_for_google_login_if_needed`):
1. Same philosophy: replace `GEMINI_LOGIN_WAIT_MS` deadline with
   cancel_event-only termination.
2. Keep all existing recovery logic (navigate back to gemini.google.com
   after account picker, re-check composer readiness) unchanged.

Worker integration:
1. While paused-for-login, dashboard status MUST show the login phase and
   uptime counter keeps ticking (it does via snapshot).
2. Stop button works during login wait: worker thread checks
   `self._stop` each poll iteration (already the pattern inside
   `_checkpoint()`; replicate it inside the two wait loops).

## Implementation notes

`fb.py::_wait_login_if_needed` — rewrite core loop:

```python
async def _wait_login_if_needed(self) -> None:
    assert self.page is not None
    if await self._has_feed():
        return
    await self._emit("fb_login_wait")
    print("[M1 FB] Facebook login required — waiting indefinitely...")
    while True:
        await asyncio.sleep(1.5)
        if self._cancel is not None and self._cancel.is_set():
            raise RuntimeError("Cancelled during Facebook login wait")
        try:
            if self.page.is_closed():
                raise RuntimeError("Page closed during login wait")
            if await self._has_feed():
                print("[M1 FB] Login detected — continuing.")
                await asyncio.sleep(1.0)
                return
        except RuntimeError:
            raise
        except Exception as exc:
            # transient DOM/eval errors must not kill an indefinite wait
            continue
```

Constructor change: `FacebookAutomation.__init__(..., cancel_event=None)`
so the worker passes its `threading.Event` stop flag (`self._stop`),
exactly like GeminiWebBridge already accepts `cancel_event`.

`gemini_bridge.py`: in `_wait_for_google_login_if_needed`, delete the
deadline branch and timeout raise; loop becomes:

```python
while True:
    if self.cancel_event is not None and self.cancel_event.is_set():
        raise RuntimeError("Gemini login wait cancelled by user")
    ... existing detection/recovery logic ...
```

Keep `GEMINI_LOGIN_WAIT_MS` READ but reinterpret: value `0` = infinite
(new default when unset); any positive number still honored for automated
environments. This preserves backward compat without a config field.

Visible-window guarantee (D5): no code change needed (headless defaults
false), BUT add a startup log line and status detail when headless IS on:

```python
if headless:
    print("[M1 FB] WARNING: running headless — manual login will block "
          "forever. Set FB_HEADLESS=false for interactive runs.")
```

Legacy `bot.py::_wait_login_if_needed`: apply the same indefinite-wait +
stop-flag check so legacy bot matches product behavior.

## Edge cases

- User closes the browser window instead of logging in ->
  `page.is_closed()` raises path -> RuntimeError propagates -> worker
  finally-block closes everything, phase=error, message explains.
- User logs into the WRONG account -> out of scope; document in runbook.
- Sleep-based polling at 1.5 s is CPU-trivial; do NOT switch to
  page.wait_for_selector with long timeout because it can't observe the
  stop event mid-wait.

## Acceptance criteria

- [ ] Fresh profile start -> phase shows fb_login_wait indefinitely;
      dashboard Stop during this state ends cleanly within ~2 s.
- [ ] Logging in after 10+ minutes resumes automatically without restart.
- [ ] Same verified for Google sign-in wall before AND mid-Gemini-flow.
- [ ] `GEMINI_LOGIN_WAIT_MS=60000` still times out after 60 s (compat).

---
---

# PHASE-03 — AI ENGINE SELECTOR (5 MODES) + DASHBOARD API KEY

- Status: `[x] DONE (2026-08-23)`
- Priority: 3 (highest-value feature)
- Depends on: PHASE-01
- Files touched: NEW `ai_engine.py`, `gemini_api.py`,
  `gemini_bridge.py` (minor), `config.py::prompt()` (prep for P10),
  `worker.py` (call-site swap), runner status payload

## Purpose

Implement D2/D3/D4. Today the worker ONLY uses the slow Web-UI bridge and
the REST client is orphaned (used by legacy bot). The owner wants instant
API comments as primary, Web as fallback, five combinable modes, and the
key editable in the panel.

## The five modes (exact semantics)

| ai_mode | Meaning | Failure of preferred engine |
|---|---|---|
| `api_only` | REST API every post | skip post (record gemini_error) |
| `web_only` | Web bridge every post (today's behavior) | skip post |
| `api_primary_web_fallback` **(DEFAULT)** | try API first; on ANY exception or unparseable reply, immediately try Web for that same screenshot | if both fail -> skip |
| `web_primary_api_fallback` | inverse of above | if both fail -> skip |
| `race_both` | fire API + Web concurrently; FIRST valid decision wins; loser task cancelled | if either fails silently, winner still returns |

## Runtime behavior (per post, inside worker's "gemini" phase)

1. Worker obtains `engine.decide(screenshot_path)` — single entry point.
2. Engine reads live settings each call (mode may have been changed
   mid-run by owner).
3. Mode dispatch per table above.
4. Result contract (unchanged shape): `{"reaction","comment","confidence",
   "source"}` where source ∈ {`gemini_api`,`gemini_web`} for telemetry,
   plus `"attempts": ["api","web"]` list appended by engine.
5. Worker treats empty comment / confidence < min_confidence exactly as
   today (skip path).

## Implementation notes

### 3.1 New file `ai_engine.py`

```python
"""
Module 1 — AI Engine facade.

Chooses between Gemini REST API and Gemini Web UI according to
settings.ai_mode. Single entry point used by Module1Worker.
"""

import asyncio
from typing import Any, Dict, Optional

class AIEngine:
    def __init__(self, store, web_bridge_factory, cancel_event=None):
        # store: M1SettingsStore (live reads)
        # web_bridge_factory: callable() -> GeminiWebBridge bound to context
        self.store = store
        self._bridge_factory = web_bridge_factory
        self._bridge = None
        self.cancel_event = cancel_event

    async def ensure_ready(self) -> None:
        """Pre-open Web tab ONLY if current mode can ever use it."""
        s = self.store.get()
        if s.ai_mode in {"web_only", "web_primary_api_fallback",
                         "race_both"}:
            self._bridge = self._bridge_factory()
            await self._bridge.ensure_ready()
        # api_primary_web_fallback opens lazily on first need so a pure-API
        # user never sees a Gemini tab flash open.

    async def decide(self, screenshot_path: str) -> Dict[str, Any]:
        mode = self.store.get().ai_mode
        if mode == "api_only":
            return await self._via_api(screenshot_path)
        if mode == "web_only":
            return await self._via_web(screenshot_path)
        if mode == "race_both":
            return await self._race(screenshot_path)
        order = ("api", "web") if mode == "api_primary_web_fallback" \
                else ("web", "api")
        return await self._fallback_chain(order, screenshot_path)

    async def _fallback_chain(self, order, path):
        errors = []
        attempts = []
        for which in order:
            try:
                fn = self._via_api if which == "api" else self._via_web
                d = await fn(path)
                d["attempts"] = attempts + [which]
                return d
            except Exception as exc:
                errors.append(f"{which}: {type(exc).__name__}: {exc}")
                attempts.append(which)
        raise RuntimeError("All AI engines failed :: " + " | ".join(errors))

    async def _race(self, path):
        api_t = asyncio.create_task(self._safe(self._via_api(path)))
        web_t = asyncio.create_task(self._safe(self._via_web(path)))
        done, pending = await asyncio.wait(
            {api_t, web_t}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        results = [t.result() for t in done if not t.cancelled()]
        ok = [r for r in results if r is not None]
        if not ok:
            raise RuntimeError("race_both: both engines failed")
        # prefer whichever finished first that is valid; _safe swallows
        # exceptions returning None so FIRST_COMPLETED + not-None wins.
        return ok[0]

    async def _safe(self, coro):
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _via_api(self, path) -> Dict[str, Any]:
        from services.module_1_feed_observer.gemini_api import \
            generate_engagement_v2
        s = self.store.get()
        return await generate_engagement_v2(
            path,
            api_key=(s.gemini_api_key or None),
            model=(s.gemini_api_model or None),
            prompt=self.build_prompt(s),
            structured=True,
        )

    async def _via_web(self, path) -> Dict[str, Any]:
        if self._bridge is None:
            self._bridge = self._bridge_factory()
        await self._bridge.ensure_ready()
        s = self.store.get()
        self._bridge.prompt = self.build_prompt(s)
        self._bridge.structured = True
        return await self._bridge.generate_decision(path)

    # prompt builder fully realized in PHASE-10; minimal version now:
    def build_prompt(self, s) -> str:
        return s.prompt()   # config.py already composes style+JSON suffix
```

Design rules encoded above:
- Engine NEVER caches settings across calls beyond one decision.
- Web bridge lazily constructed so `api_only` users don't pay tab cost.
- `race_both` cancellation is best-effort; a cancelled Web upload may
  leave a stray draft in the Gemini tab — acceptable, next fresh-chat
  clears it (`fresh_chat_per_post` stays True default).

### 3.2 `gemini_api.py` — new function (keep old for legacy bot)

```python
async def generate_engagement_v2(
    screenshot_path: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    prompt: str = "",
    structured: bool = True,
) -> Dict[str, Any]:
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("MOD1_AI_API_KEY") or ""
    if not key or key.startswith(("sk-proj-your", "your")):
        raise RuntimeError("No usable Gemini API key")
    mdl = model or os.getenv("GEMINI_API_MODEL") or os.getenv(
        "MOD1_AI_MODEL", "gemini-2.0-flash")
    base = os.getenv("GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    b64 = base64.b64encode(Path(screenshot_path).read_bytes()).decode()
    payload = {
        "contents": [{"parts": [
            {"text": prompt or PROMPT},
            {"inline_data": {"mime_type": "image/png", "data": b64}},
        ]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 200},
    }
    url = f"{base}/models/{mdl}:generateContent?key={key}"
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(url, json=payload)
        res.raise_for_status()
        body = res.json()
    text = (body.get("candidates",[{}])[0].get("content",{})
              .get("parts",[{}])[0].get("text",""))
    decision = parse_gemini_reply(text, structured=structured)
    decision["source"] = "gemini_api"
    return decision
```

Import `parse_gemini_reply` from gemini_bridge (single parser, two
transports) — delete duplicated `parse_api_reply` logic over time;
keep it untouched this phase for legacy-bot safety.

### 3.3 `worker.py` swap

Replace direct construction:

```python
# BEFORE
gemini = GeminiWebBridge(fb.context, ...)
await gemini.ensure_ready()
...
decision = await gemini.generate_decision(shot["screenshot"])

# AFTER
engine = AIEngine(self.store,
                  web_bridge_factory=lambda: GeminiWebBridge(
                      fb.context,
                      structured=True,
                      response_wait_ms=int(settings.gemini_reply_wait_sec*1000),
                      gemini_url=settings.gemini_url,
                      fresh_chat=settings.fresh_chat_per_post,
                      cancel_event=self._stop),
                  cancel_event=self._stop)
if settings.ai_mode != "api_only":
    await engine.ensure_ready()
...
decision = await engine.decide(shot["screenshot"])
```

Finally-block: close engine's bridge if present
(`await engine.aclose()` — add small method closing `self._bridge`).
Runner status gains `last_ai_source` (from decision["source"]) so the
panel shows which engine answered each post.

### 3.4 Dashboard key handling

- Settings route already generic (`update(patch)` whitelist-by-model-keys),
  so `gemini_api_key` PATCHes just work once field exists (P01).
- MASKING enforced in dump()/compact() (P01 §Implementation step 6).
- Add `POST /module1/config/test-ai` endpoint (runner method
  `test_ai_engine()`): runs `generate_engagement_v2` against a tiny
  built-in 1x1 PNG with prompt "Reply with JSON {\"ok\":true}" and
  returns {ok, latency_ms, source} so the owner can verify the key
  WITHOUT starting a run. Web-mode test omitted (would need browser).

## Edge cases

- Key invalid/quota-exceeded in api_primary mode -> exception bubbles ->
  chain falls to Web for that post; ALSO increments consecutive-failure
  counter consumed later by PHASE-12 breaker (counter lives in worker,
  fed from decision exceptions).
- race_both doubles quota burn — warn once in UI help text (P16).
- Screenshot deleted between chain attempts? Worker owns deletion only in
  its own finally AFTER decide() returns/raises — safe.
- Mid-run mode switch api->web: next loop iteration ensure_ready() opens
  tab lazily; no restart needed.

## Acceptance criteria

- [ ] With valid key + `api_only`: comments appear < 5 s/post, no second
      browser tab ever opens.
- [ ] `api_primary_web_fallback` with BROKEN key: post still gets commented
      via Web; status shows source=gemini_web, attempts=["api","web"].
- [ ] `race_both`: replies arrive from fastest engine; exactly one comment
      posted (never two).
- [ ] Key set/cleared from panel persists to JSON masked in GET responses.
- [ ] test-ai endpoint returns latency for good key, structured error for bad.

---
---

# PHASE-04 — RUN-LENGTH MODES INCLUDING TIME-BOXED RUNS

- Status: `[x] DONE (2026-08-23)`
- Priority: 4
- Depends on: PHASE-01
- Files touched: `worker.py` (gate section), runner stop_reason strings

## Purpose

D33: owner chooses how a run ends — forever-until-stopped, N posts, or
X hours. max_posts exists; add time-boxed and unify the gate.

## Runtime behavior

At top of every loop iteration (before schedule gates):

1. Read `run_length_mode`.
2. `unlimited` -> no check.
3. `max_posts` -> existing check `posts_engaged >= max_posts_per_run`
   (only when max_posts_per_run > 0; 0 treated as unlimited regardless
   of mode to avoid instant-stop footgun — document in UI hint).
4. `time_boxed` -> `elapsed = now - started_at`;
   if `elapsed >= run_time_limit_min * 60` -> set
   `stop_reason="time_limit_reached"` and break.
5. Snapshot gains `run_time_used_sec` + `run_time_limit_sec` so the panel
   can show a progress bar.

## Implementation notes

In `run()` init block, capture `started_at` (already stored). In the gate
section insert:

```python
elif settings.run_length_mode == "time_boxed" and \
        settings.run_time_limit_min > 0:
    limit = settings.run_time_limit_min * 60
    elapsed = time.time() - snap_started_at
    if elapsed >= limit:
        stop_reason = "time_limit_reached"
        break
```

Expose in `_idle_snapshot`/reset block:
`run_time_used_sec=0, run_time_limit_sec=...`; update inside
`_set_snapshot(run_time_used_sec=int(now-started_at))` piggybacked onto
existing periodic updates (cheapest place: `snapshot()` computes it live
like `uptime_sec` already does — prefer this, zero extra writes).

Validator (P01): `run_time_limit_min` clamped 5..24*60.

## Edge cases

- Owner lowers the time limit below already-elapsed time mid-run ->
  stops on NEXT gate check (≤1 cycle latency) — correct.
- Pause counts toward wall-clock limit (deliberate: caps protect account
  wall-clock presence, not active seconds) — document in help text.
- Combined with scheduler (P15): interval mode uses time_boxed internally
  by writing derived settings, NOT by special-casing worker.

## Acceptance criteria

- [ ] time_boxed 5-min run auto-stops at ~5:00 with stop_reason visible.
- [ ] Switching modes mid-run applies next cycle without restart.
- [ ] unlimited + daily cap still terminates (cap precedence preserved:
      daily cap > run-length, since cap is a safety device).

---
---

# PHASE-05 — PER-POST ACTION COUNTING FOR CAPS

- Status: `[x] DONE (2026-08-23)`
- Priority: 5
- Depends on: PHASE-01
- Files touched: `worker.py` (`_record_action`, engage block), snapshot

## Purpose

D28. Today a post where the bot both reacts AND comments consumes 2 units
of the hourly/daily caps (`_record_action` fires on react success AND on
comment success). Owner decision: ONE POST = ONE ACTION, because
Facebook's risk model keys on posts-interacted-with, not sub-actions.
Legacy counting stays available via `action_count_mode`.

## Options

| action_count_mode | Semantics |
|---|---|
| `per_post` **(default)** | one timestamp per engaged post regardless of reaction/comment outcomes |
| `per_action` | today's behavior (each successful react and each successful comment counts) |

## Runtime behavior

In the engage section:

1. Track outcome flags: `did_react`, `did_comment`.
2. AFTER both attempts complete, record ONCE when per_post:
   - `if did_react or did_comment:` -> single `_record_action(settings)`.
3. per_action mode keeps today's two inline calls exactly as-is.
4. Seen-store marking / posts_engaged increments unchanged.

## Implementation notes

```python
# engage section restructure in worker.run()
did_react = False
if reaction not in {"NONE", ""}:
    await self._checkpoint()
    ok_r = await fb.react(post, reaction)
    if settings.action_count_mode == "per_action" and ok_r:
        self._record_action(settings)
    did_react = bool(ok_r)

did_comment = False
if settings.comment_enabled:
    await self._checkpoint()
    ok_c = await fb.comment(post, comment, style=settings.typing_style)
    if settings.action_count_mode == "per_action" and ok_c:
        self._record_action(settings)
    did_comment = bool(ok_c)

if settings.action_count_mode == "per_post" and (did_react or did_comment):
    self._record_action(settings)
```

No changes to `_cap_status` — it already just counts stored timestamps.

UI hint text (delivered in P16): "Recommended: per-post counting +
hourly cap ~20 = about 20 posts/hour ceiling."

## Edge cases

- Reaction succeeded but comment box missing -> still counts 1 in
  per_post mode; correct by definition.
- Both failed -> no count in either mode; post NOT marked seen so a
  future run may retry it — preserve this existing behavior explicitly.
- Owner flips mode mid-run -> next engaged post uses new semantics;
  historical timestamps unchanged (mixed accounting inside one window,
  acceptable + documented in help text).

## Acceptance criteria

- [ ] Engage a post with both actions under cap=3/hour, per_post:
      hourly_used reads 1, not 2.
- [ ] Same scenario under per_action: reads 2 (regression-safe).
- [ ] Failed-everything post increments neither counter nor seen-store.

---
---

# PHASE-06 — SCROLL PROFILE PRESETS

- Status: `[x] DONE (2026-08-23)`
- Priority: 6
- Depends on: PHASE-01
- Files touched: `config.py` (presets + resolution), `fb.py`
  (`scroll_to_next_post` consumer), `worker.py` (settings refresh),
  dashboard select control

## Purpose

D10. Named scroll personalities instead of raw pixel numbers:
Lazy-human (safe default), Slow-careful (max stealth), Fast-scan
(throughput). Preset selection writes through to the existing min/max
fields so `fb.py` stays dumb and the JSON file always reflects reality.

## Presets

| Profile | delta_min | delta_max | inter-chunk sleep multiplier | Feel |
|---|---|---|---|---|
| `lazy` | 420 | 780 | 1.0 | current default thumb-flick, 3–6 steps |
| `slow` | 200 | 400 | 1.5 | deliberate reader |
| `fast` | 800 | 1400 | 0.7 | skimmer |

## Runtime behavior

1. Worker refreshes live settings each loop cycle and rebinds
   `fb.settings = settings` next to `_apply_typing_profile(settings)`.
2. `scroll_to_next_post()` calls `settings.effective_scroll()`
   -> `(min_px, max_px, sleep_mul)`.
3. Wheel chunks computed from effective range; sleeps multiplied.

Custom-vs-preset rule: if an update patch sends explicit
`scroll_delta_min/max` WITHOUT a profile change, values stick verbatim
(UI then shows profile selector as "custom"). If patch sets a named
profile and does NOT send explicit deltas in the same transaction,
write-through copies preset numbers into min/max.

## Implementation notes

```python
SCROLL_PRESETS = {
    "lazy": (420, 780, 1.0),
    "slow": (200, 400, 1.5),
    "fast": (800, 1400, 0.7),
}

def effective_scroll(self) -> tuple[int, int, float]:
    pre = SCROLL_PRESETS.get(self.scroll_profile)
    if pre:
        return pre
    return self.scroll_delta_min, self.scroll_delta_max, 1.0
```

Because write-through happens in `M1SettingsStore.update()` (see below),
`effective_scroll()` can simply return presets whenever profile is a
known name; manual numbers only matter while UI shows custom state
(profile field value "" allowed? NO — keep enum-only; custom state is
represented by preset value whose numbers were hand-edited after; add
validator note: unknown profile strings coerce to "lazy").

Store.update() addition after validation succeeds:

```python
if updated.scroll_profile in SCROLL_PRESETS and \
        not ({"scroll_delta_min", "scroll_delta_max"} & set(patch or {})):
    pmin, pmax, _ = SCROLL_PRESETS[updated.scroll_profile]
    updated = updated.model_copy(update={
        "scroll_delta_min": pmin, "scroll_delta_max": pmax})
```

fb.py::scroll_to_next_post:

```python
dmin, dmax, smul = self.settings.effective_scroll()
total = random.randint(dmin, dmax)
...
await asyncio.sleep(random.uniform(0.08, 0.28) * smul)
```

Also apply smul to `_human_scroll_post_to_center` chunk sleeps for a
consistent personality.

## Edge cases

- slow profile + long_break off -> still safe; dwell dominates pace.
- fast + checkpoint indefinite -> fine; checkpoint scan is per-post.
- Patch with BOTH profile and explicit deltas -> deltas win; UI custom.

## Acceptance criteria

- [ ] Each preset changes real wheel distance (debug log total px).
- [ ] Manual delta edit sticks; selecting Lazy snaps back to 420/780.
- [ ] Mid-run switch applies within one post gap; no restart.

---
---

# PHASE-07 — VIDEO / REEL POLICY

- Status: `[x] DONE (2026-08-23)`
- Priority: 7
- Depends on: PHASE-06
- Files touched: `fb.py` (classify helper), `worker.py` (gate), config

## Purpose

D12. Videos/reels need different treatment: commenting on a muted
autoplay you never watched looks robotic; skipping avoids embarrassment;
like-only is the balanced owner-chosen default.

## Options

| video_policy | Behavior on detected video/reel |
|---|---|
| `engage` | identical to normal post (AI sees the frame, decides everything) |
| `skip` | scroll past immediately; posts_skipped+1; screenshot deleted; NOT marked seen |
| `like_only` **(default)** | click Like, NO Gemini call, NO comment; marks seen; counts 1 action (P05 rules); last_reaction=LIKE |

## Detection heuristics (ordered, best-effort)

1. Permalink href contains `/videos/`, `/reel/`, `/watch/`
   (language-independent, primary signal).
2. Post contains a `<video>` element descendant.
3. aria-label containing "reel" (English UIs only; documented caveat).

`capture_post()` result gains `"kind": "text" | "video"`.

## Runtime behavior — loop ordering decision

Dedup check comes FIRST, then kind-gate:

```
shot = capture_post()
if not shot.ok            -> capture-policy path (PHASE-08)
if seen.contains(url)     -> delete png, scroll, continue  (unchanged)
if shot.kind == "video":
    policy = settings.video_policy
    "skip"      -> posts_skipped+1; delete png; scroll; continue
    "like_only" -> short dwell 1–2.5 s; _checkpoint();
                   fb.react(post,"LIKE"); record per P05;
                   seen.add(url); likes_done+1; last_reaction="LIKE";
                   posts_engaged+1; checkpoint scan; pacing; scroll;
                   continue
    "engage"    -> fall through to Gemini flow unchanged
```

like_only still respects: pause gate before the click, caps checked at
loop top, post-action checkpoint scan, long-break counter.

## Implementation notes

```python
_VIDEO_HREF_HINTS = ("/videos/", "/reel/", "/watch/")

async def classify_post(self, post: Locator,
                        url: Optional[str]) -> str:
    try:
        if url and any(h in url for h in _VIDEO_HREF_HINTS):
            return "video"
        if await post.locator("video").count() > 0:
            return "video"
    except Exception:
        pass
    return "text"
```

Worker: add cosmetic phase detail `"skipping_video":
"Video/reel handled by policy"` in PHASES.

Short dwell for like_only (tap-and-go reads human):

```python
await self._sleep(random.uniform(1.0, 2.5))
```

Guard contradiction like_only + reaction_mode none => stronger rule wins:

```python
effective_like_only = (settings.video_policy == "like_only"
                       and settings.reaction_mode != "none")
# if contradiction -> behave as skip
```

## Edge cases

- Carousel/multi-image: classified text; AI handles fine — out of scope.
- Live videos: video bucket; never commented under like_only/skip.
- Misclassified GIF-as-video: conservative bias accepted; owner can set
  engage to override globally.
- reaction fails during like_only (button missing): treat like failed
  capture of the ACTION: no seen-marking, posts_skipped+1, scroll on.

## Acceptance criteria

- [ ] Known video post: skip->untouched; like_only->Like only, zero AI
      calls logged for it; engage->full flow.
- [ ] Caps consumed 1 per liked video (per_post mode).
- [ ] Policy switch mid-run honored from next post.

---
---

# PHASE-08 — CAPTURE FAILURE POLICY (RETRY vs INSTANT SKIP)

- Status: `[x] DONE (2026-08-23)`
- Priority: 8
- Depends on: PHASE-07
- Files touched: `worker.py` (capture section)

## Purpose

D13. Today one bad capture = instant scroll-past (`if not shot["ok"]:
scroll; continue`). Sometimes the feed serves an ad skeleton or a
half-rendered unit that renders fine moments later. Owner wants both
strategies selectable.

## Options

| capture_failure_policy | Behavior when shot.ok == False |
|---|---|
| `instant_skip` **(default)** | scroll_to_next_post immediately (today's behavior) |
| `retry_limited` | up to `capture_retry_limit` consecutive failures on the SAME viewport region: micro-scroll ~300px via slow_scroll, wait 1.5–3 s, re-capture; only after limit reached do a full scroll_to_next_post and reset the counter |

Counter resets on ANY successful capture.

## Runtime behavior

```
fail_streak = 0   # worker-run scope, init in run()
...
shot = await fb.capture_post()
self._set_snapshot(posts_seen=+1)
if not shot["ok"]:
    if settings.capture_failure_policy == "retry_limited" and \
            fail_streak < max(1, settings.capture_retry_limit):
        fail_streak += 1
        phase_detail = f"Capture failed — retry {fail_streak}/{limit}"
        await fb.slow_scroll(random.randint(240, 360))
        await self._sleep(random.uniform(1.5, 3.0))
        continue            # loop top re-checks gates; next capture retries
    fail_streak = 0
    await fb.scroll_to_next_post()
    continue
fail_streak = 0          # success path
```

Note posts_seen still increments per attempt (honest telemetry of feed
units observed); consider separate counter later if noisy.

## Implementation notes

- Pure control-flow change inside worker.run(); no new fb methods needed
  (`slow_scroll` already exists).
- Keep `_checkpoint()` semantics: retries pass through loop-top gate so
  Stop/Pause respond within ~0.25 s even mid-retry-streak.
- Phase detail text uses PHASES["scanning"] with dynamic detail param
  (mechanism already supports detail override).

## Edge cases

- Persistent broken feed area (e.g., end-of-feed placeholder): streak
  exhausts -> full scroll advances past it; loop continues.
- Retry storm safety: hard ceiling = retry limit per bad unit; a truly
  empty viewport loops micro-scrolls at most N times then advances — no
  infinite spin.
- Owner lowers limit mid-streak below current streak -> next failure
  triggers advance immediately (comparison is `<`).

## Acceptance criteria

- [ ] Simulated flaky selector (dev flag forcing one False) recovers with
      retry_limited but skips instantly under default.
- [ ] Streak never exceeds configured limit; counter visible in phase
      detail string.
- [ ] Stop during retry streak exits cleanly.

---
---

# PHASE-09 — SEEN-STORE EXPIRY (X DAYS)

- Status: `[x] DONE (2026-08-23)`
- Priority: 9
- Depends on: PHASE-01
- Files touched: `seen_store.py`, `worker.py` (one line), config

## Purpose

D14. Owner wants old posts re-engageable: a seen-entry older than
`seen_expiry_days` stops blocking. Current store only trims by COUNT.

## Runtime behavior

1. On worker start (SeenStore construction), purge expired entries:
   entries whose timestamp < now - expiry_days*86400 are deleted and the
   file re-saved. Log purged count.
2. `contains(url)` also treats expired as absent (belt-and-braces for
   long-running processes that never restarted).
3. expiry 0 = never expire (pure legacy semantics).

## Implementation notes

```python
class SeenStore:
    def __init__(self, path=None, max_entries=5000, expiry_days=30):
        self._expiry_sec = max(0, int(expiry_days)) * 86400
        ...

    def _expired(self, ts: float) -> bool:
        return self._expiry_sec > 0 and (time.time() - ts) > self._expiry_sec

    def _purge(self) -> int:
        if not self._urls:
            return 0
        dead = [u for u, t in self._urls.items() if self._expired(t)]
        for u in dead:
            del self._urls[u]
        if dead:
            self._save()
        return len(dead)

    def contains(self, url: str) -> bool:
        with self._lock:
            ts = self._urls.get(url or "")
            if ts is None:
                return False
            if self._expired(ts):
                del self._urls[url or ""]
                self._save()
                return False
            return True
```

Worker construction site passes settings:

```python
seen = SeenStore(max_entries=settings.max_seen_posts,
                 expiry_days=settings.seen_expiry_days)
purged = seen.purge()      # public wrapper calling _purge under lock
if purged:
    print(f"[M1 Seen] Purged {purged} expired entries")
self._set_snapshot(seen_count=seen.count())
```

Add public `purge()` returning count (keeps _purge private).

Trim-vs-expiry interplay: keep existing max_entries trim AFTER expiry
purge so both invariants hold (`add()` unchanged except it already
saves; expired entries can be re-added naturally).

## Edge cases

- Clock skew / manual JSON edit with future timestamps -> treated as
  fresh; harmless.
- expiry=0 with huge file -> load time grows; max_entries still bounds.
- Same post reappears within window -> correctly skipped (no change).
- Dashboard "clear seen list" button (exists via store.clear()) — expose
  in P16 panel next to expiry field.

## Acceptance criteria

- [ ] Seed JSON with entry timestamped 40 days ago, expiry 30 ->
      startup log shows purge of 1; contains() false for it.
- [ ] expiry=0 keeps the same entry blocking.
- [ ] File on disk rewritten without the expired key.

---
---

# PHASE-10 — COMMENT QUALITY SUITE (LANGUAGE / TONE / EMOJI / HASHTAG / LENGTH)

- Status: `[x] DONE (2026-08-23)`
- Priority: 10
- Depends on: PHASE-03 (engine facade owns prompt building)
- Files touched: `config.py` (`prompt()` rewrite + style table),
  `gemini_bridge.py` (strip-rule gating), `worker.py` (strip gating)

## Purpose

D15/D17/D21/D22/D23. One prompt-builder upgrade covering five owner
decisions, applied identically to API and Web engines via the P03
facade's single `build_prompt()` path.

## Decisions encoded

| Decision | Mechanism |
|---|---|
| D15 match post language | instruction block inside every prompt |
| D17 "engagement" tone | new member of COMMENT_STYLES with dedicated base prompt |
| D21 length cap | explicit word ceiling sentence when comment_max_words > 0 |
| D22 emoji toggle | conditional allow/forbid clause |
| D23 hashtags allowed | conditional clause; strip-rules in code become gated |

## New "engagement" style prompt (base text)

```
Act as a real, socially savvy Facebook user whose goal is genuine
engagement. Look at this post and write ONE short comment that sparks
connection: react to the specific content, show warm interest, and where
natural end with a light question or invitation that makes the author
want to reply. Never generic ("Nice!", "Great post"), never salesy,
never bot-like.
```

Style table becomes dict of dicts so language/emoji clauses wrap ANY
style uniformly.

## Prompt assembly algorithm (config.M1Settings.prompt())

```
base = STYLE_PROMPTS[comment_style]  (or custom_prompt if set)
lang_clause:
    match_post -> "Write the comment in the SAME language the post is
                   written in. Urdu post => Urdu comment; English =>
                   English; mixed => the dominant language."
    urdu       -> fixed Urdu-instruction line (reuse existing PROMPT's
                  Urdu sentence, translated directive form)
    english    -> "Write the comment in English."
    custom     -> custom_language_instruction verbatim
emoji_clause:
    enabled  -> "At most ONE emoji is allowed, only if it fits naturally."
    disabled -> "No emojis."
tag_clause:
    allow_hashtags -> ""   (AI choice — say nothing)
    else           -> "No hashtags (#)."
len_clause:
    comment_max_words > 0 -> f"Hard limit: {n} words maximum."
json_suffix (unchanged structured_reply contract):
    ' Return ONLY JSON, no markdown, no explanation:
      {"reaction":"LIKE|LOVE|CARE|HAHA|WOW",
       "comment":"<the single best comment>"}'
prompt = " ".join(filter(None,
         [base, lang_clause, emoji_clause, tag_clause, len_clause])) \
         + json_suffix
```

## Code-side stripping gates

Three places currently unconditionally remove hashtags:

1. `worker.py` after decision: `re.sub(r"#\w+", "", decision["comment"])`
2. `gemini_bridge.generate_decision()` same pattern
3. `gemini_bridge.parse_gemini_reply()` inside JSON branch + fallbacks
4. `fb.comment()` entry-level strip

Gate ALL of them on settings.allow_hashtags (pass flag down or check at
single choke point). CHOSEN DESIGN: keep stripping logic but make it a
helper used conditionally —

```python
def maybe_strip_tags(text: str, allowed: bool) -> str:
    return text if allowed else re.sub(r"#\w+", "", text or "").strip()
```

- parse_gemini_reply gains optional kw `strip_tags=True`; engine passes
  `not settings.allow_hashtags`.
- worker/fb call sites switch to helper with live setting read.
- Length enforcement stays SOFT (prompt) + HARD truncation safety:
  existing `[:280]` char cap retained regardless (platform sanity), plus
  word-count guard in worker:

```python
if s.comment_max_words > 0:
    words = comment.split()
    if len(words) > s.comment_max_words:
        comment = " ".join(words[: s.comment_max_words])
        # trailing punctuation cleanup
        comment = comment.rstrip(",;:-— ") 
```

Emoji hard-enforcement when toggle off (model may disobey):

```python
if not s.emoji_enabled:
    comment = _EMOJI_RE.sub("", comment).strip()
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F000-\U0001F02F"
    "\U00002600-\U000026FF]+", flags=re.UNICODE)
```

## Edge cases

- match_post + screenshot-only content (no visible text): instruct model
  "If the post has no readable text, infer from imagery and write in the
  account's primary audience language (default English)." Add as final
  lang_clause fallback sentence for match_post mode.
- custom_language_instruction empty while mode=custom -> validator
  coerces mode back to match_post (add cross-field rule).
- comment_max_words=0 -> no len_clause, no truncation beyond 280 chars.
- Web bridge structured=False legacy path: prompt builder still applies;
  parser handles plain text (existing).

## Acceptance criteria

- [ ] Urdu-language test image yields Urdu comment; English image yields
      English; both under match_post.
- [ ] engagement style comments are non-generic and often end with a
      question (manual spot-check over 10 posts).
- [ ] emoji off -> zero emojis in last_comment across 20-post run even
      if raw reply contained them.
- [ ] allow_hashtags=True passes through any AI hashtag untouched.
- [ ] Word cap enforced exactly at configured boundary.

---
---

# PHASE-11 — CHECKPOINT POLICY SELECTOR

- Status: `[x] DONE (2026-08-23)`
- Priority: 11
- Depends on: PHASE-01
- Files touched: `worker.py` (`_handle_checkpoint`), snapshot detail

## Purpose

D29. Owner chooses between waiting forever for a manual checkpoint
clear (preferred when at the PC) vs auto-stopping after X minutes
(unattended safety).

## Options

| checkpoint_policy | Behavior |
|---|---|
| `wait_indefinitely` **(default)** | phase=checkpoint_paused; loop polls `checkpoint_detected()` every 3 s until clear or user Stop |
| `wait_then_stop` | same, plus deadline = now + checkpoint_wait_sec; on expiry set last_error="Checkpoint unresolved — auto-stopped", raise _WorkerStopped |

## Runtime behavior

Existing `_handle_checkpoint` already implements wait-then-stop with
deadline=None-capable structure. Change is parameterization:

```python
async def _handle_checkpoint(self, fb, settings):
    await self._set_phase("checkpoint_paused",
        detail="Facebook security check detected — verify in browser")
    deadline = None
    if settings.checkpoint_policy == "wait_then_stop" and \
            settings.checkpoint_wait_sec > 0:
        deadline = time.time() + settings.checkpoint_wait_sec
    while True:
        if self._stop.is_set():
            raise _WorkerStopped()
        if not self._pause_gate.is_set():
            await self._checkpoint()
            continue
        if not await fb.checkpoint_detected():
            await self._set_phase("running")
            return
        if deadline is not None and time.time() > deadline:
            self._set_snapshot(
                last_error="Checkpoint unresolved — auto-stopped")
            raise _WorkerStopped()
        await asyncio.sleep(3.0)
```

Snapshot additions: `checkpoint_since` timestamp while paused so panel
can render elapsed timer.

## Implementation notes

- pause_on_checkpoint=False legacy behavior unchanged: detection gate is
  skipped entirely before reaching handler (existing condition).
- Detail string should include remaining minutes under wait_then_stop,
  refreshed each poll iteration via _set_phase detail param.
- Cross-field: validator keeps checkpoint_wait_sec meaningful only for
  wait_then_stop; UI hides/disables it otherwise (P16).

## Edge cases

- Checkpoint text false-positive (friend's post literally says "security
  check") -> existing phrase-scan limitation, unchanged; mitigation
  documented in runbook: owner tunes phrases list in fb.py constant.
- Stop pressed during indefinite wait -> clean exit (loop checks flag).
- Resume/Pause during checkpoint -> `_checkpoint()` interplay preserved
  (pause gate honored inside loop).

## Acceptance criteria

- [ ] Injected checkpoint page: indefinite mode waits >10 min without
      error; clearing resumes into running phase automatically.
- [ ] wait_then_stop 2-min config stops at ~2 min with exact last_error
      string above.
- [ ] Dashboard timer shows growing elapsed while blocked.

---
---

# PHASE-12 — GEMINI CIRCUIT BREAKER

- Status: `[x] DONE (2026-08-23)`
- Priority: 12
- Depends on: PHASE-03
- Files touched: `worker.py` (Gemini error path + success reset), new
  phase string, snapshot fields

## Purpose

D30. When AI is systematically down (bad key, quota, Gemini outage,
login loop), today the worker burns every post into a skip+error cycle
forever. Breaker pauses after N consecutive failures.

## Options

| Setting | Meaning |
|---|---|
| circuit_breaker_enabled | False (off) = today's behavior |
| circuit_breaker_threshold | consecutive AI-decision failures before tripping (default 5) |
| circuit_breaker_cooldown_min | >0: auto-resume attempts after cooldown; 0: stay paused until dashboard Resume |

## Runtime behavior

Worker maintains `self._ai_fail_streak` (reset in run() init).

Failure increment points (each already exists as except/skip paths):
1. engine.decide() raised (any mode/fallback exhausted).
2. decide() returned but comment empty AND treated as low-confidence
   failure? NO — low confidence is a QUALITY skip, not an availability
   failure; do NOT count it. Only exceptions count.

Trip sequence when streak == threshold:
```
phase = "ai_paused"
detail = f"AI failed {n}x consecutively — cooling down"
if cooldown_min > 0:
    sleep(cooldown window, interruptible)      # then streak=0, continue
else:
    wait until resume requested OR stop        # manual gate
streak resets on ANY successful decision
```

New PHASES entry: `"ai_paused": "AI engine failing — paused for cooldown"`.

Snapshot additions: `ai_fail_streak`, `ai_breaker_tripped: bool`.

## Implementation notes

```python
# inside gemini exception handler
except Exception as exc:
    if self._stop.is_set():
        raise _WorkerStopped()
    self._ai_fail_streak += 1
    self._set_snapshot(ai_fail_streak=self._ai_fail_streak)
    await self._maybe_trip_breaker(settings)
    ...existing skip/scroll flow...
else:
    self._ai_fail_streak = 0
    self._set_snapshot(ai_breaker_tripped=False)

async def _maybe_trip_breaker(self, settings):
    s = settings
    if not s.circuit_breaker_enabled:
        return
    if self._ai_fail_streak < max(2, s.circuit_breaker_threshold):
        return
    self._set_snapshot(ai_breaker_tripped=True)
    await self._set_phase("ai_paused")
    cd = max(0.0, float(s.circuit_breaker_cooldown_min)) * 60
    if cd > 0:
        await self._sleep(cd)          # interruptible + pause-aware
        self._ai_fail_streak = 0
        await self._set_phase("running")
    else:
        while not self._pause_gate_wait_resume():   # helper below
            if self._stop.is_set():
                raise _WorkerStopped()
            await asyncio.sleep(0.5)
        self._ai_fail_streak = 0
        await self._set_phase("running")

def _pause_gate_wait_resume(self) -> bool:
    """True once a NEW resume request arrived (edge-triggered)."""
    if not hasattr(self, "_resume_seen"):
        self._resume_seen = True
    now_set = self._pause_gate.is_set()
    edge = now_set and not getattr(self, "_was_set", True)
    self._was_set = now_set
    return edge
```

Simpler alternative acceptable: reuse pause semantics — trip breaker by
calling `self.request_pause("ai_breaker")`; Resume button naturally
clears; streak reset inside resume path when reason==ai_breaker.
PREFER THIS — fewer primitives:

```python
async def _trip_breaker(self, settings):
    self._set_snapshot(
        ai_breaker_tripped=True,
        last_error=f"AI failed {self._ai_fail_streak}x — paused")
    await self._set_phase("ai_paused",
        detail="Paused by circuit breaker — press Resume after fixing AI")
    self.request_pause("ai_breaker")     # existing gate machinery
    await self._checkpoint()             # blocks until user resumes/stops
    self._ai_fail_streak = 0
    self._set_snapshot(ai_breaker_tripped=False)
    await self._set_phase("running")
```

Cooldown variant wraps the same with timer before request_pause auto-
resume via background task — implement cooldown as: sleep(cd) THEN
request_resume() from worker itself (safe; gate is its own).

Runner.status passthrough of ai_breaker_tripped for panel badge.

## Edge cases

- Threshold reached during final post before daily cap -> cap check at
  loop top wins next iteration; breaker state harmless.
- User fixes key and presses Resume -> next decide() success resets.
- Cooldown auto-resume with still-broken AI -> threshold re-trips after
  another N failures (bounded retry rhythm, no tight loop because each
  failure path includes its normal 2–4 s backoff).

## Acceptance criteria

- [ ] Forced invalid key, threshold 3: run pauses after exactly 3 failed
      posts; phase ai_paused visible; Resume works.
- [ ] Off-switch reproduces legacy endless-skip behavior.
- [ ] Success resets streak mid-run (2 fails, 1 success, 3 more fails ->
      trips only on 3rd of second group... i.e., streak semantics).

---
---

# PHASE-13 — FEED ROTATION: HOME + GROUPS, QUOTAS, 4 MODES

- Status: `[x] DONE (2026-08-23)`
- Priority: 13 (largest feature)
- Depends on: PHASE-06, PHASE-08, PHASE-09
- Files touched: NEW `rotation.py`, `fb.py` (goto helper),
  `worker.py` (source loop wrapper), config (validators done in P01)

## Purpose

D7/D8/D9. Operate across the home feed AND a saved list of group feeds
with owner-controlled order and per-source session budgets measured in
posts or minutes.

## Source model

A "source" is one navigable feed:
```
{"type": "home",  "url": settings.feed_url}
{"type": "group", "url": <group url from feed_group_urls>}
```

## The four rotation modes

| feed_rotation_mode | Source ordering |
|---|---|
| `ordered` **(default)** | home first, then groups in saved-list order; cycles |
| `random` | pick uniformly among sources for EACH switch |
| `home_first` | spend `feed_home_first_count` engaged posts on home, then rotate groups only; if groups empty stay home forever |
| `manual_queue` | follow `feed_manual_queue` exactly ("home" or group URLs); queue exhausted -> wrap to start |

Per-source budget (`source_quota_unit`):
- `posts`: leave source after N ENGAGED posts on it (skips don't count).
- `minutes`: leave after N minutes wall-clock ON that source (measured
  from navigation completion).

Plus `source_switch_cooldown_sec` settle pause after each navigation.

## Runtime behavior

Worker main loop gains an outer source context:

```
rot = RotationController(settings_store)     # reads live each call
while not stop:
    settings = refresh()
    src = rot.current()                       # may trigger switch
    if rot.needs_navigation(src):
        phase="switching_source"
        await fb.goto_feed(src.url)           # new fb helper
        await fb.wait_feed_ready()
        await sleep(cooldown)
        rot.mark_arrived(src)
    ... existing per-post pipeline (capture..engage) ...
    rot.record_engagement()                   # on posts_engaged+1
    # loop top: rot evaluates quota -> flips current -> nav flag set
```

Rotation decisions are PURE (no IO) so they're unit-testable: given
(mode, counters, clock) -> source index or None(=stay).

## Implementation notes — new `rotation.py` sketch

```python
@dataclass
class SourceState:
    kind: str          # home|group
    url: str
    engaged: int = 0
    since: float = 0.0 # arrival time

class RotationController:
    def __init__(self, store):
        self.store = store
        self.state: dict[str, SourceState] = {}
        self.order_cursor = 0

    def _sources(self, s) -> list[SourceState]:
        out = [SourceState("home", s.feed_url)]
        out += [SourceState("group", u) for u in s.feed_group_urls]
        return out

    def current(self) -> Optional[SourceState]:
        s = self.store.get()
        srcs = self._sources(s)
        if not srcs: return None
        key = self._identity(self._pick(s, srcs))
        st = self.state.get(key)
        if st is None:
            st = srcs[self._index_of(srcs, key)]
            self.state[key] = st
            self._nav_pending = True
        return st

    def needs_navigation(self, cur) -> bool:
        return getattr(self, "_nav_pending", True)

    def mark_arrived(self, cur):
        cur.since = time.time(); cur.engaged = 0
        self._nav_pending = False

    def record_engagement(self):
        cur = self._active()
        if cur: cur.engaged += 1

    def quota_exhausted(self, s) -> bool:
        cur = self._active()
        if not cur: return False
        if s.source_quota_unit == "posts":
            return cur.engaged >= max(1, s.source_quota_value)
        return (time.time() - cur.since) >= max(60,
                    int(s.source_quota_value * 60))

    def _pick(self, s, srcs) -> SourceState:
        mode = s.feed_rotation_mode
        live = [x for x in srcs if x.kind != "group" or x.url]
        if mode == "random":
            return random.choice(live)
        if mode == "home_first":
            home_used = sum(st.engaged for k, st in self.state.items()
                            if k.startswith("home"))
            if home_used < max(1, s.feed_home_first_count) \
                    and any(x.kind == "home" for x in live):
                return next(x for x in live if x.kind == "home")
            groups = [x for x in live if x.kind == "group"]
            if not groups:
                return next(x for x in live if x.kind == "home")
            return groups[self.order_cursor % len(groups)]
        if mode == "manual_queue" and s.feed_manual_queue:
            entry = s.feed_manual_queue[self.order_cursor %
                                        len(s.feed_manual_queue)]
            url = s.feed_url if entry == "home" else entry
            return next((x for x in live if x.url == url), live[0])
        # ordered
        return live[self.order_cursor % len(live)]

    def advance(self):      # called when quota_exhausted confirmed
        self.order_cursor += 1
        self._nav_pending = True
```

Worker integration points:

```python
# loop top, before schedule gates:
if rot.quota_exhausted(settings):
    rot.advance()
src = rot.current()
if rot.needs_navigation(src):
    await self._set_phase("switching_source",
        detail=f"Moving to {src.kind}: {src.url}")
    await fb.goto_feed(src.url)
    await self._sleep(max(2.0, settings.source_switch_cooldown_sec))
    rot.mark_arrived(src)
...
# right where posts_engaged increments:
rot.record_engagement()
```

fb helper:

```python
async def goto_feed(self, url: str) -> None:
    assert self.page is not None
    await self.bring_to_front()
    self._active_post = None
    await self.page.goto(url, wait_until="domcontentloaded",
                         timeout=90_000)
    await self._wait_feed_ready()
```

Phase string addition: `"switching_source": "Switching feed source"`.

Group URL validation (P01 validator) already restricts to
facebook.com/groups/... patterns; additionally strip trailing slashes +
query at intake.

Checkpoint scan should ALSO run immediately after each navigation
(groups are common checkpoint triggers) — insert
`if await fb.checkpoint_detected(): await self._handle_checkpoint(...)`
right after mark_arrived.

## Edge cases

- Empty group list + any mode -> behaves exactly like today (single
  home source; quota still rotates cursor but same source -> no nav).
- Duplicate group URLs -> de-dupe at validator level preserving order.
- Owner edits list mid-run -> `_sources()` rebuilt per decision; state
  dict keyed by url survives; removed urls simply orphan their state.
- Group requires joining / shows "You must log in" wall ->
  `_wait_feed_ready` times into generic capture failures; P08 policy
  advances; runbook instructs pre-joining groups.
- minutes-quota + long pause (checkpoint) counts toward the source's
  window — accepted (wall-clock semantics, documented).
- manual_queue containing ONLY unknown entries -> falls back ordered
  (guard in _pick via `next(..., live[0])` pattern).

## Acceptance criteria

- [ ] ordered with 2 groups + posts-quota 3: engagement alternates
      home->g1->g2 cycling every 3 engaged posts, navigations logged.
- [ ] minutes-quota 5 switches within ~5:00 of arrival regardless of
      engagement count.
- [ ] random never navigates to a source twice consecutively unless it
      is the only source.
- [ ] manual_queue ["home","gB"] strictly alternates.
- [ ] Mid-run list edit respected on next switch without restart.
- [ ] Checkpoint injected right after group load pauses correctly.

---
---

# PHASE-14 — RUN-END POLICY (CLOSE vs STAY-OPEN BROWSER)

- Status: `[x] DONE (2026-08-23)`
- Priority: 14
- Depends on: PHASE-04
- Files touched: `worker.py` (finally block), runner status strings,
  new phase

## Purpose

D31. After caps/time/max-posts end a run, owner may want Chrome left
open on the feed (to keep the session warm / inspect manually) instead
of auto-closing. Errors and explicit Stops ALWAYS close.

## Options

| run_end_policy | On NATURAL end (cap/time/max-posts reached) | On error / user Stop |
|---|---|---|
| `close_browser` **(default)** | close context+playwright (today) | close (today) |
| `stay_open` | skip browser teardown; enter detached phase; worker thread parks | close regardless of policy |

## Runtime behavior — stay_open natural end

1. finally-block branches:

```python
finally:
    await self._set_phase("stopping")
    if gemini is not None:
        try: await gemini.close()
        except Exception: pass          # Gemini tab always closes
    natural_end = stop_reason in {
        "daily_cap_reached", "max_posts_reached",
        "time_limit_reached", "hourly cap wait ended"}
    if settings.run_end_policy == "stay_open" and natural_end:
        await self._park_with_browser_open(fb)
    else:
        try: await fb.close()
        except Exception: pass
    self._set_snapshot(running=False, phase=..., stop_reason=...)
```

2. `_park_with_browser_open`: worker stays ALIVE in a light loop so the
   Playwright objects aren't GC'd mid-use by the user:

```python
async def _park_with_browser_open(self, fb):
    await self._set_phase("browser_open",
        detail="Run complete — browser left open. Press Stop to close it.")
    while not self._stop.is_set():
        if fb.page_closed():            # tiny helper polling is_closed
            break                       # user closed window themselves
        await asyncio.sleep(1.0)
    try: await fb.close()
    except Exception: pass
```

3. Runner.stop() already sets stop-event -> parked thread exits within
   ~1 s and closes; status shows "stopped".
4. New PHASES entry: `"browser_open": "Finished — browser open"`.

Snapshot flag: `browser_left_open: bool` for panel badge.

## Implementation notes

- `fb.page_closed()` helper: `return bool(self.page and
  self.page.is_closed())` wrapped in try/except -> True.
- start() after a stay-open previous run: previous worker must be fully
  finished BEFORE runner allows new start — runner's existing
  `is_running()` check covers it because snapshot.running only flips
  False AFTER park loop ends (keep running=True during parking! adjust:
  set running False but add phase browser_open + separate
  `active_thread` guard). DECIDED: introduce
  `self._thread_alive` check via task done-callback; simplest correct
  rule: `running=False` immediately (panel shows Finished), but
  `Module1Runner.start()` refuses until prior `_task.done()` OR force-
  adopts by cancelling prior task then closing its browser first.
  Implement adoption path: if task not done and phase==browser_open ->
  call old_worker.request_stop(), wait ≤5 s, proceed.
- Crash-during-park safety: outer runner _job wrapper catches; browser
  may leak until process exit — acceptable, documented.

## Edge cases

- stay_open + scheduler interval mode: next scheduled start uses the
  adoption path above; session reuse actually BENEFITS login state.
- User navigates the parked browser to somewhere random then presses
  Stop -> close proceeds regardless of current URL.
- Memory: parked Playwright keeps websocket alive; fine for hours.

## Acceptance criteria

- [ ] Natural cap-end with stay_open: dashboard "Finished", Chrome still
      showing feed; Stop closes it; new Start works without app restart.
- [ ] Same scenario close_browser: window gone within ~3 s.
- [ ] Error path always closes even under stay_open.

---
---

# PHASE-15 — SCHEDULER (MANUAL / DAILY / INTERVAL)

- Status: `[x] DONE (2026-08-23)`
- Priority: 15
- Depends on: PHASE-14
- Files touched: `runner.py` (scheduler task + methods), config,
  snapshot fields, dashboard controls

## Purpose

D32. Runs start themselves according to a mode: manual-only (today),
daily at fixed local time, or repeating cycles of run-X-hours then
sleep-Y-hours.

## Options

| schedule_mode | Behavior |
|---|---|
| `manual` **(default)** | no automation; Start/Stop buttons only |
| `daily_at` | every day at `daily_start_time` (HH:MM local) auto-start if not already running; run shape governed by run_length_mode |
| `interval` | continuous alternation: start -> after `interval_run_hours` request stop -> sleep `interval_sleep_hours` -> repeat |

## Runtime behavior

Runner gains a background asyncio task started lazily on first runner
use and on settings change:

```python
class Module1Runner:
    def __init__(...):
        ...
        self._sched_task = None
        self._sched_wakeup: asyncio.Event | None = None

    async def _scheduler_loop(self):
        while True:
            s = self.store.get()
            if s.schedule_mode == "manual":
                await asyncio.sleep(30); continue
            if s.schedule_mode == "daily_at":
                delay = self._seconds_until(s.daily_start_time)
                await self._sleep_or_reconfig(delay)
                if not self._is_active():
                    await self.start()
                    # let the run govern its own end via P04 modes
                await asyncio.sleep(60)   # avoid double-fire minute
            else:  # interval
                if not self._is_active():
                    await self.start()
                    await self._sleep_or_reconfig(
                        s.interval_run_hours * 3600)
                    if self._worker_running():
                        await self.stop()
                await self._sleep_or_reconfig(
                    s.interval_sleep_hours * 3600)
```

Reconfig responsiveness: `_sleep_or_reconfig(sec)` sleeps in 10 s slices
checking a generation counter bumped by update_config(); any schedule-
related setting change aborts current slice so new params apply within
~10 s. Prevents stale 4-hour sleeps after owner edits.

Guard rails:
- daily_at fires ONLY when previous day's fire didn't already happen
  (track `last_fire_date` in memory; reset on app restart is fine —
  worst case one extra start after restart same-day IF time window
  re-entered; acceptable + documented).
- interval stop uses graceful worker.request_stop() path (P14 adoption).
- Scheduler NEVER stops a manual run mid-way except interval's natural
  run-window expiry; document that manual Stop always available.

Snapshot additions: `schedule_mode`, `next_scheduled_action` ISO string
(computed best-effort), `last_auto_start`.

## Implementation notes

```python
def _seconds_until(self, hhmm: str) -> float:
    try:
        h, m = map(int, hhmm.split(":"))
    except Exception:
        return 3600.0        # invalid -> retry in an hour silently
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()
```

update_config() hook:

```python
SCHED_KEYS = {"schedule_mode","daily_start_time",
              "interval_run_hours","interval_sleep_hours"}
async def update_config(self, patch):
    result = self.store.update(patch)
    if SCHED_KEYS & set(patch or {}):
        await self._kick_scheduler()   # ensure task running + bump gen
    return result
```

Lazy task bootstrap inside status()/start()/config() first call:

```python
if self._sched_task is None or self._sched_task.done():
    self._sched_task = asyncio.create_task(self._scheduler_loop())
```

uvicorn-lifespan note: task created on the FastAPI loop; runner already
runs in that loop for control endpoints — safe. Worker thread remains
the separate Proactor thread (unchanged architecture).

Interval + stay_open interplay handled purely by P14 adoption logic.
Interval + circuit breaker paused state: scheduler sees worker running
(paused) -> treats as active; cooldown/manual resume governs — correct.

## Edge cases

- PC asleep through scheduled time -> fires immediately on wake if time
  passed within grace 0? DECIDED: skip missed fires (delay recomputes to
  next day); log "missed scheduled start".
- DST transitions: computed from naive local datetime each cycle ->
  self-corrects; ±1h edge documented as accepted.
- Owner sets interval_run=0 -> validator clamps ≥0.25h (never hot loop).

## Acceptance criteria

- [ ] daily_at set 2 min ahead: auto-starts on time; snapshot shows
      last_auto_start; second fire next day only once.
- [ ] interval 0.25h run / 0.25h sleep cycles visibly without overlap;
      changing sleep mid-sleep applies ≤10 s later.
- [ ] manual mode never auto-starts anything.

---
---

# PHASE-16 — DASHBOARD SETTINGS PANEL COMPLETION

- Status: `[ ] TODO`
- Priority: 16
- Depends on: PHASE-01..PHASE-15 (renders everything they added)
- Files touched: dashboard frontend (module-1 settings view),
  backend already generic via store.dump()/update()

## Purpose

D35. Every knob above must be visible, labeled, validated client-side,
and live-applied. Backend contract already exists (PATCH whitelist by
model keys); this phase is UI + hints.

## Panel layout spec (sections in order)

1. **AI Engine**
   - ai_mode select [5 options] with per-option help tooltip
     (race_both warns: "uses double quota")
   - gemini_api_key password field (write-only; shows `configured` dot)
   - gemini_api_model text
   - "Test AI" button -> POST test-ai -> inline latency/source result
   - circuit breaker group (enabled toggle, threshold number,
     cooldown minutes) with explainer
2. **Feeds & Rotation**
   - rotation mode select [4]
   - group URLs list editor (add/remove/reorder rows)
   - manual queue builder when mode=manual_queue (chips from home+groups)
   - home_first_count (visible only mode=home_first)
   - quota unit select + value number + switch cooldown seconds
3. **Scroll & Reading**
   - scroll_profile select [lazy/slow/fast/custom(auto)]
   - explicit delta min/max inputs (disabled unless custom state)
   - reading dwell min/max sliders (0–60 s)
4. **Post handling**
   - video_policy select [3]
   - capture_failure_policy select + retry limit
   - seen expiry days + "Clear seen list" button + count badge
5. **Comments**
   - comment_style select incl. engagement
   - language select [match_post/urdu/english/custom] + custom textbox
   - emoji toggle, hashtags toggle, max words number
   - typing_style select, typo slider %, key delay min/max ms
   - min_confidence slider %
6. **Pacing & Caps**
   - gap min/max seconds
   - long_break_every/min/max (+ off state)
   - hourly cap number + hint "~20/hr recommended"
   - daily cap number + hint "0 = unlimited"
   - action_count_mode radio [per_post recommended / per_action]
7. **Safety**
   - checkpoint_policy radio + wait minutes (disabled if indefinite)
   - work hours start/end (empty = always) — D34 default empty
8. **Run shape**
   - run_length_mode radio + N posts / X hours conditional fields
   - run_end_policy radio
9. **Schedule**
   - schedule_mode radio; daily time picker; interval h/h numbers
10. **Live status strip** (existing): phase, counters, streak,
    breaker badge, source badge (last_ai_source), uptime.

## Implementation notes

- Single PATCH on Save (batch), optimistic UI, warnings list rendered
  verbatim from backend response.
- Field metadata source of truth: add `FIELD_HELP: dict[str,str]` to
  config.py so tooltips ship with schema (single-file documentation):
```python
FIELD_HELP = {
  "ai_mode": "API replies instantly; Web is slower but keyless.",
  ...
}
```
- Masking respected: key input never prefilled; placeholder
  "paste new key to replace" when configured.
- All selects map EXACTLY to validator enums — pull options list from a
  new GET /module1/config/meta endpoint returning the enum constants +
  FIELD_HELP so UI never hardcodes drift-prone lists.

## Acceptance criteria

- [ ] Every P01 setting reachable and editable; save round-trips.
- [ ] Invalid input blocked client-side AND warned server-side.
- [ ] Hints render from meta endpoint (no hardcoded option lists).
- [ ] Key never appears in any GET payload or screenshot-visible text.

---
---

# PHASE-17 — END-TO-END VERIFICATION (RUNBOOK EXECUTION)

- Status: `[ ] TODO`
- Priority: 17 (final gate)
- Depends on: all above
- Files touched: none (verification only); produces RUNBOOK.md signoff

## Scripted verification session (~90 minutes, owner present)

1. Fresh settings file -> boot -> defaults audit vs P01 table (10 min).
2. Login-wait indefinite check both providers (P02).
3. api_only 5-post smoke with Test-AI first (P03).
4. Fallback chain drill: corrupt key mid-run -> web takeover (P03).
5. time_boxed 10-min run end + stay_open park + Stop-close (P04/P14).
6. Caps drill: hourly=3, per_post -> exactly 3 posts then cap_paused;
   flip per_action regression spot-check (P05).
7. Scroll preset sweep lazy->slow->fast observing px logs (P06).
8. Feed with video: like_only behavior + skip + engage once each (P07).
9. Dev-forced capture failure x2 under retry_limited recovery (P08).
10. Seen expiry: backdated entry purge proof (P09).
11. Language/tone/emoji/hashtag matrix on 6 posts (P10).
12. Injected checkpoint: indefinite hold + timed stop variants (P11).
13. Breaker trip with dead key threshold=3 + resume after fix (P12).
14. ordered rotation across 2 real groups, quota 3 posts each (P13).
15. daily_at fire + interval cycle short-window test (P15).
16. Full panel edit pass incl. masked key (P16).
17. Owner signs RUNBOOK.md acceptance block; roadmap marked complete.

## Exit checklist (all must be green)

- [ ] Zero console tracebacks across entire session except injected ones.
- [ ] Every phase transition observed matched PHASES dictionary strings.
- [ ] No raw API key in logs/network panel/UI at any point.
- [ ] Account untouched by FB warnings during session.

---
---

# APPENDIX A — SNAPSHOT FIELD REGISTRY (dashboard contract additions)

| Field | Phase | Type | Notes |
|---|---|---|---|
| last_ai_source | P03 | str | gemini_api/gemini_web |
| attempts | P03 | list[str] | engines tried for last decision |
| run_time_used_sec / run_time_limit_sec | P04 | int | progress bar |
| ai_fail_streak | P12 | int | |
| ai_breaker_tripped | P12 | bool | panel badge |
| browser_left_open | P14 | bool | Finished-state badge |
| schedule_mode / next_scheduled_action / last_auto_start | P15 | mixed | scheduler card |
| checkpoint_since | P11 | float | elapsed timer |

# APPENDIX B — NEW/CHANGED PHASES STRINGS (worker.PHASES)

```python
"switching_source": "Switching feed source",          # P13
"skipping_video":   "Video/reel handled by policy",   # P07 (detail)
"ai_paused":        "AI engine failing — paused",     # P12
"browser_open":     "Finished — browser left open",   # P14
```

# APPENDIX C — OUT OF SCOPE / DEFERRED (do NOT implement silently)

- Multi-account switching inside one profile.
- Headless-safe login automation (credentials storage = security risk).
- Non-Gemini model providers (facade in P03 makes this easy LATER).
- i18n reel aria-label detection beyond English heuristics.
- Cross-process settings locking (single-dashboard assumption).

# CHANGELOG (append one line per completed phase)

<!-- format: YYYY-MM-DD | PHASE-XX | one-line summary | initials -->
- 2026-08-23 | PHASE-01 | Schema v2 shipped: ~40 new settings across 6 groups (AI engine, rotation, policies, quality, breaker, run-shape), enum/numeric coercion validators, `_sanitize_raw` per-key corruption pruning, `gemini_api_key` masked in dump()/compact() with `set_secret()` writer, list-value patches enabled, "engagement" comment style registered. Tests: tests/test_module1_settings_v2.py (24) — repo suite 58/58 green, zero legacy regressions. Custom-prompt and scroll_profile fields caught missing during self-review before ship. | agent
- 2026-08-23 | PHASE-02 | Indefinite login waits shipped: fb.py `_wait_login_if_needed` waits forever for manual sign-in (cancel_event aborts ~1.5s, FB_LOGIN_WAIT_MS>0 = CI timeout), headless warning added; gemini_bridge login wait reinterpreted identically (GEMINI_LOGIN_WAIT_MS unset/0=forever) and now emits gemini_login_wait/gemini_ok phase signals; worker passes stop-event to FacebookAutomation, restores pre-login phase on gemini_ok with terminal-phase guard; bot.py legacy parity. Tests: tests/test_module1_login_wait.py (11, faked pages) — repo suite 69/69 green. Self-review catches: worker restored "running" when gemini_ok arrived without preceding wait (fixed); test fake wrongly made sync Playwright is_closed() async (fixed). | agent
- 2026-08-23 | PHASE-03 | AI Engine selector shipped: NEW ai_engine.py facade with all five modes (api_only / web_only / api_primary_web_fallback DEFAULT / web_primary_api_fallback / race_both), lazy Web-bridge construction, first-SUCCESS-wins race with loser cancellation, aggregated all-fail errors; gemini_api.generate_engagement_v2 parameterized key/model/prompt via shared parse_gemini_reply; worker swapped to engine.decide() with live-settings bridge factory and last_ai_source/last_ai_attempts telemetry; runner.test_ai_engine + POST /api/v1/module1/test-ai verifies the dashboard key against a 1x1 PNG without a browser. Tests: tests/test_module1_ai_engine.py (13, faked HTTP/bridges) — repo suite 82/82 green. Self-review catches: test harness awaited a sync stub, helper pre-seeded bridges defeating laziness assertions, aclose tested before open — all fixed in tests; production code needed zero changes. | agent
- 2026-08-23 | PHASE-04 | Run-length modes shipped: unified `_run_length_stop()` gate (pure/testable) replacing the inline max-posts check — unlimited / max_posts (N=0 = unlimited footgun guard) / time_boxed (wall-clock vs run_time_limit_min, pauses count); snapshot() computes run_time_used_sec + run_time_limit_sec live for the panel progress bar; runner idle-status relays both fields. DEVIATION NOTE: roadmap said unlimited = no check, but a legacy-compat shim honors max_posts_per_run>0 under unlimited mode so pre-v2 configs keep their stop behavior (one-shot console notice explains). Cap gates keep precedence over run-length stops. Tests: tests/test_module1_run_length.py (11) — repo suite 93/93 green. | agent
- 2026-08-23 | PHASE-05 | Per-post cap accounting shipped: engage block now tracks did_react/did_comment and delegates to new `_record_engagement_actions()` — per_post (default) consumes exactly ONE cap unit per engaged post, per_action preserves legacy double-counting; fully-failed posts still count nothing and remain unmarked in seen-store. Seen-marking stays tied to successful comment only (unchanged). Tests: tests/test_module1_action_count.py (11 incl. hourly-wait trip after exactly 3 per_post engagements and mid-run mode flip mixed accounting) — repo suite 104/104 green. | agent
- 2026-08-23 | PHASE-06 | Scroll personalities shipped: SCROLL_PRESETS lazy(420,780,x1.0)/slow(200,400,x1.5)/fast(800,1400,x0.7); `effective_scroll()` returns persisted pixels + profile multiplier; update() write-through snaps pixels on profile SELECTION (refinement over sketch: sync requires scroll_profile IN the patch, so unrelated edits never silently reset hand-tuned custom deltas — explicit deltas always win verbatim, custom state = named profile with edited numbers); fb.py applies range+multiplier in both scroll_to_next_post and center-scroll; worker rebinds fb.settings each cycle for live persona switches. Tests: tests/test_module1_scroll_profiles.py (12 incl. real wheel-range + pause-multiplier recording through faked page) — repo suite 116/116 green. Self-review catches: fake page again needed SYNC is_closed; settle-sleep filter overlapped chunk ceiling (positional exclusion fixed both). | agent
- 2026-08-23 | PHASE-07 | Video/reel policy shipped: fb.classify_post (href hints -> <video> -> aria 'reel' -> text, exception-safe) wired into capture_post result as `kind`; worker gate runs AFTER dedup per spec ordering and delegates to `_handle_video_policy` — skip (delete+scroll, no seen-marking), like_only default (short tap-and-go dwell 1-2.5s, LIKE click via P05 counting, seen-marking, last_reaction=LIKE, post-action checkpoint scan, long-break counter, pacing gap), engage fall-through untouched; contradiction like_only+reaction_mode=none resolves to skip (stronger rule wins); failed-Like edge = skip without seen-marking. PHASES gained skipping_video. Tests: tests/test_module1_video_policy.py (10) — repo suite 126/126 green. Self-review catches: FakeSeen missing count(); temp-dir scoping put an exists() assert after cleanup — test-side only, production unchanged. | agent
- 2026-08-23 | PHASE-08 | Capture failure policy shipped: pure `_capture_failure_action()` helper returns retry/advance + new streak; worker gate on failed capture — instant_skip (default) advances immediately as before; retry_limited micro-scrolls slow_scroll(240-360px), waits 1.5-3s pause-aware, re-captures same region up to capture_retry_limit consecutive failures (strict `<` so owner lowering limit mid-streak advances next failure), phase detail shows live "retry N/limit", success resets streak, loop-top checkpoint keeps Stop/Pause responsive mid-streak, no infinite spin possible. Tests: tests/test_module1_capture_failures.py (9 incl. flaky-recover + persistent-junk simulations) — repo suite 135/135 green. | agent
- 2026-08-23 | PHASE-09 | Seen-store expiry shipped: SeenStore gains expiry_days param (0=legacy never-expire default keeps singleton safe), `_expired()` strict-greater window honoring, public `purge()` called by worker at run start with purged-count log, lazy drop inside contains() for never-restarted processes with disk rewrite, future timestamps always fresh, legacy list-format files still load; worker passes seen_expiry_days live. DOCUMENTED LEGACY QUIRK pinned by test: count-trim is a soft bound settling at max_entries+1 (pre-existing cutoff math, add() untouched per spec). Tests: tests/test_module1_seen_expiry.py (8) — repo suite 143/143 green. Self-review catches: package __init__ shadows seen_store submodule with singleton instance (string-target mock.patch resolves real module); injected clock must anchor to NOW or expiry layer eats entries post-restore; rapid time.time() collisions made trim flaky (deterministic spaced clock). | agent
- 2026-08-23 | PHASE-10 | Comment quality suite shipped: prompt() now assembles base(custom|style) + language clause (match_post w/ image-only fallback / urdu directive / english / custom verbatim) + emoji clause + conditional hashtag ban + word ceiling + structured suffix; shared helpers maybe_strip_tags/enforce_comment_quality (emoji regex, word clamp w/ punctuation cleanup, 280-char platform cap) live in config.py as single choke point; strip-gating threaded end-to-end — parse_gemini_reply(strip_tags kw), bridge.strip_tags attr through generate_decision + _generate_comment_once, gemini_api v2 kwarg, engine forwards not allow_hashtags to both transports, worker applies quality enforcement post-decision, fb.comment entry gate defense-in-depth; legacy style prompts stripped of hardcoded "No emojis./No hashtags." so dynamic clauses are single source of truth. CRITICAL BUG CAUGHT BY TESTS BEFORE SHIP: semantic inversion — helper contract (allowed=True keeps) vs parser flag (True strips) had every call site preserving tags it meant to remove and vice versa; fixed via allow=not strip_tags conversions. Also fixed test-side: char-cap applies even at max_words=0; engine attrs only assigned during decide(). Tests: tests/test_module1_comment_quality.py (26) — repo suite 169/169 green. | agent
- 2026-08-23 | PHASE-11 | Checkpoint policy selector shipped: pure `_checkpoint_deadline()` (None unless policy==wait_then_stop with budget>0 — zero-budget footgun guard), `_handle_checkpoint` parameterized per D29 — wait_indefinitely (default) polls forever until human clears or Stop; wait_then_stop auto-stops at budget expiry with exact panel message "Checkpoint unresolved — auto-stopped" + stop_reason checkpoint_unresolved; snapshot `checkpoint_since` set on entry / cleared on every exit path (finally) so the dashboard renders a growing elapsed timer; timed detail refreshes remaining "auto-stop in ~X min YYs" every 3s poll. LATENT BUG FIXED: old code set last_error then raised _WorkerStopped but the finally-block's stopping-phase transition wiped it before the panel ever read it — new `_terminal_note` sticky field carries the message into the final stopped snapshot (normal runs unchanged). Pause-gate interplay preserved (user pause inside hold blocks until Resume, wall-clock keeps ticking per P04 convention). Tests: tests/test_module1_checkpoint_policy.py (13, deterministic ticking-clock — no wall-time dependence) — repo suite 182/182 green. | agent
- 2026-08-23 | PHASE-12 | Gemini circuit breaker shipped: pure `_breaker_should_trip` (disabled→never; streak ≥ max(2, threshold), floor 2 so a single flaky failure never pauses); worker AI-decision except-path increments `_ai_fail_streak` (availability failures ONLY — low-confidence quality skips explicitly not counted per spec) then `_maybe_trip_breaker`; success path resets streak via try/else and clears ai_breaker_tripped. Roadmap-PREFERRED pause-reuse design: `_trip_breaker` does request_pause("ai_breaker") + blocking `_checkpoint()` — cooldown>0 runs an interruptible wall-clock window that ends early on user Resume, else releases the gate itself; cooldown==0 holds for dashboard Resume; Stop aborts from anywhere. Snapshot gains ai_fail_streak/ai_breaker_tripped (+ runner fallback relay); PHASES gained "ai_paused"; snapshot.paused includes ai_paused so panel gating reads correctly. BUG CAUGHT BY TEST: _set_phase wiped last_error on entering ai_paused, blanking the "AI failed Nx consecutively — paused" reason — added to preserve-set alongside error/stopped. Bounded retry rhythm preserved: post-trip flow still runs normal 2–4s backoff before next attempt; still-broken AI re-trips after another N failures. Tests: tests/test_module1_circuit_breaker.py (16 incl. acceptance streak scenario 2F-1S-3F trips only on 3rd of second group, ticking clock, no wall-time dependence) — repo suite 198/198 green. | agent
- 2026-08-23 | PHASE-13 | Feed rotation shipped: NEW rotation.py with SourceState(kind,url,key,engaged,since) + RotationController — candidates rebuilt from LIVE store every call (mid-run group-list/feed_url edits apply next switch), identity "home"/group-URL so edited home URL keeps history but flags re-navigation; four modes: ordered cursor cycle, random excluding last-navigated key (acceptance: never two consecutive navigations to one source unless sole source), home_first spends feed_home_first_count engaged posts then cycles groups (empty groups → home forever), manual_queue exact order w/ wraparound (unmatched entries degrade to ordered cycling). Quotas per D7: posts = ENGAGED only (skips/videos-liked count via record_engagement wired at both posts_engaged sites incl. like_only path); minutes = wall-clock since arrival, budget max(60, value*60)s. BUG CAUGHT BY TEST: precedence slip made minutes budget 60x too long (max(60,v)*60 vs max(60,v*60)). current() suppresses nav when cursor re-picks the occupied source (spec edge: single-home runs 'no nav') yet flags nav when live URL changed; worker seeds arrival for home after fb.start() so runs never double-navigate. fb.goto_feed NEVER raises (bad URL degrades into P08 capture policy) with 20s ready-wait; post-navigation checkpoint scan added (groups trigger checks). DEVIATION: rotation block sits AFTER safety gates (caps/work-hours/run-length keep precedence — no navigation while capped) vs sketch's 'before schedule gates'. Validator intake refined: query/fragment+trailing slash stripped BEFORE dedupe (P01 test expectation updated to refined contract). PHASES gained switching_source. Tests: tests/test_module1_rotation.py (26, fake-store + ticking clock, all acceptance criteria covered) — repo suite 224/224 green. | agent
- 2026-08-23 | PHASE-14 | Run-end policy shipped: pure `_should_stay_open` gates parking on NATURAL_END_REASONS {daily_cap_reached, max_posts_reached, time_limit_reached} AND live stay_open policy read fresh at teardown; errors / user Stops / checkpoint auto-stops close regardless (spec sketch listed 'hourly cap wait ended' as natural — current gate design WAITS on hourly caps producing no stop_reason, so omitted, documented). `_park_with_browser_open`: snapshot flips running=False immediately ('Finished' panel) + browser_left_open badge + phase browser_open while a light 1s poll keeps Playwright objects alive for the user — exits on dashboard Stop OR user-closed window via new sync-safe fb.page_closed() probe (None/detached fail closed), always closes cleanly on the way out, badge cleared in final stopped snapshot. Runner adoption path per roadmap DECIDED rule: start() detects phase==browser_open on a not-done prior task → request_stop + ≤5s shielded wait before spawning the replacement worker (prevents two workers fighting over one browser; session reuse benefits login state for P15 interval mode); finished runs skip adoption entirely. PHASES gained browser_open; runner fallback relays browser_left_open. Tests: tests/test_module1_run_end_policy.py (19: decision matrix incl. checkpoint_unresolved closing, park entry flags/3 exit paths/close-failure swallowed, page_closed probe contract, runner adoption positive+negative) — repo suite 243/243 green. | agent
- 2026-08-23 | PHASE-15 | Scheduler shipped: runner gains `_scheduler_loop` asyncio task with lazy `_ensure_scheduler` bootstrap from ANY entry point (status/config/start/stop/update_config) — sync-safe via get_running_loop guard so bare/threadpool calls silently defer to the next async entry. manual never starts anything; daily_at fires once per local day at HH:MM when idle (last_fire_date guard + 60s double-fire nap, empty/invalid time idles safely, already-active runs honored not double-started); interval alternates start → run-window → graceful stop → sleep-window (validator floor 0.25h = no hot loop; a mid-window MANUAL run IS expired by the window per spec's explicit exception). `_sleep_or_reconfig` slices sleeps into 10s ticks against a generation counter bumped by update_config() on SCHED_KEYS edits — owner changes apply within one slice instead of stale multi-hour naps (kept update_config SYNC to preserve endpoint contract; kick is best-effort). Missed fires skipped to next day; DST self-corrects via naive-local recompute. Snapshot surface: schedule_mode/next_scheduled_action ISO/last_auto_start relayed through compose_status with getattr-defensive defaults (bare test doubles safe). BUG CAUGHT BY TEST HANGING: post-fire guard naps used raw asyncio.sleep(60), invisible to reconfig AND hanging injected-sleeper tests — routed through _sleep_or_reconfig (better prod semantics too). P14 adoption path handles interval stops over parked browsers. Tests: tests/test_module1_scheduler.py (18: acceptance scenarios all covered, exhaustion-sentinel sleeper keeps everything instant/deterministic) — repo suite 261/261 green. | agent

