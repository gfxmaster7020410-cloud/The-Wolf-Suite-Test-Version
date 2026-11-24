"""
Facebook Auto Engagement Bot — Module 1

Flow:
  1) Existing Chrome profile (no new profile)
  2) Open Facebook.com
  3) Wait (dashboard setting) → slow human scroll once
  4) Full Facebook viewport screenshot
  5) Gemini API → 1-line comment (no #) + reaction
  6–8) Paste comment + Enter, then Like/React
  9) Wait 3–5s → scroll → loop (unlimited until Stop)
"""

from __future__ import annotations

import asyncio
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from services.module_1_feed_observer.gemini_api import generate_engagement
from services.shared.humanization import humanization


StatusCallback = Optional[Callable[[Dict[str, Any]], None]]


class FacebookAutoEngagementBot:
    """Small Playwright bot for unlimited FB comment + react loop."""

    def __init__(
        self,
        wait_time: float = 3.0,
        on_status: StatusCallback = None,
    ):
        # Existing profile only — never invent a random new profile name
        default_dir = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "browser_profiles"
            / "facebook"
        )
        self.user_data_dir = str(
            os.getenv("FB_USER_DATA_DIR") or default_dir
        ).strip() or str(default_dir)
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)

        self.facebook_url = os.getenv("FB_HOME_URL", "https://www.facebook.com")
        self.wait_time = max(0.0, float(wait_time))
        self.on_status = on_status

        # threading.Event: safe across uvicorn loop ↔ Playwright worker thread
        self._stop = threading.Event()
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._active_post: Optional[Locator] = None
        self._temp = Path(__file__).resolve().parents[2] / "temp"
        self._temp.mkdir(parents=True, exist_ok=True)

        # Live analytics
        self.comments_done = 0
        self.likes_done = 0
        self.posts_seen = 0
        self.last_comment = ""
        self.last_error = ""
        self.running = False

    # ------------------------------------------------------------------ control
    def request_stop(self) -> None:
        """Dashboard Stop button."""
        self._stop.set()

    def _stopped(self) -> bool:
        return self._stop.is_set()

    def _emit(self, **kwargs: Any) -> None:
        payload = {
            "running": self.running,
            "comments": self.comments_done,
            "likes": self.likes_done,
            "posts_seen": self.posts_seen,
            "last_comment": self.last_comment,
            "last_error": self.last_error,
            "wait_time": self.wait_time,
            **kwargs,
        }
        if self.on_status:
            try:
                self.on_status(payload)
            except Exception:
                pass

    # ------------------------------------------------------------------ browser
    async def start_browser(self) -> Page:
        """Open existing Chrome profile and go to Facebook."""
        self.playwright = await async_playwright().start()
        headless = os.getenv("FB_HEADLESS", "false").lower() in {"1", "true", "yes"}
        # Prefer installed Google Chrome + existing user-data-dir
        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": self.user_data_dir,
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
                channel="chrome",
                **launch_kwargs,
            )
            print(f"[M1 Bot] Using installed Chrome profile: {self.user_data_dir}")
        except Exception as exc:
            print(f"[M1 Bot] Chrome channel unavailable ({exc}); Playwright Chromium.")
            self.context = await self.playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
            print(f"[M1 Bot] Using Chromium profile: {self.user_data_dir}")

        try:
            await self.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://www.facebook.com",
            )
        except Exception:
            pass

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await self.page.goto(self.facebook_url, wait_until="domcontentloaded", timeout=90_000)
        await self._wait_facebook_ready()
        await self._wait_login_if_needed()
        return self.page

    async def _wait_facebook_ready(self, timeout_ms: int = 90_000) -> None:
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
        while time.time() < deadline and not self._stopped():
            if await self.page.locator(sel).count() > 0:
                return
            await asyncio.sleep(0.5)

    async def _wait_login_if_needed(self) -> None:
        """
        PHASE-02 parity: wait INDEFINITELY for manual login; only the
        dashboard Stop flag aborts. FB_LOGIN_WAIT_MS>0 keeps a bounded
        timeout for headless/CI use (0/unset = forever).
        """
        assert self.page is not None
        feed = await self.page.locator(
            "div[role='feed'], [aria-label*='News Feed' i]"
        ).count()
        if feed > 0:
            return
        print("[M1 Bot] Facebook login required — waiting indefinitely for manual sign-in...")
        self._emit(phase="waiting_login")
        try:
            wait_ms = int(os.getenv("FB_LOGIN_WAIT_MS", "0"))
        except ValueError:
            wait_ms = 0
        deadline = time.time() + wait_ms / 1000.0 if wait_ms > 0 else None
        while not self._stopped():
            if deadline is not None and time.time() > deadline:
                break
            await asyncio.sleep(1.5)
            if await self.page.locator(
                "div[role='feed'], [aria-label*='News Feed' i]"
            ).count() > 0:
                print("[M1 Bot] Login detected — continuing.")
                return
        if not self._stopped():
            raise RuntimeError("Facebook login timed out")

    async def close(self) -> None:
        self.running = False
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

    # ------------------------------------------------------------------ actions
    async def slow_human_scroll(self, distance: Optional[int] = None) -> None:
        """Slow, human-like downward scroll (not a fast jump)."""
        assert self.page is not None
        total = distance if distance is not None else random.randint(420, 720)
        steps = random.randint(8, 14)
        remaining = float(total)
        for i in range(steps):
            if self._stopped():
                return
            chunk = remaining if i == steps - 1 else remaining / (steps - i) * random.uniform(0.75, 1.2)
            await self.page.mouse.wheel(0, int(chunk))
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.12, 0.28))
        await asyncio.sleep(random.uniform(0.35, 0.7))

    async def full_screen_screenshot(self) -> str:
        """Full visible Facebook screen (viewport), not a single post crop."""
        assert self.page is not None
        out = self._temp / f"fb_screen_{int(time.time() * 1000)}.png"
        await self.page.screenshot(path=str(out), type="png", full_page=False)
        self._active_post = await self._first_visible_post()
        print(f"[M1 Bot] Full screen saved -> {out}")
        return str(out)

    async def _first_visible_post(self) -> Optional[Locator]:
        assert self.page is not None
        locs = self.page.locator(
            "div[role='feed'] div[role='article'], "
            "div[role='article'], "
            "div[data-pagelet*='FeedUnit']"
        )
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

    async def paste_comment(self, comment: str) -> bool:
        """Paste comment into the post comment box and press Enter."""
        assert self.page is not None
        comment = (comment or "").strip()
        if not comment:
            return False

        post = self._active_post or await self._first_visible_post()
        if post is None:
            print("[M1 Bot] No post found for commenting")
            return False

        # Open comment composer
        try:
            btn = post.locator(
                "[aria-label*='Comment' i][role='button'], "
                "[aria-label='Leave a comment'][role='button']"
            ).first
            if await btn.count() > 0:
                await btn.click(timeout=4000)
                await asyncio.sleep(0.4)
        except Exception:
            pass

        box = await self._find_comment_box(post)
        if box is None:
            print("[M1 Bot] Comment box not found")
            return False

        await box.click()
        await asyncio.sleep(0.2)

        # Paste (user asked for Paste, not slow type)
        try:
            await self.page.evaluate(
                """async (text) => {
                  await navigator.clipboard.writeText(text);
                }""",
                comment,
            )
            await self.page.keyboard.press("Control+v")
        except Exception:
            await self.page.keyboard.insert_text(comment)

        await asyncio.sleep(0.25)
        await self.page.keyboard.press("Enter")
        await asyncio.sleep(0.8)
        return True

    async def _find_comment_box(self, post: Locator) -> Optional[Locator]:
        assert self.page is not None
        selectors = [
            "[aria-label*='Write a comment' i][contenteditable='true']",
            "[aria-label*='Write a comment' i]",
            "div[role='textbox'][contenteditable='true']",
        ]
        for sel in selectors:
            loc = post.locator(sel)
            if await loc.count() > 0:
                return loc.first
        for sel in selectors:
            loc = self.page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        return None

    async def react_post(self, reaction: str = "LIKE") -> bool:
        """Like / React on the current post. Falls back to Like."""
        assert self.page is not None
        reaction = (reaction or "LIKE").upper()
        post = self._active_post or await self._first_visible_post()
        if post is None:
            return False

        # Simple Like first (most reliable)
        like_btn = post.locator(
            "[aria-label='Like'][role='button'], "
            "[aria-label*='Like' i][role='button']"
        ).first
        try:
            if await like_btn.count() == 0:
                return False
            if reaction == "LIKE":
                await like_btn.click(timeout=4000)
                await asyncio.sleep(0.4)
                return True

            # Hover Like to open reaction tray, then pick reaction
            await like_btn.hover(timeout=4000)
            await asyncio.sleep(0.7)
            react_sel = f"[aria-label='{reaction}'][role='button'], [aria-label*='{reaction}' i][role='button']"
            react_btn = self.page.locator(react_sel).first
            if await react_btn.count() > 0 and await react_btn.is_visible():
                await react_btn.click(timeout=3000)
                await asyncio.sleep(0.4)
                return True
            # Fallback: plain Like
            await like_btn.click(timeout=4000)
            await asyncio.sleep(0.4)
            return True
        except Exception as exc:
            print(f"[M1 Bot] React failed: {exc}")
            return False

    # ------------------------------------------------------------------ main loop
    async def run_forever(self) -> Dict[str, Any]:
        """Unlimited loop until request_stop()."""
        self._stop.clear()
        self.running = True
        self.last_error = ""
        self._emit(phase="starting")

        try:
            await self.start_browser()
            assert self.page is not None

            # Wait Time (dashboard) — after FB load, before first scroll
            print(f"[M1 Bot] Wait Time {self.wait_time:.1f}s before scroll...")
            self._emit(phase="wait_before_scroll")
            await self._interruptible_sleep(self.wait_time)

            # First slow scroll
            if not self._stopped():
                await self.slow_human_scroll()

            while not self._stopped():
                self.posts_seen += 1
                self._emit(phase="screenshot")
                shot = await self.full_screen_screenshot()

                # Spec 3.2 — variable reading dwell before analysis
                try:
                    dwell = humanization.reading_dwell()
                    print(f"[M1 Bot] Reading dwell {dwell:.1f}s...")
                    await self._interruptible_sleep(dwell)
                except Exception:
                    pass

                try:
                    self._emit(phase="gemini")
                    decision = await generate_engagement(shot)
                    comment = decision["comment"]
                    reaction = decision.get("reaction", "LIKE")
                except Exception as exc:
                    self.last_error = f"Gemini: {exc}"
                    self._emit(phase="gemini_error")
                    print(f"[M1 Bot] Gemini error: {exc}")
                    await self._interruptible_sleep(random.uniform(3, 5))
                    await self.slow_human_scroll()
                    continue
                finally:
                    try:
                        Path(shot).unlink(missing_ok=True)
                    except OSError:
                        pass

                self.last_comment = comment
                self._emit(phase="engaging", last_comment=comment)

                # Comment
                ok_c = await self.paste_comment(comment)
                if ok_c:
                    self.comments_done += 1
                    self._emit(phase="commented")
                else:
                    self.last_error = "Comment box not found / paste failed"

                # React
                ok_r = await self.react_post(reaction)
                if ok_r:
                    self.likes_done += 1
                    self._emit(phase="reacted", reaction=reaction)

                print(
                    f"[M1 Bot] Post #{self.posts_seen} | "
                    f"comments={self.comments_done} likes={self.likes_done} | "
                    f"{comment[:60]!r}"
                )

                # 3–5s then next post
                await self._interruptible_sleep(random.uniform(3.0, 5.0))
                if self._stopped():
                    break
                await self.slow_human_scroll()

            return self.stats()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._emit(phase="error")
            raise
        finally:
            self.running = False
            await self.close()
            self._emit(phase="stopped")

    async def _interruptible_sleep(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end and not self._stopped():
            await asyncio.sleep(min(0.25, end - time.time()))

    def stats(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "comments": self.comments_done,
            "likes": self.likes_done,
            "posts_seen": self.posts_seen,
            "last_comment": self.last_comment,
            "last_error": self.last_error,
            "wait_time": self.wait_time,
            "profile": self.user_data_dir,
        }
