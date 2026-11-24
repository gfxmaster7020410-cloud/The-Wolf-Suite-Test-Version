"""
Module 1 — Facebook automation client (single-tab, human-like).

Owns the persistent Chromium session and every Facebook DOM interaction:
feed waiting, login idle-wait, post capture, comment (paste or human typing),
reaction, scroll, and checkpoint (security challenge) detection.

Designed for one browser / one Facebook tab so the user never leaves Facebook.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from services.module_1_feed_observer.config import M1Settings, maybe_strip_tags
from services.shared.humanization import HumanizationEngine, humanization

# --------------------------------------------------------------------------
# Selectors & checkpoint heuristics (centralized so fixes are one-line edits)
# --------------------------------------------------------------------------

_POST_SELECTOR = (
    "div[role='feed'] div[role='article'], "
    "div[role='article'], "
    "div[data-pagelet*='FeedUnit']"
)

_COMMENT_BUTTON_SELECTORS = (
    "[aria-label*='Comment' i][role='button'], "
    "[aria-label='Leave a comment'][role='button']"
)

_COMMENT_BOX_SELECTORS = (
    "[aria-label*='Write a comment' i][contenteditable='true']",
    "[aria-label*='Write a comment' i]",
    "div[role='textbox'][contenteditable='true']",
    "div[contenteditable='true'][role='textbox']",
)

_LIKE_BUTTON_SELECTORS = "[aria-label='Like'][role='button'], [aria-label*='Like' i][role='button']"

_POST_URL_HINT_SELECTORS = (
    "a[href*='/posts/']",
    "a[href*='/photos/']",
    "a[href*='/videos/']",
    "a[href*='/reel/']",
    "a[href*='/watch/']",
    "a[href*='story_fbid']",
    "a[href*='/permalink/']",
    "a[href*='/groups/'][href*='?']",
)

# PHASE-07 — language-independent video signals (primary detection layer)
_VIDEO_HREF_HINTS = ("/videos/", "/reel/", "/watch/")

_CHECKPOINT_PHRASES = (
    "enter the code",
    "security check",
    "confirm your identity",
    "we suspect",
    "unusual activity",
    "account is temporarily locked",
    "get your account back",
    "checkpoint",
    "review recent activity",
    "reverify your identity",
)


class FacebookAutomation:
    """Persistent Facebook browser session with humanized engagement actions."""

    def __init__(self, settings: M1Settings, on_phase=None, cancel_event=None):
        self.settings = settings
        self.human: HumanizationEngine = humanization
        self.on_phase = on_phase  # Optional async callable(str) for status updates
        # threading.Event shared with Module1Worker so dashboard Stop can
        # break us out of indefinite login waits (PHASE-02 / decision D6).
        self.cancel_event = cancel_event
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._active_post: Optional[Locator] = None
        self._temp_dir = Path(__file__).resolve().parents[2] / "temp"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> Page:
        """Launch persistent Chromium with the user's Facebook profile."""
        self.playwright = await async_playwright().start()
        user_data_dir = self.settings.user_data_dir or str(
            Path(__file__).resolve().parents[2] / "data" / "browser_profiles" / "facebook"
        )
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        headless = os.getenv("FB_HEADLESS", "false").lower() in {"1", "true", "yes"}
        if headless:
            # PHASE-02 visible-window guarantee: logins/checkpoints are
            # resolved by the human in the visible window; headless makes
            # indefinite waits unresolvable, so warn loudly.
            print(
                "[M1 FB] WARNING: running headless — manual login will block "
                "until FB_LOGIN_WAIT_MS expires (0 = forever). Set FB_HEADLESS=false for interactive runs."
            )
        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": user_data_dir,
            "headless": headless,
            "viewport": {"width": 1365, "height": 900},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
        }
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                channel="chrome", **launch_kwargs
            )
            print(f"[M1 FB] Using installed Chrome profile: {user_data_dir}")
        except Exception as exc:
            print(f"[M1 FB] Chrome channel unavailable ({exc}); using Playwright Chromium.")
            self.context = await self.playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )

        try:
            await self.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://www.facebook.com",
            )
        except Exception:
            pass

        self.page = (
            self.context.pages[0] if self.context.pages else await self.context.new_page()
        )
        await self._emit("fb_loading")
        await self.page.goto(
            self.settings.feed_url, wait_until="domcontentloaded", timeout=90_000
        )
        await self._wait_feed_ready()
        await self._wait_login_if_needed()
        return self.page

    async def close(self) -> None:
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        self.page = None
        self._active_post = None

    async def bring_to_front(self) -> None:
        if self.page is not None and not self.page.is_closed():
            await self.page.bring_to_front()

    async def goto_feed(self, url: str) -> None:
        """
        PHASE-13 — navigate to any feed source (home or group).
        NEVER raises: an unreachable/bad URL degrades into capture
        failures, which the capture-failure policy (PHASE-08) then
        advances past — golden rule, no fatal run stops from rotation.
        """
        assert self.page is not None
        await self.bring_to_front()
        self._active_post = None
        target = url or self.settings.feed_url
        try:
            await self.page.goto(target, wait_until="domcontentloaded", timeout=90_000)
        except Exception as exc:
            print(f"[M1 FB] Navigation to {target} failed: {type(exc).__name__}: {exc}")
            return
        # Shorter ready-wait than startup: groups render slower but a miss
        # just means the next capture fails and policy advances.
        await self._wait_feed_ready(timeout_ms=20_000)

    def current_post(self) -> Optional[Locator]:
        """The active post locator for the current engagement step."""
        return self._active_post

    def page_closed(self) -> bool:
        """
        PHASE-14 — sync probe for the park loop (Playwright's is_closed is
        sync). True whenever the page is gone or unusable.
        """
        try:
            return bool(self.page is None or self.page.is_closed())
        except Exception:
            return True

    # ------------------------------------------------------------------ waits
    async def _wait_feed_ready(self, timeout_ms: int = 90_000) -> None:
        assert self.page is not None
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        try:
            await self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        deadline = time.time() + timeout_ms / 1000.0
        sel = (
            "div[role='feed'], div[role='article'], "
            "[aria-label*='News Feed' i], [aria-label*='Create a post' i]"
        )
        while time.time() < deadline:
            try:
                if await self.page.locator(sel).count() > 0:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def _wait_login_if_needed(self) -> None:
        """
        PHASE-02 (decision D6): if a login page shows, wait INDEFINITELY for
        the user to sign in manually — work resumes automatically on login.
        Only the dashboard Stop button (cancel_event) aborts, unless
        FB_LOGIN_WAIT_MS is set to a positive millisecond value (headless/CI
        escape hatch; 0 or unset = wait forever).
        """
        assert self.page is not None
        if await self._has_feed():
            return
        await self._emit("fb_login_wait")
        print(
            "[M1 FB] Facebook login required — waiting indefinitely for "
            "manual sign-in in the browser window (Stop button cancels)..."
        )
        raw_wait = os.getenv("FB_LOGIN_WAIT_MS", "0")
        try:
            wait_ms = int(raw_wait)
        except ValueError:
            wait_ms = 0
        deadline = time.time() + wait_ms / 1000.0 if wait_ms > 0 else None

        while True:
            await asyncio.sleep(1.5)
            # Stop must always win, fast (~1 poll), even mid-wait.
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise RuntimeError("Cancelled during Facebook login wait")
            if deadline is not None and time.time() > deadline:
                raise RuntimeError("Facebook login not completed within wait window")
            try:
                if self.page.is_closed():
                    raise RuntimeError("Facebook page closed during login wait")
                if await self._has_feed():
                    print("[M1 FB] Login detected — continuing.")
                    await asyncio.sleep(1.0)
                    return
            except RuntimeError:
                raise
            except Exception:
                # Transient DOM/eval errors during FB's own reloads must not
                # kill an indefinite wait — keep polling.
                continue

    async def _has_feed(self) -> bool:
        if self.page is None or self.page.is_closed():
            return False
        try:
            feed = await self.page.locator(
                "div[role='feed'], [aria-label*='News Feed' i], "
                "[aria-label*='Create a post' i]"
            ).count()
            return feed > 0
        except Exception:
            return False

    # ------------------------------------------------------------------ capture
    async def capture_post(self) -> Dict[str, Any]:
        """
        Locate the first visible feed post, human-scroll it to center, and
        screenshot it. Returns {screenshot, url, ok} (ok=False → keep scrolling).
        """
        assert self.page is not None
        await self.bring_to_front()

        post = self._active_post or await self._first_visible_post()
        if post is None:
            return {"ok": False, "reason": "no_visible_post", "screenshot": None, "url": None}

        self._active_post = post
        await self._human_scroll_post_to_center(post)

        out_file = self._temp_dir / f"m1_post_{int(time.time() * 1000)}.png"
        try:
            await post.screenshot(path=str(out_file), type="png")
        except Exception as exc:
            print(f"[M1 FB] Element screenshot failed ({exc}); viewport fallback.")
            box = await post.bounding_box()
            if box:
                await self.page.screenshot(
                    path=str(out_file),
                    type="png",
                    clip={
                        "x": max(0, box["x"]),
                        "y": max(0, box["y"]),
                        "width": max(1, box["width"]),
                        "height": max(1, box["height"]),
                    },
                )
            else:
                await self.page.screenshot(path=str(out_file), type="png")

        url = await self.extract_post_url(post)
        kind = await self.classify_post(post, url)
        return {
            "ok": True,
            "screenshot": str(out_file),
            "url": url,
            "kind": kind,
        }

    async def classify_post(self, post: Locator, url: Optional[str]) -> str:
        """
        PHASE-07 — best-effort 'text' | 'video' classification.
        Ordered heuristics: permalink hints (language-independent, primary)
        -> <video> descendant -> aria-label containing 'reel' (English UIs
        only; documented caveat). Any failure classifies as 'text'.
        """
        try:
            if url and any(h in url for h in _VIDEO_HREF_HINTS):
                return "video"
            if await post.locator("video").count() > 0:
                return "video"
            if await post.locator("[aria-label*='reel' i]").count() > 0:
                return "video"
        except Exception:
            pass
        return "text"

    async def _first_visible_post(self) -> Optional[Locator]:
        assert self.page is not None
        locs = self.page.locator(_POST_SELECTOR)
        n = await locs.count()
        for i in range(min(n, 15)):
            loc = locs.nth(i)
            try:
                box = await loc.bounding_box()
                if box and box["height"] >= 100 and box["width"] >= 200:
                    return loc
            except Exception:
                continue
        return None

    async def _human_scroll_post_to_center(self, post: Locator) -> None:
        assert self.page is not None
        box = await post.bounding_box()
        if not box:
            await post.scroll_into_view_if_needed()
            await asyncio.sleep(0.4)
            return
        viewport = self.page.viewport_size or {"width": 1365, "height": 900}
        target_y = box["y"] + box["height"] / 2
        scroll_needed = target_y - (viewport["height"] / 2)
        if abs(scroll_needed) < 20:
            return
        _, _, smul = self.settings.effective_scroll()
        steps = random.randint(4, 7)
        remaining = scroll_needed
        for i in range(steps):
            if i == steps - 1:
                chunk = remaining
            else:
                chunk = remaining / (steps - i) * random.uniform(0.7, 1.3)
            await self.page.mouse.wheel(0, int(chunk))
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.12, 0.32) * smul)
        await asyncio.sleep(random.uniform(0.45, 0.9))

    async def extract_post_url(self, post: Locator) -> Optional[str]:
        """Best-effort post permalink used for duplicate detection."""
        try:
            link = post.locator(_POST_URL_HINT_SELECTORS).first
            if await link.count() == 0:
                return None
            href = await link.get_attribute("href")
            if not href:
                return None
            href = href.split("?")[0].split("#")[0].strip()
            return href if href else None
        except Exception:
            return None

    # ------------------------------------------------------------------ engagement
    async def comment(self, post: Locator, text: str, style: str = "paste") -> bool:
        """
        Post a comment on the given post. style = paste (fast, clipboard) or
        type (humanized per-character typing). Returns True when submitted.
        """
        assert self.page is not None
        # PHASE-10: entry gate honors the allow_hashtags setting (defense in
        # depth — upstream already gated, this catches manual/legacy paths).
        text = maybe_strip_tags(text, self.settings.allow_hashtags)
        text = (text or "").strip()
        if not text:
            return False

        # Open the composer if it is hidden behind the Comment button
        try:
            btn = post.locator(_COMMENT_BUTTON_SELECTORS).first
            if await btn.count() > 0:
                await self._click_human(btn)
                await asyncio.sleep(random.uniform(0.3, 0.7))
        except Exception:
            pass

        box = await self._find_comment_box(post)
        if box is None:
            print("[M1 FB] Comment box not found")
            return False

        await self._click_human(box)
        await asyncio.sleep(random.uniform(0.2, 0.45))

        try:
            if style == "type":
                await self.human.type_text_humanized(
                    self.page,
                    text,
                    typo_probability=self.settings.typo_probability,
                )
            else:
                await self._paste_text(text)
        except Exception:
            await self.page.keyboard.insert_text(text)

        await asyncio.sleep(random.uniform(0.2, 0.5))
        await self.page.keyboard.press("Enter")
        await asyncio.sleep(random.uniform(0.8, 1.4))
        return True

    async def _paste_text(self, text: str) -> None:
        assert self.page is not None
        try:
            await self.page.evaluate(
                """async (t) => { await navigator.clipboard.writeText(t); }""", text
            )
            await self.page.keyboard.press("Control+v")
        except Exception:
            await self.page.keyboard.insert_text(text)

    async def _find_comment_box(self, post: Locator) -> Optional[Locator]:
        assert self.page is not None
        for sel in _COMMENT_BOX_SELECTORS:
            loc = post.locator(sel)
            if await loc.count() > 0:
                return loc.first
        for sel in _COMMENT_BOX_SELECTORS:
            loc = self.page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        return None

    async def react(self, post: Locator, reaction: str = "LIKE") -> bool:
        """Like / react on the active post; LIKE is a direct click, others use the tray."""
        assert self.page is not None
        reaction = (reaction or "LIKE").upper()
        like_btn = post.locator(_LIKE_BUTTON_SELECTORS).first
        try:
            if await like_btn.count() == 0:
                return False
            if reaction == "LIKE":
                await self._click_human(like_btn)
                await asyncio.sleep(0.4)
                return True
            await like_btn.hover(timeout=4000)
            await asyncio.sleep(0.7)
            react_sel = (
                f"[aria-label='{reaction}'][role='button'], "
                f"[aria-label*='{reaction}' i][role='button']"
            )
            react_btn = self.page.locator(react_sel).first
            if await react_btn.count() > 0 and await react_btn.is_visible():
                await self._click_human(react_btn)
                await asyncio.sleep(0.4)
                return True
            await self._click_human(like_btn)
            await asyncio.sleep(0.4)
            return True
        except Exception as exc:
            print(f"[M1 FB] React failed: {exc}")
            return False

    async def _click_human(self, locator: Locator) -> None:
        """Bezier mouse move + click when humanization is enabled."""
        assert self.page is not None
        box = await locator.bounding_box()
        if box and self.settings.humanization_enabled:
            await self.human.move_mouse_bezier(
                self.page,
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
        await locator.click(timeout=5000)

    # ------------------------------------------------------------------ scroll
    async def scroll_to_next_post(self) -> None:
        """
        Natural inertia scroll to reveal the next feed unit.
        PHASE-06: pixel range from the active scroll profile
        (effective_scroll) and inter-chunk pauses scaled by its multiplier.
        """
        assert self.page is not None
        await self.bring_to_front()
        dmin, dmax, smul = self.settings.effective_scroll()
        lo, hi = sorted((int(dmin), int(dmax)))
        total = random.randint(max(1, lo), max(1, hi))
        steps = random.randint(3, 6)
        remaining = total
        for i in range(steps):
            if remaining <= 0:
                break
            chunk = (
                remaining
                if i == steps - 1
                else int(remaining / (steps - i) * random.uniform(0.75, 1.25))
            )
            chunk = max(40, min(remaining, chunk))
            await self.page.mouse.wheel(0, chunk)
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.08, 0.28) * smul)
        self._active_post = None
        await asyncio.sleep(random.uniform(0.4, 0.8))

    async def slow_scroll(self, distance: Optional[int] = None) -> None:
        """Extra human scroll (used between skipped posts)."""
        assert self.page is not None
        total = distance if distance is not None else random.randint(420, 720)
        steps = random.randint(8, 14)
        remaining = float(total)
        for i in range(steps):
            if remaining <= 0:
                break
            chunk = (
                remaining
                if i == steps - 1
                else remaining / (steps - i) * random.uniform(0.75, 1.2)
            )
            await self.page.mouse.wheel(0, int(chunk))
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.12, 0.28))
        await asyncio.sleep(random.uniform(0.35, 0.7))

    # ------------------------------------------------------------------ safety
    async def checkpoint_detected(self) -> bool:
        """Detect a Facebook security challenge / checkpoint on screen."""
        if self.page is None or self.page.is_closed():
            return False
        try:
            text = await self.page.evaluate(
                """() => document.body ? document.body.innerText.slice(0, 300000).toLowerCase() : ''"""
            )
        except Exception:
            return False
        if not text:
            return False
        return any(phrase in text for phrase in _CHECKPOINT_PHRASES)

    async def post_word_count(self) -> int:
        """Approximate word count of the active post (used for reading dwell)."""
        post = self._active_post
        if post is None:
            return 40
        try:
            text = (await post.inner_text(timeout=3000)) or ""
            return max(5, len(text.split()))
        except Exception:
            return 40

    # ------------------------------------------------------------------ internal
    async def _emit(self, phase: str) -> None:
        if self.on_phase is not None:
            try:
                await self.on_phase(phase)
            except Exception:
                pass
