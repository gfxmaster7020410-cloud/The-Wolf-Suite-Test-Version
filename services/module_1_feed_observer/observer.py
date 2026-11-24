"""
Wolf Suite — Module 1 Feed Observer

Phase 1: Persistent Chromium → Facebook → capture feed post screenshot
Phase 3: Human-like engage (Gaussian typing + Bezier click)
Continuous loop orchestration also lives here via Module1FeedObserver.
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

from services.module_1_feed_observer.gemini_bridge import (
    VALID_REACTIONS,
    GeminiWebAnalyzer,
    GeminiWebBridge,
    human_move_to,
)
from services.security_governance.pii_redactor import PIIRedactor
from services.shared.humanization import HumanizationEngine, humanization


class BezierMouseController:
    """Cubic Bezier trajectories via shared HumanizationEngine."""

    @staticmethod
    def generate_bezier_path(
        start: Tuple[float, float],
        end: Tuple[float, float],
        num_points: Optional[int] = None,
    ) -> List[Tuple[float, float]]:
        return humanization.bezier_path(start, end, num_points)

    @staticmethod
    async def move_along_path(page: Page, end: Tuple[float, float]) -> None:
        await humanization.move_mouse_bezier(page, end[0], end[1])


class GaussianTypingEngine:
    """Character-by-character typing with Gaussian delays (Phase 3)."""

    def __init__(self, page: Page, engine: Optional[HumanizationEngine] = None):
        self.page = page
        self.human = engine or humanization

    async def type_humanized(self, text: str, typo_probability: float = 0.20) -> None:
        await self.human.type_text_humanized(
            self.page, text, typo_probability=typo_probability
        )


class FeedObserver:
    """
    Phase 1 + 3 — Persistent Facebook browser session:
    login idle wait, screenshot capture, humanized comment engagement.
    """

    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        facebook_url: Optional[str] = None,
        engine: Optional[HumanizationEngine] = None,
    ):
        default_dir = (
            Path(__file__).resolve().parents[2] / "data" / "browser_profiles" / "facebook"
        )
        self.user_data_dir = str(
            user_data_dir or os.getenv("FB_USER_DATA_DIR", default_dir)
        )
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        self.facebook_url = facebook_url or os.getenv(
            "FB_HOME_URL", "https://www.facebook.com"
        )
        self.human = engine or humanization
        self.playwright: Optional[Any] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._active_post: Optional[Locator] = None
        self._temp_dir = Path(__file__).resolve().parents[2] / "temp"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> Page:
        """Launch persistent Chromium and open Facebook; wait until feed is ready."""
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=os.getenv("FB_HEADLESS", "false").lower() in {"1", "true", "yes"},
            viewport={"width": 1365, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        print(f"[FeedObserver] Opening {self.facebook_url}")
        await self.page.goto(self.facebook_url, wait_until="domcontentloaded", timeout=90_000)
        await self._wait_for_facebook_ready()
        await self._ensure_logged_in()
        return self.page

    async def _wait_for_facebook_ready(self, timeout_ms: int = 90_000) -> None:
        assert self.page is not None
        print("[FeedObserver] Waiting for Facebook to finish loading...")
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        try:
            await self.page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass

        deadline = time.time() + (timeout_ms / 1000.0)
        feed_sel = (
            "div[role='feed'], [aria-label*='News Feed' i], "
            "div[role='article'], [aria-label*='Create a post' i], "
            "[aria-label*='What' i][role='button'], input[name='email']"
        )
        while time.time() < deadline:
            try:
                if await self.page.locator(feed_sel).count() > 0:
                    await asyncio.sleep(1.0)
                    print("[FeedObserver] Facebook page settled.")
                    return
            except Exception:
                pass
            await asyncio.sleep(0.6)

    async def _is_logged_in(self) -> bool:
        if not self.page:
            return False
        feed = await self.page.locator(
            "div[role='feed'], [aria-label*='News Feed' i]"
        ).count()
        composer = await self.page.locator(
            "[aria-label*='What' i][role='button'], [aria-label*='Create a post' i]"
        ).count()
        return feed > 0 or composer > 0

    async def _ensure_logged_in(self) -> None:
        if await self._is_logged_in():
            return
        print("\n" + "=" * 72)
        print("[FeedObserver] INSTRUCTION: Log in to Facebook manually in the browser.")
        print(f"[FeedObserver] Profile: {self.user_data_dir}")
        print("[FeedObserver] Waiting until the home feed loads...")
        print("=" * 72 + "\n")
        wait_ms = int(os.getenv("FB_LOGIN_WAIT_MS", "300000"))
        deadline = time.time() + (wait_ms / 1000.0)
        while time.time() < deadline:
            await asyncio.sleep(2.0)
            try:
                if self.page is None or self.page.is_closed():
                    raise RuntimeError("Facebook page closed during login wait")
                if await self._is_logged_in():
                    print("[FeedObserver] Manual login detected - continuing.")
                    await asyncio.sleep(1.0)
                    return
            except Exception as exc:
                print(f"[FeedObserver] Login loop: {exc}")
                raise
        raise RuntimeError("Facebook login not completed within wait window")

    async def capture_feed_post_screenshot(
        self, output_path: Optional[str] = None
    ) -> str:
        """
        Locate first visible feed post, human-scroll to center, screenshot it.
        Returns local PNG path.
        """
        if not self.page:
            raise RuntimeError("Browser/Page is not initialized. Call start() first.")

        await self.page.bring_to_front()
        out_file = Path(output_path) if output_path else (
            self._temp_dir / f"post_screenshot_{int(time.time() * 1000)}.png"
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)

        post = await self._locate_first_visible_post()
        if post is None:
            raise RuntimeError("No visible post element found in the Facebook feed")

        self._active_post = post
        await self._human_scroll_post_to_center(post)

        try:
            await post.screenshot(path=str(out_file), type="png")
        except Exception as exc:
            print(f"[FeedObserver] Element screenshot failed ({exc}); viewport fallback.")
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

        print(f"[FeedObserver] Screenshot saved -> {out_file}")
        return str(out_file)

    async def _locate_first_visible_post(self) -> Optional[Locator]:
        assert self.page is not None
        combined = (
            "div[role='feed'] div[role='article'], "
            "div[role='article'], "
            "div[data-pagelet*='FeedUnit']"
        )
        locs = self.page.locator(combined)
        count = await locs.count()
        for i in range(min(count, 12)):
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

        steps = random.randint(4, 7)
        remaining = scroll_needed
        for i in range(steps):
            if i == steps - 1:
                chunk = remaining
            else:
                chunk = remaining / (steps - i) * random.uniform(0.7, 1.3)
            await self.page.mouse.wheel(0, int(chunk))
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.12, 0.32))
        await asyncio.sleep(random.uniform(0.45, 0.9))

    async def engage_post(self, comment_text: str) -> bool:
        """
        Phase 3 — Switch to Facebook tab, type comment humanistically, submit.
        """
        if not self.page:
            raise RuntimeError("Browser/Page is not initialized. Call start() first.")
        comment = re.sub(r"#\w+", "", (comment_text or "")).strip()
        if not comment:
            return False

        await self.page.bring_to_front()
        await asyncio.sleep(0.4)

        post = self._active_post
        if post is None:
            post = await self._locate_first_visible_post()
        if post is None:
            print("[FeedObserver] engage_post: no active post locator")
            return False

        # Prefer clicking Comment control first (opens composer on many layouts)
        try:
            comment_btn = post.locator(
                "[aria-label*='Comment' i][role='button'], "
                "[aria-label='Leave a comment'][role='button']"
            ).first
            if await comment_btn.count() > 0:
                box = await comment_btn.bounding_box()
                if box:
                    await humanization.move_mouse_bezier(
                        self.page,
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                await comment_btn.click(timeout=4_000)
                await asyncio.sleep(random.uniform(0.35, 0.8))
        except Exception:
            pass

        target = await self._find_comment_box(post)
        if target is None:
            print("[FeedObserver] engage_post: comment box not found")
            return False

        box = await target.bounding_box()
        if box:
            await humanization.move_mouse_bezier(
                self.page,
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
        await target.click()
        await asyncio.sleep(random.uniform(0.2, 0.45))

        # Do NOT use .fill() — Gaussian human typing with ~20% typo chance
        typing = GaussianTypingEngine(self.page, self.human)
        await typing.type_humanized(comment, typo_probability=0.20)

        submitted = await self._submit_comment(post, target)
        # Simulate reading success state
        await asyncio.sleep(random.uniform(2.0, 4.0))
        return submitted

    async def _find_comment_box(self, post: Locator) -> Optional[Locator]:
        assert self.page is not None
        selectors = [
            "[aria-label*='Write a comment' i][contenteditable='true']",
            "[aria-label*='Write a comment' i]",
            "div[role='textbox'][contenteditable='true']",
            "div[contenteditable='true'][role='textbox']",
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

    async def _submit_comment(self, post: Locator, comment_box: Locator) -> bool:
        assert self.page is not None
        # Try explicit Post/Comment submit via Bezier click
        for sel in [
            "[aria-label='Comment' i][role='button']",
            "[aria-label='Post' i][role='button']",
            "div[aria-label='Comment'][role='button']",
        ]:
            btn = post.locator(sel).last
            try:
                if await btn.count() > 0 and await btn.is_visible():
                    box = await btn.bounding_box()
                    if box:
                        await humanization.move_mouse_bezier(
                            self.page,
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2,
                        )
                        await btn.click()
                        return True
            except Exception:
                continue

        # Fallback: Enter in comment box
        await comment_box.click()
        await self.page.keyboard.press("Enter")
        return True

    async def react_post(self, reaction: str = "LIKE") -> bool:
        """
        Phase 3 — React on the active post (LIKE/LOVE/CARE/HAHA/WOW).
        LIKE is a direct click; other reactions open the hover-tray first.
        """
        if not self.page:
            raise RuntimeError("Browser/Page is not initialized. Call start() first.")
        reaction = (reaction or "LIKE").upper()
        if reaction not in VALID_REACTIONS:
            reaction = "LIKE"

        post = self._active_post or await self._locate_first_visible_post()
        if post is None:
            print("[FeedObserver] react_post: no active post locator")
            return False

        like_btn = post.locator(
            "[aria-label='Like'][role='button'], "
            "[aria-label*='Like' i][role='button']"
        ).first
        try:
            if await like_btn.count() == 0:
                return False

            if reaction == "LIKE":
                box = await like_btn.bounding_box()
                if box:
                    await humanization.move_mouse_bezier(
                        self.page,
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                await like_btn.click(timeout=4_000)
                await asyncio.sleep(0.4)
                return True

            # Hover Like to open reaction tray, then pick the chosen reaction
            await like_btn.hover(timeout=4_000)
            await asyncio.sleep(0.7)
            react_sel = (
                f"[aria-label='{reaction}'][role='button'], "
                f"[aria-label*='{reaction}' i][role='button']"
            )
            react_btn = self.page.locator(react_sel).first
            if await react_btn.count() > 0 and await react_btn.is_visible():
                box = await react_btn.bounding_box()
                if box:
                    await humanization.move_mouse_bezier(
                        self.page,
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                await react_btn.click(timeout=3_000)
                await asyncio.sleep(0.4)
                return True
            # Fallback: plain Like
            box = await like_btn.bounding_box()
            if box:
                await humanization.move_mouse_bezier(
                    self.page,
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
            await like_btn.click(timeout=4_000)
            await asyncio.sleep(0.4)
            return True
        except Exception as exc:
            print(f"[FeedObserver] React failed: {exc}")
            return False

    async def scroll_to_next_post(self) -> None:
        """Natural inertia scroll to reveal the next feed unit."""
        if not self.page:
            return
        await self.page.bring_to_front()
        total = random.randint(
            self.human.profile.scroll_delta_min,
            self.human.profile.scroll_delta_max,
        )
        steps = random.randint(3, 6)
        remaining = total
        for i in range(steps):
            chunk = (
                remaining
                if i == steps - 1
                else int(remaining / (steps - i) * random.uniform(0.75, 1.25))
            )
            chunk = max(40, min(remaining, chunk))
            await self.page.mouse.wheel(0, chunk)
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.08, 0.28))
            if remaining <= 0:
                break
        self._active_post = None
        await asyncio.sleep(0.5)

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


class Module1FeedObserver:
    """
    Suite / API-compatible wrapper around FeedObserver + GeminiWebBridge.
    Runs the continuous capture → Gemini → engage loop for `max_posts`.
    """

    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        facebook_url: Optional[str] = None,
        engine: Optional[HumanizationEngine] = None,
    ):
        self.observer = FeedObserver(
            user_data_dir=user_data_dir,
            facebook_url=facebook_url,
            engine=engine,
        )
        self.human = engine or humanization
        self.pii = PIIRedactor()
        self.gemini = GeminiWebAnalyzer()
        self.default_reaction = os.getenv("M1_DEFAULT_REACTION", "LIKE").upper()

    async def run_observer_session(
        self,
        target_url: Optional[str] = None,
        max_posts: int = 1,
        account_id: str = "fb_main",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        url = target_url or self.observer.facebook_url
        if target_url:
            self.observer.facebook_url = target_url

        cap = self.human.can_act(account_id)
        if not cap["allowed"]:
            return {"status": "THROTTLED_HUMAN_CAP", "posts_processed": 0, "cap": cap}

        results: List[Dict[str, Any]] = []
        bridge: Optional[GeminiWebBridge] = None

        try:
            await self.observer.start()
            assert self.observer.context is not None
            assert self.observer.page is not None

            try:
                from services.shared.visual_runner import VisualRunner

                await VisualRunner.inject_hud_overlay(
                    self.observer.page,
                    "Module 1 · Feed Observer",
                    "Facebook ready",
                    "LIVE",
                )
            except Exception:
                pass

            if self.gemini.mode in {"web", "both"}:
                bridge = GeminiWebBridge(self.observer.context)
                await bridge.ensure_ready()
                self.gemini._bridge = bridge

            for i in range(max(1, max_posts)):
                cap = self.human.can_act(account_id)
                if not cap["allowed"]:
                    break

                step: Dict[str, Any] = {
                    "index": i,
                    "steps": {
                        "1_navigation": "ok",
                        "2_post_capture": None,
                        "3_gemini_analysis": None,
                        "4_engagement": None,
                    },
                }

                try:
                    shot_path = await self.observer.capture_feed_post_screenshot()
                    step["steps"]["2_post_capture"] = "ok"
                    step["screenshot"] = shot_path
                except Exception as exc:
                    step["steps"]["2_post_capture"] = f"error:{exc}"
                    results.append(step)
                    await self.observer.scroll_to_next_post()
                    continue

                # Spec 3.2 — reading dwell scaled to post content before any action
                try:
                    dwell = self.human.reading_dwell()
                    print(f"[FeedObserver] Reading dwell {dwell:.1f}s before analysis...")
                    await self.human.async_apply_delay(dwell, fast_cap=1.2)
                except Exception:
                    pass

                try:
                    if bridge is not None:
                        comment = await bridge.generate_comment(shot_path)
                        decision = {
                            "reaction": self.default_reaction,
                            "comment": comment,
                            "source": "gemini_web",
                            "confidence": 0.9,
                        }
                    else:
                        screenshot = Path(shot_path).read_bytes()
                        decision = await self.gemini.get_best_comment(
                            screenshot, self.observer.context
                        )
                        comment = decision.get("comment", "")

                    comment = self.pii.sanitize_prompt(comment).strip()
                    comment = re.sub(r"#\w+", "", comment).strip()
                    reaction = str(
                        decision.get("reaction", self.default_reaction)
                    ).upper()
                    if reaction not in VALID_REACTIONS:
                        reaction = (
                            self.default_reaction
                            if self.default_reaction in VALID_REACTIONS
                            else "LIKE"
                        )

                    step["decision"] = {**decision, "comment": comment, "reaction": reaction}
                    step["steps"]["3_gemini_analysis"] = {
                        "source": decision.get("source"),
                        "comment": comment,
                        "reaction": reaction,
                    }
                except Exception as exc:
                    print(f"[FeedObserver] Gemini failed - skipping post: {exc}")
                    step["steps"]["3_gemini_analysis"] = f"error:{exc}"
                    results.append(step)
                    try:
                        Path(shot_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                    await self.observer.scroll_to_next_post()
                    continue

                await self.observer.page.bring_to_front()

                if dry_run:
                    step["steps"]["4_engagement"] = "skipped_dry_run"
                    results.append(step)
                else:
                    try:
                        # Spec Step 4 — react first, then comment
                        reacted = False
                        if reaction not in {"", "NONE"}:
                            await self.human.async_apply_delay(
                                self.human.reaction_decision_pause(), fast_cap=0.5
                            )
                            reacted = await self.observer.react_post(reaction)
                        commented = await self.observer.engage_post(comment)
                        step["commented"] = commented
                        step["reacted"] = reacted
                        step["steps"]["4_engagement"] = {
                            "commented": commented,
                            "reacted": reacted,
                        }
                        if commented:
                            self.human.record_action(account_id)
                    except Exception as exc:
                        step["steps"]["4_engagement"] = f"error:{exc}"
                        raise
                    results.append(step)

                try:
                    Path(shot_path).unlink(missing_ok=True)
                except OSError:
                    pass

                if i + 1 < max_posts:
                    await self.observer.scroll_to_next_post()
                    dwell = self.human.post_scroll_dwell()
                    if self.human.simulate_fast():
                        dwell = min(dwell, 1.0)
                    await asyncio.sleep(dwell)

            return {
                "status": "COMPLETED",
                "pipeline": [
                    "1_facebook_navigation",
                    "2_post_capture",
                    "3_gemini_analysis",
                    "4_engagement_execution",
                    "5_human_like_automation",
                ],
                "facebook_url": url,
                "posts_processed": len(results),
                "results": results,
                "humanization": self.human.can_act(account_id),
                "gemini_mode": self.gemini.mode,
            }
        finally:
            if bridge is not None:
                await bridge.close()
            await self.observer.close()


async def run_feed_observer_cli(
    max_posts: int = 1,
    dry_run: bool = False,
    account_id: str = "fb_main",
    facebook_url: Optional[str] = None,
) -> Dict[str, Any]:
    observer = Module1FeedObserver(facebook_url=facebook_url)
    return await observer.run_observer_session(
        max_posts=max_posts,
        account_id=account_id,
        dry_run=dry_run,
    )
