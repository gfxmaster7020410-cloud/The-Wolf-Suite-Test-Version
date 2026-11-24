"""
Module 1 — Gemini API client (vision → comment + reaction).

Sends a Facebook screenshot and returns one short comment (no hashtags).
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from services.module_1_feed_observer.gemini_bridge import parse_gemini_reply

# User-requested prompt (Urdu) + reaction for Like/React step
PROMPT = (
    "اس پوسٹ کے لیے ایک انگیجمنٹ والا کمنٹ لکھو۔ "
    "# استعمال نہ کرنا۔ صرف 1 لائن کا کمنٹ چاہیے۔ "
    "Also choose the best Facebook reaction for this post: LIKE, LOVE, CARE, HAHA, or WOW. "
    'Return ONLY JSON: {"comment":"one line","reaction":"LIKE"}'
)

VALID_REACTIONS = {"LIKE", "LOVE", "CARE", "HAHA", "WOW"}


def _clean_comment(text: str) -> str:
    text = re.sub(r"#\w+", "", (text or "")).strip()
    text = text.strip('"').strip("'")
    # Keep first line only
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line[:280]


def parse_api_reply(raw: str) -> Dict[str, Any]:
    """Parse Gemini text into {comment, reaction}."""
    text = (raw or "").strip()
    reaction = os.getenv("M1_DEFAULT_REACTION", "LIKE").upper()
    comment = ""

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    blob = fence.group(1) if fence else None
    if not blob:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        blob = m.group(0) if m else None
    if blob:
        try:
            data = json.loads(blob)
            comment = _clean_comment(str(data.get("comment", "")))
            r = str(data.get("reaction", reaction)).upper().strip()
            if r in VALID_REACTIONS:
                reaction = r
        except json.JSONDecodeError:
            pass

    if not comment:
        comment = _clean_comment(text)

    if not comment:
        comment = "Really nice — thanks for sharing!"

    if reaction not in VALID_REACTIONS:
        reaction = "LIKE"

    return {"comment": comment, "reaction": reaction}


async def generate_engagement(screenshot_path: str) -> Dict[str, Any]:
    """
    Send screenshot to Gemini API.
    Returns {"comment": str, "reaction": str, "source": "gemini_api"}.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("MOD1_AI_API_KEY") or ""
    if not api_key or api_key.startswith("sk-proj-your") or api_key.startswith("your"):
        raise RuntimeError("GEMINI_API_KEY missing — set it in .env")

    model = os.getenv("GEMINI_API_MODEL") or os.getenv("MOD1_AI_MODEL", "gemini-2.0-flash")
    base = os.getenv(
        "GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")

    path = Path(screenshot_path)
    if not path.exists():
        raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    url = f"{base}/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 120},
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
    decision = parse_api_reply(text)
    decision["source"] = "gemini_api"
    return decision

# --------------------------------------------------------------------------
# PHASE-03 — parameterized REST transport for the AIEngine facade.
# Legacy generate_engagement() above stays untouched for bot.py parity.
# --------------------------------------------------------------------------

async def generate_engagement_v2(
    screenshot_path: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    prompt: str = "",
    structured: bool = True,
    strip_tags: bool = True,
) -> Dict[str, Any]:
    """
    Send a screenshot to the Gemini REST API using explicitly supplied
    credentials/model/prompt (dashboard settings override env). Returns
    {comment, reaction, confidence, source: "gemini_api"} via the single
    shared parser (parse_gemini_reply).
    """
    key = (api_key or "").strip()
    if not key:
        key = os.getenv("GEMINI_API_KEY") or os.getenv("MOD1_AI_API_KEY") or ""
    if not key or key.startswith(("sk-proj-your", "your")):
        raise RuntimeError(
            "No usable Gemini API key — set it in Module 1 settings or .env"
        )

    mdl = (model or "").strip() or os.getenv("GEMINI_API_MODEL") or os.getenv(
        "MOD1_AI_MODEL", "gemini-2.0-flash"
    )
    base = os.getenv(
        "GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")

    path = Path(screenshot_path)
    if not path.exists():
        raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    url = f"{base}/models/{mdl}:generateContent?key={key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt or PROMPT},
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 200},
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
    decision = parse_gemini_reply(text, structured=structured, strip_tags=strip_tags)
    decision["source"] = "gemini_api"
    return decision
