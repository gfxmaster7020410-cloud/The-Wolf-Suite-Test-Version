"""
Wolf Suite — Shared Visual Automation Runner & On-Screen Display Engine.

Launches headful Playwright Chromium windows for all modules so every action,
step, decision, and result is displayed visually on screen in real-time.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import random
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from playwright.async_api import BrowserContext, Page, async_playwright

from services.shared.humanization import humanization
from services.shared.preview_store import active_preview_session_id, preview_store


DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[2] / "data" / "browser_profiles"


class VisualRunner:
    """
    On-Screen Visual Execution Engine for Wolf Suite.
    Renders live browser windows with step overlays, Bezier cursor movements,
    and real-time visual progress dashboards.
    """

    def __init__(self, module_id: str, title: str = "Wolf Suite Module"):
        self.module_id = module_id
        self.title = title
        self.profile_dir = str(
            Path(os.getenv("WOLF_BROWSER_DIR", DEFAULT_PROFILES_DIR)) / module_id
        )
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        # Default SUITE_HEADLESS=false → browser opens on screen
        self.headless = os.getenv("SUITE_HEADLESS", "false").lower() in {"1", "true", "yes"}
        self._playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.steps: List[Dict[str, Any]] = []
        self.cards: List[Dict[str, Any]] = []
        self.subtitle = "Preparing live execution…"
        self.preview_session_id: Optional[str] = None

    async def launch_visual_session(self) -> Tuple[Any, BrowserContext, Page]:
        self._playwright = await async_playwright().start()
        self.context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=self.headless,
            viewport={"width": 1365, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self._playwright, self.context, self.page

    async def close(self) -> None:
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

    async def begin(
        self,
        subtitle: str,
        planned_steps: List[str],
        cards: Optional[List[Dict[str, Any]]] = None,
        preview_session_id: Optional[str] = None,
    ) -> Page:
        visual = os.getenv("SUITE_VISUAL", "true").lower() in {"1", "true", "yes"}
        self.subtitle = subtitle
        self.cards = cards or []
        self.steps = [
            {"name": name, "detail": "Waiting…", "status": "pending"} for name in planned_steps
        ]
        existing = preview_session_id or active_preview_session_id.get()
        if existing:
            self.preview_session_id = existing
        else:
            self.preview_session_id = preview_store.start(
                module_id=self.module_id,
                title=self.title,
                subtitle=subtitle,
                step_names=planned_steps,
                cards=self.cards,
            )
        if visual:
            await self.launch_visual_session()
            assert self.page is not None
            await self.refresh_dashboard()
            await self.page.wait_for_timeout(400)
            return self.page
        # Web-only preview — no Playwright window
        return None  # type: ignore[return-value]

    async def set_step(
        self,
        index: int,
        *,
        status: str = "running",
        detail: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        if index < 0 or index >= len(self.steps):
            return
        if name:
            self.steps[index]["name"] = name
        if detail is not None:
            self.steps[index]["detail"] = detail
        self.steps[index]["status"] = status
        if status == "running":
            for i in range(index):
                if self.steps[i]["status"] in {"pending", "running"}:
                    self.steps[i]["status"] = "completed"
        if self.preview_session_id:
            preview_store.set_step(
                self.preview_session_id, index, status=status, detail=detail
            )
        if self.page:
            await self.refresh_dashboard()
        # Tiny yield so web preview clients can poll between steps (no heavy dwell)
        import asyncio as _asyncio

        await _asyncio.sleep(0.05)

    async def complete_step(self, index: int, detail: str = "Done") -> None:
        await self.set_step(index, status="completed", detail=detail)

    async def fail_step(self, index: int, detail: str) -> None:
        await self.set_step(index, status="failed", detail=detail)

    async def update_cards(self, cards: List[Dict[str, Any]]) -> None:
        self.cards = cards
        if self.preview_session_id:
            preview_store.update_cards(self.preview_session_id, cards)
        if self.page:
            await self.refresh_dashboard()

    async def refresh_dashboard(self) -> None:
        if not self.page:
            return
        await self.render_visual_dashboard(
            self.page,
            module_id=self.module_id,
            module_title=self.title,
            subtitle=self.subtitle,
            steps=self.steps,
            data_cards=self.cards,
            auto_close_ms=0,
        )

    async def hold_open(self, ms: Optional[int] = None) -> None:
        """Keep the window visible so the operator can read results."""
        hold = ms
        if hold is None:
            hold = int(os.getenv("SUITE_VISUAL_HOLD_MS", "4500"))
        if self.page and hold > 0:
            await self.page.wait_for_timeout(hold)

    @staticmethod
    async def render_visual_dashboard(
        page: Page,
        module_id: str,
        module_title: str,
        subtitle: str,
        steps: List[Dict[str, Any]],
        data_cards: Optional[List[Dict[str, Any]]] = None,
        auto_close_ms: int = 0,
    ) -> None:
        """Renders / refreshes the Wolf Suite live execution screen."""
        cards_html = ""
        if data_cards:
            for card in data_cards:
                label = html.escape(str(card.get("label", "")))
                value = html.escape(str(card.get("value", "")))
                sub = html.escape(str(card.get("sub", "")))
                cards_html += f"""
                <div style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 16px;">
                    <div style="color: #8b9bb4; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; font-weight: 600;">{label}</div>
                    <div style="color: #ffffff; font-size: 22px; font-weight: 700; margin-top: 4px;">{value}</div>
                    <div style="color: #c4a35a; font-size: 12px; margin-top: 4px;">{sub}</div>
                </div>
                """

        steps_html = ""
        for i, st in enumerate(steps, 1):
            status = st.get("status", "pending")
            badge_color = {
                "completed": "#2d6a4f",
                "running": "#c4a35a",
                "failed": "#9b2226",
                "pending": "#3d4f66",
            }.get(status, "#3d4f66")
            name = html.escape(str(st.get("name", "")))
            detail = html.escape(str(st.get("detail", "")))
            steps_html += f"""
            <div style="display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; background: rgba(26,35,50,0.6); border-radius: 10px; margin-bottom: 8px; border-left: 4px solid {badge_color};">
                <div>
                    <div style="color: #ffffff; font-size: 14px; font-weight: 600;">Step {i}: {name}</div>
                    <div style="color: #8b9bb4; font-size: 12px; margin-top: 2px;">{detail}</div>
                </div>
                <div style="background: {badge_color}; color: #ffffff; font-size: 10px; text-transform: uppercase; font-weight: 700; padding: 4px 10px; border-radius: 20px;">
                    {html.escape(status)}
                </div>
            </div>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>{html.escape(module_title)} — Wolf Suite Live</title>
            <style>
                body {{
                    margin: 0;
                    padding: 32px;
                    background: radial-gradient(ellipse at top, #1a2332 0%, #0c1118 55%);
                    color: #e8eef7;
                    font-family: system-ui, "Segoe UI", sans-serif;
                    box-sizing: border-box;
                    min-height: 100vh;
                }}
                .container {{ max-width: 1100px; margin: 0 auto; }}
                .header {{
                    display: flex; align-items: center; justify-content: space-between;
                    padding-bottom: 24px; border-bottom: 1px solid rgba(255,255,255,0.1);
                    margin-bottom: 28px;
                }}
                .brand {{ color: #c4a35a; font-size: 12px; text-transform: uppercase; letter-spacing: 3px; font-weight: 700; }}
                .title {{ color: #ffffff; font-size: 32px; font-weight: 800; margin: 6px 0 2px 0; }}
                .subtitle {{ color: #8b9bb4; font-size: 14px; }}
                .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 28px; }}
                .live-pill {{
                    display: flex; align-items: center; gap: 8px;
                    background: rgba(45,106,79,0.2); border: 1px solid #2d6a4f;
                    padding: 8px 16px; border-radius: 20px; color: #52b788;
                    font-weight: 700; font-size: 12px; text-transform: uppercase;
                }}
                .dot {{
                    width: 8px; height: 8px; background: #52b788; border-radius: 50%;
                    animation: pulse 1.5s infinite;
                }}
                @keyframes pulse {{
                    0% {{ opacity: 1; transform: scale(1); }}
                    50% {{ opacity: 0.4; transform: scale(0.8); }}
                    100% {{ opacity: 1; transform: scale(1); }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div>
                        <div class="brand">Wolf Suite · Live Module Runner</div>
                        <div class="title">{html.escape(module_title)}</div>
                        <div class="subtitle">{html.escape(subtitle)}</div>
                    </div>
                    <div class="live-pill"><div class="dot"></div> Live On-Screen</div>
                </div>
                {f'<div class="grid">{cards_html}</div>' if cards_html else ''}
                <div style="background: #121820; border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 24px;">
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px;">
                        <span style="color: #ffffff; font-size: 16px; font-weight: 700;">Pipeline Execution Stream</span>
                        <span style="color: #8b9bb4; font-size: 12px;">Module ID: {html.escape(module_id)}</span>
                    </div>
                    {steps_html}
                </div>
            </div>
        </body>
        </html>
        """
        await page.set_content(html_content)
        if auto_close_ms > 0:
            await page.wait_for_timeout(auto_close_ms)

    @staticmethod
    async def inject_hud_overlay(
        page: Page, module_title: str, current_step: str, status_text: str = "Active"
    ) -> None:
        """Injects a floating HUD overlay onto any active webpage tab."""
        hud_script = f"""
        (() => {{
            let hud = document.getElementById('wolf-suite-hud');
            if (!hud) {{
                hud = document.createElement('div');
                hud.id = 'wolf-suite-hud';
                hud.style.position = 'fixed';
                hud.style.bottom = '24px';
                hud.style.right = '24px';
                hud.style.zIndex = '999999';
                hud.style.background = 'rgba(12, 17, 24, 0.92)';
                hud.style.backdropFilter = 'blur(12px)';
                hud.style.border = '1px solid rgba(196, 163, 90, 0.4)';
                hud.style.borderRadius = '16px';
                hud.style.padding = '16px 20px';
                hud.style.color = '#e8eef7';
                hud.style.fontFamily = 'system-ui, sans-serif';
                hud.style.boxShadow = '0 12px 32px rgba(0,0,0,0.6)';
                hud.style.minWidth = '280px';
                document.body.appendChild(hud);
            }}
            hud.innerHTML = `
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                    <span style="color: #c4a35a; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px;">Wolf Suite · Visual Mode</span>
                    <span style="background: #2d6a4f; color: #fff; font-size: 9px; font-weight: 700; padding: 2px 8px; border-radius: 10px;">{status_text}</span>
                </div>
                <div style="color: #ffffff; font-size: 15px; font-weight: 700;">{module_title}</div>
                <div style="color: #8b9bb4; font-size: 12px; margin-top: 4px;">► {current_step}</div>
            `;
        }})();
        """
        try:
            await page.evaluate(hud_script)
        except Exception:
            pass

    @staticmethod
    async def perform_bezier_click(page: Page, selector: str) -> bool:
        """Moves cursor along a cubic Bezier curve to selector and clicks."""
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                box = await loc.bounding_box()
                if box:
                    target_x = box["x"] + box["width"] / 2
                    target_y = box["y"] + box["height"] / 2
                    start_x = target_x - random.uniform(150, 350)
                    start_y = target_y - random.uniform(100, 250)
                    path = humanization.bezier_path((start_x, start_y), (target_x, target_y))
                    for px, py in path:
                        await page.mouse.move(px, py)
                        await page.wait_for_timeout(int(humanization.mouse_step_delay() * 1000))
                    await loc.click()
                    return True
        except Exception:
            pass
        return False


async def run_visual_pipeline(
    module_id: str,
    title: str,
    subtitle: str,
    step_names: List[str],
    executor: Callable[[VisualRunner], Awaitable[Dict[str, Any]]],
    cards: Optional[List[Dict[str, Any]]] = None,
    preview_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Stream steps to the web preview store, optionally open a Chromium window,
    run `executor`, then hold/finish.
    """
    visual = os.getenv("SUITE_VISUAL", "true").lower() in {"1", "true", "yes"}
    runner = VisualRunner(module_id=module_id, title=title)
    try:
        await runner.begin(
            subtitle=subtitle,
            planned_steps=step_names,
            cards=cards,
            preview_session_id=preview_session_id,
        )
        result = await executor(runner)
        for i, st in enumerate(runner.steps):
            if st["status"] in {"pending", "running"}:
                await runner.complete_step(i, st.get("detail") or "Finished")
        runner.subtitle = "Execution complete — review results on screen"
        if runner.page:
            await runner.refresh_dashboard()
            await runner.hold_open()
        if runner.preview_session_id:
            preview_store.complete(runner.preview_session_id, result)
        return {
            "visual": visual,
            "web_preview": True,
            "preview_session_id": runner.preview_session_id,
            "module": module_id,
            **result,
        }
    except Exception as exc:
        err_txt = f"{type(exc).__name__}: {exc}".strip()
        if err_txt.endswith(":") or not str(exc):
            err_txt = f"{type(exc).__name__}: {exc!r}"
        try:
            already_failed = any(s.get("status") == "failed" for s in runner.steps)
            if not already_failed:
                running = next(
                    (i for i, s in enumerate(runner.steps) if s["status"] == "running"),
                    max(0, len(runner.steps) - 1),
                )
                await runner.fail_step(max(0, running), err_txt[:240])
            if runner.page:
                await runner.hold_open()
            if runner.preview_session_id:
                preview_store.fail(runner.preview_session_id, err_txt)
        except Exception:
            if runner.preview_session_id:
                try:
                    preview_store.fail(runner.preview_session_id, err_txt)
                except Exception:
                    pass
        return {
            "visual": visual,
            "web_preview": True,
            "preview_session_id": runner.preview_session_id,
            "module": module_id,
            "status": "ERROR",
            "error": err_txt,
        }
    finally:
        await runner.close()
