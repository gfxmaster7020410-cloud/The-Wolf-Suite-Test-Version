"""
Wolf Suite — Module 1 Gemini Web Bridge (Phase 2)

Opens Gemini in the same persistent Chromium context as Facebook (new tab),
uploads a post screenshot, prompts for a short natural comment, extracts it.
Reuses one Gemini tab across the continuous Feed Observer loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from playwright.async_api import BrowserContext, Page

from services.ai_gateway.gateway import UniversalAIGateway
from services.module_1_feed_observer.config import maybe_strip_tags
from services.shared.humanization import humanization


COMMENT_PROMPT = (
    "Act as a real human Facebook user. Look at this post and write the single best, "
    "most natural, short comment to engage with it. "
    "Do NOT use any hashtags (#). Output ONLY the comment text, nothing else."
)

STRUCTURED_PROMPT = (
    "Look at this Facebook post/video screenshot. "
    "Return ONLY JSON (no markdown and NO hashtags): "
    '{"reaction":"LIKE|LOVE|CARE|HAHA|WOW","comment":"single best short natural comment without hashtags"}'
)

VALID_REACTIONS = {"LIKE", "LOVE", "CARE", "HAHA", "WOW"}

# Gemini chrome / nav labels that must never be treated as model replies
_UI_NOISE = re.compile(
    r"^(about gemini|gemini apps?|settings|new chat|sign in|google|privacy|"
    r"terms|help|activity|upgrade|try gemini|gemini advanced|explore gems|"
    r"tools|prompt gallery|get apps|conversations?|try another way|"
    r"use your google account|forgot email|create account|next)$",
    re.IGNORECASE,
)


def _is_ui_noise(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 3:
        return True
    if _UI_NOISE.match(t):
        return True
    # Multi-line chrome dumps often start with these
    first = t.splitlines()[0].strip().lower()
    if first in {
        "about gemini",
        "gemini apps",
        "settings",
        "new chat",
        "try another way",
        "sign in",
    }:
        return True
    if "about gemini" in t.lower() and len(t) < 80:
        return True
    if "try another way" in t.lower() and len(t) < 120:
        return True
    return False


def parse_gemini_reply(
    raw: str, structured: bool = False, strip_tags: bool = True
) -> Dict[str, Any]:
    """
    Parse a raw Gemini reply into {reaction, comment, confidence}.
    strip_tags=False (PHASE-10/D23) preserves AI hashtags end-to-end.
    """
    allow_tags = not strip_tags  # maybe_strip_tags(text, ALLOWED)
    text = (raw or "").strip()
    if not text:
        return {"reaction": "LIKE", "comment": "Nice!", "confidence": 0.3}

    if structured or text.lstrip().startswith("{"):
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        blob = fence.group(1) if fence else None
        if not blob:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            blob = m.group(0) if m else None
        if blob:
            try:
                data = json.loads(blob)
                reaction = str(data.get("reaction", "LIKE")).upper().strip()
                if reaction not in VALID_REACTIONS:
                    reaction = "LIKE"
                comment = str(data.get("comment", "")).strip() or "Nice one!"
                comment = maybe_strip_tags(comment, allow_tags).strip()
                return {"reaction": reaction, "comment": comment, "confidence": 0.9}
            except json.JSONDecodeError:
                pass

    comment = text
    comment = re.sub(r"^```.*?```", "", comment, flags=re.DOTALL).strip()
    chosen = ""
    for line in comment.splitlines():
        line = line.strip().strip('"').strip("'")
        if not line or _is_ui_noise(line):
            continue
        if line.lower().startswith(("here", "sure", "of course", "comment:", "reaction:")):
            if ":" in line and len(line.split(":", 1)[1].strip()) > 3:
                line = line.split(":", 1)[1].strip()
            else:
                continue
        chosen = line
        break
    # If multi-line model reply, take first usable sentence/paragraph chunk
    if not chosen and comment and not _is_ui_noise(comment):
        chosen = comment.splitlines()[0].strip()
    comment = maybe_strip_tags(chosen, allow_tags).strip()
    comment = comment[:280].strip() or "Really enjoyed this — thanks for sharing!"
    return {"reaction": "LIKE", "comment": comment, "confidence": 0.85}


class GeminiWebBridge:
    """
    Phase 2 — Gemini Web App bridge on an existing Playwright BrowserContext.
    Keeps one Gemini tab open for the continuous Module 1 loop.
    """

    def __init__(
        self,
        context: BrowserContext,
        *,
        structured: Optional[bool] = None,
        prompt: Optional[str] = None,
        response_wait_ms: Optional[int] = None,
        gemini_url: Optional[str] = None,
        fresh_chat: Optional[bool] = None,
        cancel_event: Optional[threading.Event] = None,
        on_phase: Optional[Callable[[str], Awaitable[None]]] = None,
        strip_tags: bool = True,
    ):
        self.context = context
        self.page: Optional[Page] = None
        self.cancel_event = cancel_event
        # Optional async callable(str) — lets the worker surface
        # "gemini_login_wait" / "gemini_ok" transitions on the dashboard.
        self.on_phase = on_phase
        # PHASE-10/D23: False preserves AI hashtags through the pipeline.
        self.strip_tags = strip_tags
        self.gemini_url = gemini_url or os.getenv("GEMINI_WEB_URL", "https://gemini.google.com/app")
        env_structured = os.getenv("GEMINI_STRUCTURED_JSON", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.structured = env_structured if structured is None else bool(structured)
        self.prompt = (
            prompt
            or (STRUCTURED_PROMPT if self.structured else COMMENT_PROMPT)
        )
        self.fresh_chat = True if fresh_chat is None else bool(fresh_chat)
        # Vision replies often need 20–45s; keep a sane floor even if .env is low
        self.response_wait_ms = max(
            35_000, int(response_wait_ms or os.getenv("GEMINI_RESPONSE_WAIT_MS", "45000"))
        )

    async def ensure_ready(self) -> Page:
        """Open Gemini once (or reuse) and wait until composer is interactive."""
        if self.page is not None and not self.page.is_closed():
            await self.page.bring_to_front()
            if await self._is_composer_ready(self.page):
                return self.page

        page = await self.context.new_page()
        self.page = page
        print(f"[GeminiWebBridge] Opening {self.gemini_url}")
        await page.goto(self.gemini_url, wait_until="domcontentloaded", timeout=90_000)
        await self._wait_for_loaded(page)
        await self._wait_for_google_login_if_needed(page)
        await self._wait_for_composer(page)
        print("[GeminiWebBridge] Ready - reusing this tab for the whole loop.")
        return page

    async def generate_comment(self, screenshot_path: str) -> str:
        """
        Upload screenshot → prompt → wait for stable reply → return comment text.
        Operates on the shared Gemini tab (does not close it; call close() at end).
        If Google Sign-in appears at any point, waits for manual login then retries.
        """
        path = Path(screenshot_path)
        if not path.exists():
            raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

        page = await self.ensure_ready()
        await page.bring_to_front()
        await self._wait_for_google_login_if_needed(page)

        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                return await self._generate_comment_once(page, path)
            except Exception as exc:
                last_error = exc
                # Sign-in wall mid-flow → wait, then one retry
                if await self._needs_sign_in(page):
                    print(
                        "[GeminiWebBridge] Sign-in appeared during send — "
                        "waiting for login, then retrying..."
                    )
                    await self._wait_for_google_login_if_needed(page)
                    continue
                raise
        raise RuntimeError(
            f"Gemini comment failed after login wait/retry: {last_error}"
        )

    async def generate_decision(
        self, screenshot_path: str, strip_tags: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Full decision from Gemini Web: {reaction, comment, confidence,
        source, attempts?}. Uses the configured prompt (plain or structured
        JSON) and parses the raw reply accordingly. Tag stripping follows
        the explicit arg, else this bridge's strip_tags attribute (PHASE-10).
        """
        allowed = self.strip_tags if strip_tags is None else strip_tags
        allow = not allowed  # helper contract: allowed=True keeps tags
        raw = await self.generate_comment(screenshot_path)
        decision = parse_gemini_reply(raw, structured=self.structured, strip_tags=allowed)
        comment = maybe_strip_tags(decision.get("comment", ""), allow).strip()
        if not comment:
            comment = maybe_strip_tags(raw or "", allow).strip()
        comment = comment[:280]
        decision["comment"] = comment or "Really enjoyed this — thanks for sharing!"
        decision["source"] = "gemini_web"
        return decision

    async def _generate_comment_once(self, page: Page, path: Path) -> str:
        # Clean turn so we don't scrape an older bubble
        if self.fresh_chat:
            await self._start_fresh_chat(page)
        else:
            await self._focus_composer(page)
        await self._wait_for_google_login_if_needed(page)

        # Focus first so paste/upload targets the chat input
        await self._focus_composer(page)

        uploaded = await self._upload_image(page, path)
        print(f"[GeminiWebBridge] Upload via file input: {uploaded}")
        if not uploaded:
            await self._paste_image_from_file(page, path)
            print("[GeminiWebBridge] Upload via clipboard paste attempted")

        # Upload/send often triggers Google auth wall
        await self._wait_for_google_login_if_needed(page)

        await self._wait_for_image_preview(page)
        await self._focus_composer(page)

        try:
            await page.keyboard.insert_text(self.prompt)
        except Exception:
            try:
                await page.keyboard.type(self.prompt, delay=12)
            except Exception:
                # Fallback: fill via JS into contenteditable
                await page.evaluate(
                    """(text) => {
                      const el = document.querySelector(
                        "rich-textarea div[contenteditable='true'], div[contenteditable='true'], textarea"
                      );
                      if (!el) return;
                      el.focus();
                      if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                        el.value = text;
                      } else {
                        el.textContent = text;
                        el.dispatchEvent(new InputEvent('input', { bubbles: true }));
                      }
                    }""",
                    self.prompt,
                )

        baseline = await self._extract_latest_reply(page)
        await asyncio.sleep(0.35)
        await self._submit_prompt(page)

        # After send: Sign in / Try another way → pause until logged in
        await asyncio.sleep(1.0)
        if await self._needs_sign_in(page):
            print(
                "[GeminiWebBridge] Sign-in required after send — "
                "complete Google login in the Gemini tab..."
            )
            await self._wait_for_google_login_if_needed(page)
            # Re-submit once logged in (message may have been lost)
            await self._focus_composer(page)
            try:
                await page.keyboard.insert_text(self.prompt)
            except Exception:
                await page.keyboard.type(self.prompt, delay=12)
            await self._submit_prompt(page)

        reply = await self._wait_for_stable_reply(page, baseline=baseline)
        if _is_ui_noise(reply) or reply.strip() == (baseline or "").strip():
            # One more chance if UI is actually a login wall misread as reply
            if await self._needs_sign_in(page):
                raise RuntimeError("Gemini sign-in still required")
            raise RuntimeError(
                "Gemini did not return a usable comment "
                f"(got: {(reply or '')[:80]!r})"
            )
        decision = parse_gemini_reply(
            reply, structured=self.structured, strip_tags=self.strip_tags
        )
        comment = maybe_strip_tags(decision.get("comment", ""), not self.strip_tags).strip()
        if not comment or _is_ui_noise(comment):
            raise RuntimeError(
                "Gemini returned an empty/invalid comment "
                f"(raw: {(reply or '')[:120]!r})"
            )
        return comment

    async def close(self) -> None:
        if self.page is not None and not self.page.is_closed():
            try:
                await self.page.close()
            except Exception:
                pass
        self.page = None

    # ------------------------------------------------------------------ waits
    async def _wait_for_loaded(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=60_000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        await asyncio.sleep(0.8)

    async def _needs_sign_in(self, page: Page) -> bool:
        """True when Google/Gemini is asking the user to sign in."""
        try:
            url = (page.url or "").lower()
        except Exception:
            url = ""
        # Hard auth redirects
        if "accounts.google.com" in url or "servicelogin" in url:
            return True

        # Visible auth controls / copy
        selectors = [
            "input[type='email']",
            "input[name='identifier']",
            "input#identifierId",
            "a[href*='ServiceLogin']",
            "button:has-text('Sign in')",
            "a:has-text('Sign in')",
            "text=Use your Google Account",
            "text=Try another way",
            "text=Forgot email?",
            "text=Sign in to continue",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                n = await loc.count()
                if n == 0:
                    continue
                for i in range(min(n, 3)):
                    try:
                        if await loc.nth(i).is_visible():
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        # Body text fallback (login interstitial on Gemini itself)
        try:
            body = (await page.locator("body").inner_text(timeout=2_000)).lower()
        except Exception:
            return False
        strong_phrases = (
            "sign in to continue",
            "use your google account",
            "to continue to gemini",
            "choose an account",
        )
        if any(p in body for p in strong_phrases):
            return True
        # "Try another way" is Google auth UI — only count when composer is missing
        if "try another way" in body and not await self._is_composer_ready(page):
            return True
        return False

    async def _emit_phase(self, phase: str) -> None:
        if self.on_phase is not None:
            try:
                await self.on_phase(phase)
            except Exception:
                pass

    async def _wait_for_google_login_if_needed(self, page: Page) -> None:
        """
        PHASE-02 (decision D6): if Sign-in is showing -> wait INDEFINITELY
        for manual login (only cancel_event aborts). If already logged in
        (composer ready, no auth wall) -> continue immediately.
        Emits gemini_login_wait on entry and gemini_ok once resolved so the
        dashboard reflects the blocked-on-human state.
        """
        try:
            if not await self._needs_sign_in(page):
                return
        except Exception:
            return

        await self._emit_phase("gemini_login_wait")
        # If the core raises (timeout/cancel), gemini_ok is intentionally
        # NOT emitted — dashboard keeps showing the blocked state.
        await self._wait_google_login_core(page)
        await self._emit_phase("gemini_ok")

    async def _wait_google_login_core(self, page: Page) -> None:
        # GEMINI_LOGIN_WAIT_MS semantics since PHASE-02:
        #   unset or "0"  -> wait forever (default, interactive runs)
        #   positive int  -> honor as millisecond timeout (headless/CI)
        raw_wait = os.getenv("GEMINI_LOGIN_WAIT_MS", "0")
        try:
            wait_ms = int(raw_wait)
        except ValueError:
            wait_ms = 0
        if wait_ms > 0:
            print(
                "[GeminiWebBridge] Google Sign-in detected — "
                f"log in manually in the Gemini tab (waiting up to {wait_ms // 1000}s)..."
            )
        else:
            print(
                "[GeminiWebBridge] Google Sign-in detected — log in manually "
                "in the Gemini tab; waiting indefinitely (Stop cancels)..."
            )
        deadline = time.time() + (wait_ms / 1000.0) if wait_ms > 0 else None
        while True:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise RuntimeError("Gemini login wait cancelled by user")
            if deadline is not None and time.time() > deadline:
                break
            await asyncio.sleep(1.0)
            try:
                # Navigate back to Gemini app if stuck on accounts.google.com after login
                url = (page.url or "").lower()
                if "accounts.google.com" not in url and await self._is_logged_in(page):
                    print("[GeminiWebBridge] Login detected — continuing.")
                    return
                if "accounts.google.com" not in url and not await self._needs_sign_in(page):
                    if await self._is_composer_ready(page):
                        print("[GeminiWebBridge] Login detected — continuing.")
                        return
                # After account picker, land back on Gemini
                if await self._is_logged_in(page):
                    if "gemini.google.com" not in url:
                        try:
                            await page.goto(
                                self.gemini_url,
                                wait_until="domcontentloaded",
                                timeout=60_000,
                            )
                            await self._wait_for_loaded(page)
                        except Exception:
                            pass
                    if await self._is_logged_in(page):
                        print("[GeminiWebBridge] Login detected — continuing.")
                        return
            except Exception:
                continue

        raise RuntimeError(
            "Gemini Sign-in still required — timed out waiting for Google login"
        )

    async def _is_logged_in(self, page: Page) -> bool:
        """Logged in = chat composer ready and no Sign-in wall."""
        if await self._needs_sign_in(page):
            return False
        return await self._is_composer_ready(page)

    async def _is_composer_ready(self, page: Page) -> bool:
        selectors = [
            "div[contenteditable='true']",
            "rich-textarea div[contenteditable='true']",
            "textarea",
            "[aria-label*='prompt' i]",
            "[aria-label*='Enter a prompt' i]",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).last
                if await loc.count() > 0 and await loc.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_composer(self, page: Page, timeout_ms: int = 90_000) -> None:
        print("[GeminiWebBridge] Waiting for chat input to finish loading...")
        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            if await self._is_composer_ready(page):
                return
            await asyncio.sleep(0.75)
        raise RuntimeError("Gemini composer never became ready")

    async def _submit_prompt(self, page: Page) -> None:
        """Prefer explicit Send button; Enter as fallback."""
        for sel in [
            "button[aria-label*='Send message' i]",
            "button[aria-label*='Send' i]",
            "button[aria-label*='Submit' i]",
            "button.send-button",
            "button:has(mat-icon:text('send'))",
        ]:
            loc = page.locator(sel)
            try:
                if await loc.count() == 0:
                    continue
                btn = loc.last
                if await btn.is_enabled():
                    await btn.click(timeout=5_000, force=True)
                    await asyncio.sleep(0.35)
                    print("[GeminiWebBridge] Submitted via Send button")
                    return
            except Exception:
                continue
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.35)
        print("[GeminiWebBridge] Submitted via Enter")

    async def _wait_for_image_preview(self, page: Page) -> None:
        await asyncio.sleep(0.9)
        try:
            thumb = page.locator(
                "img[src*='blob:'], [data-test-id*='attachment' i], "
                "button[aria-label*='Remove' i], img[alt*='Uploaded' i]"
            )
            await thumb.first.wait_for(state="visible", timeout=8_000)
        except Exception:
            # Upload may still succeed without a clear thumbnail selector
            await asyncio.sleep(0.6)

    async def _start_fresh_chat(self, page: Page) -> None:
        """Best-effort New chat so each post gets a fresh model bubble."""
        for sel in [
            "button[aria-label*='New chat' i]",
            "a[aria-label*='New chat' i]",
            "button:has-text('New chat')",
        ]:
            loc = page.locator(sel)
            try:
                if await loc.count() == 0:
                    continue
                await loc.first.click(timeout=3_000, force=True)
                await asyncio.sleep(0.8)
                await self._wait_for_composer(page, timeout_ms=30_000)
                return
            except Exception:
                continue

    async def _is_generating(self, page: Page) -> bool:
        # Narrow selectors only — broad ".loading" matches permanent chrome
        for sel in [
            "button[aria-label*='Stop' i]",
            "button[aria-label*='Stop generating' i]",
            "button:has-text('Stop')",
        ]:
            loc = page.locator(sel)
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_stable_reply(
        self, page: Page, baseline: str = ""
    ) -> str:
        reply = ""
        start = time.time()
        max_wait = min(90.0, max(35.0, self.response_wait_ms / 1000.0))
        last = ""
        stable = 0
        baseline_n = (baseline or "").strip()
        saw_generate = False
        while time.time() - start < max_wait:
            await asyncio.sleep(0.6)
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise RuntimeError("Gemini reply wait cancelled by user")
            # Mid-wait Sign-in wall → pause clock until user logs in
            if await self._needs_sign_in(page):
                print(
                    "[GeminiWebBridge] Sign-in appeared while waiting for reply — "
                    "pausing for login..."
                )
                await self._wait_for_google_login_if_needed(page)
                start = time.time()  # reset reply wait after login
                last = ""
                stable = 0
                saw_generate = False
                continue

            generating = await self._is_generating(page)
            if generating:
                saw_generate = True
                stable = 0
                continue
            # If we never saw Stop, still allow extraction after a short grace
            if not saw_generate and (time.time() - start) < 2.5:
                continue

            candidate = await self._extract_latest_reply(page)
            if not candidate or _is_ui_noise(candidate):
                continue
            if candidate.strip() == baseline_n:
                continue
            if len(candidate.strip()) < 4:
                continue

            if candidate == last:
                stable += 1
                if stable >= 3:
                    reply = candidate
                    break
            else:
                last = candidate
                stable = 0
                print(f"[GeminiWebBridge] Reply growing: {candidate[:100]!r}")
        if not reply:
            reply = await self._extract_latest_reply(page)
        print(f"[GeminiWebBridge] Final reply len={len(reply or '')}")
        return reply

    # ------------------------------------------------------------------ I/O
    async def _focus_composer(self, page: Page) -> None:
        selectors = [
            "rich-textarea div[contenteditable='true']",
            "div[contenteditable='true'][aria-label*='prompt' i]",
            "div[contenteditable='true']",
            "textarea",
            "[aria-label*='prompt' i]",
            "[aria-label*='Enter a prompt' i]",
            "[aria-label*='Enter a message' i]",
        ]
        for sel in selectors:
            loc = page.locator(sel).last
            try:
                if await loc.count() == 0:
                    continue
                try:
                    await loc.wait_for(state="visible", timeout=8_000)
                except Exception:
                    pass
                box = await loc.bounding_box()
                if box:
                    await humanization.move_mouse_bezier(
                        page,
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                        num_points=8,
                    )
                # force=True avoids actionability timeouts on Gemini overlays
                await loc.click(timeout=8_000, force=True)
                await asyncio.sleep(0.2)
                return
            except Exception:
                continue
        # Last resort: click lower-center of viewport + Tab into composer
        vp = page.viewport_size or {"width": 1365, "height": 900}
        await page.mouse.click(vp["width"] * 0.5, vp["height"] * 0.85)
        await asyncio.sleep(0.15)
        await page.keyboard.press("Tab")

    async def _upload_image(self, page: Page, path: Path) -> bool:
        async def _set_any_file_input() -> bool:
            file_inputs = page.locator("input[type='file']")
            n = await file_inputs.count()
            for i in range(n):
                try:
                    await file_inputs.nth(i).set_input_files(str(path))
                    await asyncio.sleep(0.8)
                    return True
                except Exception:
                    continue
            return False

        if await _set_any_file_input():
            return True

        # Open + / upload menu first (Gemini often hides the file input)
        for label in [
            "Open upload file menu",
            "Upload files",
            "Upload File",
            "Upload",
            "Add files",
            "Attach",
            "Insert",
        ]:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            try:
                if await btn.count() == 0:
                    continue
                await btn.first.click(timeout=4_000, force=True)
                await asyncio.sleep(0.55)
                # Second click for nested "Upload files"
                nested = page.get_by_role(
                    "button", name=re.compile(r"Upload files|From device|Files", re.I)
                )
                if await nested.count() > 0:
                    try:
                        await nested.first.click(timeout=3_000, force=True)
                        await asyncio.sleep(0.4)
                    except Exception:
                        pass
                if await _set_any_file_input():
                    return True
            except Exception:
                continue

        # Plus / add icon near composer
        for sel in [
            "button[aria-label*='Add' i]",
            "button[aria-label*='+' i]",
            "button[aria-label*='Open' i][aria-label*='upload' i]",
        ]:
            loc = page.locator(sel)
            try:
                if await loc.count() == 0:
                    continue
                await loc.last.click(timeout=3_000, force=True)
                await asyncio.sleep(0.5)
                if await _set_any_file_input():
                    return True
            except Exception:
                continue
        return False

    async def _paste_image_from_file(self, page: Page, path: Path) -> None:
        await self._focus_composer(page)
        data = path.read_bytes()
        try:
            await page.evaluate(
                """async (b64) => {
                  const bin = atob(b64);
                  const bytes = new Uint8Array(bin.length);
                  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                  const blob = new Blob([bytes], { type: 'image/png' });
                  const item = new ClipboardItem({ 'image/png': blob });
                  await navigator.clipboard.write([item]);
                }""",
                base64.b64encode(data).decode("ascii"),
            )
            await page.keyboard.press("Control+v")
            await asyncio.sleep(1.2)
        except Exception as exc:
            raise RuntimeError(f"Gemini image upload/paste failed: {exc}") from exc

    async def _extract_latest_reply(self, page: Page) -> str:
        """Prefer real model bubbles; never return bare nav chrome."""
        try:
            text = await page.evaluate(
                """() => {
                  const noise = new Set([
                    'about gemini','gemini apps','settings','new chat','sign in',
                    'google','tools','prompt gallery','conversations'
                  ]);
                  const bad = (t) => {
                    const s = (t || '').trim();
                    if (!s || s.length < 2) return true;
                    const first = s.split('\\n')[0].trim().toLowerCase();
                    if (noise.has(first)) return true;
                    if (/^about gemini$/i.test(s)) return true;
                    return false;
                  };
                  const sels = [
                    'model-response .markdown',
                    'model-response',
                    '[data-message-author-role="model"]',
                    '.model-response-text',
                    'message-content .markdown',
                    'message-content',
                    '[data-response-id]',
                    '.response-content',
                    'div[class*="model-response"]',
                    'response-container',
                  ];
                  for (const sel of sels) {
                    const nodes = Array.from(document.querySelectorAll(sel));
                    for (let i = nodes.length - 1; i >= 0; i--) {
                      const t = (nodes[i].innerText || '').trim();
                      if (!bad(t)) return t;
                    }
                  }
                  // Fallback: last substantial paragraph inside main chat pane
                  const main = document.querySelector('main')
                    || document.querySelector('[role="main"]')
                    || document.body;
                  const blocks = Array.from(main.querySelectorAll('p, .markdown, div'))
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t.length >= 8 && t.length <= 600 && !bad(t));
                  return blocks.length ? blocks[blocks.length - 1] : '';
                }"""
            )
            if text and not _is_ui_noise(text):
                return text.strip()
        except Exception:
            pass

        candidates = [
            "model-response .markdown",
            "model-response",
            '[data-message-author-role="model"]',
            "[data-response-id]",
            ".model-response-text",
            "message-content .markdown",
            "message-content",
        ]
        for sel in candidates:
            locs = page.locator(sel)
            n = await locs.count()
            for i in range(n - 1, -1, -1):
                try:
                    text = (await locs.nth(i).inner_text()).strip()
                except Exception:
                    continue
                if text and not _is_ui_noise(text):
                    return text
        return ""


class GeminiWebAnalyzer:
    """
    Compatibility adapter used by Module1FeedObserver / suite visual runner.
    Wraps GeminiWebBridge and optional API fallbacks.
    """

    def __init__(self):
        self.mode = os.getenv("GEMINI_MODE", "web").lower()
        self.structured = os.getenv("GEMINI_STRUCTURED_JSON", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        self.prompt = STRUCTURED_PROMPT if self.structured else COMMENT_PROMPT
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("MASTER_AI_API_KEY", "")
        self.api_model = os.getenv("GEMINI_API_MODEL", "gemini-2.0-flash")
        self.api_base = os.getenv(
            "GEMINI_API_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        )
        self.gateway = UniversalAIGateway()
        self._bridge: Optional[GeminiWebBridge] = None

    async def ensure_ready(self, context: BrowserContext) -> Page:
        if self._bridge is None:
            self._bridge = GeminiWebBridge(context)
        return await self._bridge.ensure_ready()

    async def close(self) -> None:
        if self._bridge is not None:
            await self._bridge.close()
            self._bridge = None

    async def get_best_comment(
        self,
        screenshot_bytes: bytes,
        context: BrowserContext,
    ) -> Dict[str, Any]:
        errors = []
        if self.mode in {"web", "both"}:
            try:
                if self._bridge is None:
                    self._bridge = GeminiWebBridge(context)
                tmp = Path("temp") / f"gemini_shot_{int(time.time() * 1000)}.png"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(screenshot_bytes)
                try:
                    comment = await self._bridge.generate_comment(str(tmp))
                finally:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return {
                    "reaction": "LIKE",
                    "comment": comment,
                    "confidence": 0.9,
                    "source": "gemini_web",
                }
            except Exception as exc:
                errors.append(f"web:{exc}")
                if self.mode == "web":
                    raise

        if self.mode in {"api", "both"}:
            try:
                return await self._via_api(screenshot_bytes)
            except Exception as exc:
                errors.append(f"api:{exc}")

        return {
            "reaction": "LIKE",
            "comment": "This is great — thanks for sharing!",
            "confidence": 0.35,
            "source": "fallback",
            "errors": errors,
        }

    async def analyze_screenshot(
        self, screenshot_bytes: bytes, context: Optional[BrowserContext] = None
    ) -> Dict[str, Any]:
        if context is None:
            raise RuntimeError("Browser context required for Gemini Web analysis")
        return await self.get_best_comment(screenshot_bytes, context)

    async def _via_api(self, screenshot_bytes: bytes) -> Dict[str, Any]:
        if not self.api_key or str(self.api_key).startswith("sk-proj-placeholder"):
            raise RuntimeError("GEMINI_API_KEY not configured")
        b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        url = (
            f"{self.api_base.rstrip('/')}/models/{self.api_model}:generateContent"
            f"?key={self.api_key}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": self.prompt},
                        {"inline_data": {"mime_type": "image/png", "data": b64}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.55, "maxOutputTokens": 200},
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            res = await client.post(url, json=payload)
            res.raise_for_status()
            body = res.json()
        text = (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        decision = parse_gemini_reply(text, structured=self.structured)
        decision["source"] = "gemini_api"
        return decision


async def human_move_to(page: Page, x: float, y: float) -> None:
    await humanization.move_mouse_bezier(page, x, y, num_points=10)


def random_key_delay_ms() -> int:
    return max(8, int(humanization.keystroke_delay() * 1000 * 0.35))


# Back-compat aliases
GeminiVisionBridge = GeminiWebAnalyzer
GeminiWebClient = GeminiWebBridge
parse_decision_payload = parse_gemini_reply
BEST_COMMENT_PROMPT = COMMENT_PROMPT
