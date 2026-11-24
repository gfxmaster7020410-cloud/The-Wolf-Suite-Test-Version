"""
Open the Gemini research chat and save full conversation text.

1) Run this script
2) Sign in to Google/Gemini in the opened browser (same profile as Facebook)
3) Wait — it will scroll and save to data/gemini_research_extract.txt

  python scripts/extract_gemini_research.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = "https://gemini.google.com/app/4ff8639a3677362f"
PROFILE = ROOT / "data" / "browser_profiles" / "facebook"
OUT = ROOT / "data" / "gemini_research_extract.txt"
LOGIN_WAIT_MS = 180_000  # 3 minutes to sign in if needed


async def main() -> int:
    from playwright.async_api import async_playwright

    PROFILE.mkdir(parents=True, exist_ok=True)
    print(f"Opening research chat:\n  {URL}")
    print(f"Profile: {PROFILE}")
    print("Agar Sign in dikhe to browser mein login karein (3 min wait)...")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1365, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(4000)

        body = (await page.locator("body").inner_text()).lower()
        if "sign in" in body and "conversation with gemini" in body and len(body) < 800:
            print("Login required — browser mein Sign in complete karein...")
            await page.wait_for_timeout(LOGIN_WAIT_MS)
            await page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
            await page.wait_for_timeout(5000)

        # Scroll conversation to load history
        for _ in range(15):
            await page.mouse.wheel(0, 2500)
            await page.wait_for_timeout(600)
        for _ in range(10):
            await page.mouse.wheel(0, -2500)
            await page.wait_for_timeout(400)

        text = await page.locator("body").inner_text()
        OUT.write_text(text, encoding="utf-8")
        print(f"\nSaved {len(text)} chars → {OUT}")
        if len(text) < 500 or "sign in" in text.lower()[:400]:
            print("WARNING: Chat abhi bhi empty/login page lag raha hai.")
            print("Login ke baad script dobara chalayein, YA chat text yahan paste karein.")
        else:
            print("OK — research extract ready. Ab main isse Module 1 steps mein map kar sakta hoon.")
        await ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
