"""
Module 2: AI Media Processing, Multi-Platform Distribution & Engagement Engine.

Phase 1 — Media ingestion, aspect-ratio validation, optional compression
Phase 2 — Dual-stage Gemini/vision caption generation via Universal AI Gateway
Phase 3 — Cross-post to Facebook, Instagram, Threads (Graph API when tokens set)
Phase 4 — Group share loop with humanized 45–120s delays
Phase 5 — Seed comment + discussion hooks
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from services.ai_gateway.gateway import UniversalAIGateway
from services.security_governance.pii_redactor import PIIRedactor
from services.shared.humanization import humanization


class MediaPreFlightValidator:
    """
    Phase 1: Validates aspect ratios (1:1 feed, 9:16 vertical/reels, 16:9 landscape)
    and formats (.jpg, .png, .mp4, .mov), applying dynamic compression if needed.
    """

    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4", ".mov"}
    MAX_IMAGE_BYTES = int(os.getenv("M2_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))

    @classmethod
    def validate_and_stage_media(cls, media_path: str, compress: bool = True) -> Dict[str, Any]:
        if not os.path.exists(media_path):
            raise FileNotFoundError(f"Media file not found: {media_path}")

        ext = os.path.splitext(media_path)[1].lower()
        if ext not in cls.ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported media format: {ext}")

        is_video = ext in {".mp4", ".mov"}
        aspect_ratio_type = "unknown"
        staged_path = media_path
        compressed = False
        width = height = None

        if not is_video:
            with Image.open(media_path) as img:
                width, height = img.size
                ratio = width / float(height or 1)
                if 0.9 <= ratio <= 1.1:
                    aspect_ratio_type = "1:1"
                elif 0.5 <= ratio <= 0.65 or ratio < 0.8:
                    aspect_ratio_type = "9:16"
                elif 1.6 <= ratio <= 1.9 or ratio >= 1.4:
                    aspect_ratio_type = "16:9"
                else:
                    aspect_ratio_type = "custom"

                if compress and (
                    os.path.getsize(media_path) > cls.MAX_IMAGE_BYTES
                    or max(width, height) > 2048
                    or img.mode not in {"RGB", "L"}
                ):
                    staged_path = cls._compress_image(img, media_path)
                    compressed = staged_path != media_path

        return {
            "media_path": staged_path,
            "original_path": media_path,
            "is_video": is_video,
            "aspect_ratio_type": aspect_ratio_type,
            "width": width,
            "height": height,
            "compressed": compressed,
            "status": "VALIDATED",
        }

    @classmethod
    def _compress_image(cls, img: Image.Image, source_path: str) -> str:
        out_dir = Path(os.getenv("M2_STAGING_DIR", Path(source_path).parent / ".wolf_staging"))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{Path(source_path).stem}_compressed.jpg"

        work = img.convert("RGB")
        max_edge = 2048
        if max(work.size) > max_edge:
            work.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        quality = 85
        work.save(out_path, format="JPEG", quality=quality, optimize=True)
        while out_path.stat().st_size > cls.MAX_IMAGE_BYTES and quality > 40:
            quality -= 10
            work.save(out_path, format="JPEG", quality=quality, optimize=True)
        return str(out_path)


class Module2MediaEngine:
    """
    Module 2: AI Media Processing, Multi-Platform Distribution & Engagement Engine.
    Executes Phase 1-5 pipeline across Facebook, Instagram, and Threads.
    Human-like pacing: caps, inter-platform delays, group-share dwell, Spintax seed comments.
    """

    GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v19.0")

    def __init__(self, engine=None):
        self.ai_gateway = UniversalAIGateway()
        self.pii_redactor = PIIRedactor()
        self.human = engine or humanization
        self.access_token = os.getenv("META_PAGE_ACCESS_TOKEN") or os.getenv("FB_PAGE_ACCESS_TOKEN", "")
        self.page_id = os.getenv("META_PAGE_ID") or os.getenv("FB_PAGE_ID", "")
        self.ig_user_id = os.getenv("IG_USER_ID", "")
        self.threads_user_id = os.getenv("THREADS_USER_ID", "")

    async def execute_media_pipeline(
        self,
        media_path: str,
        target_platforms: Optional[List[str]] = None,
        joined_groups: Optional[List[str]] = None,
        mentions: Optional[List[str]] = None,
        collab_user_ids: Optional[List[str]] = None,
        pin_highlight: bool = True,
        account_id: str = "media_main",
    ) -> Dict[str, Any]:
        target_platforms = target_platforms or ["facebook", "instagram", "threads"]
        joined_groups = joined_groups or []

        cap = self.human.can_act(account_id)
        if not cap["allowed"]:
            return {
                "status": "THROTTLED_HUMAN_CAP",
                "cap": cap,
                "humanization": self.human.snapshot(),
            }

        # Phase 1
        media_info = MediaPreFlightValidator.validate_and_stage_media(media_path)

        # Human "review media" dwell before AI / publish
        await self.human.async_apply_delay(self.human.reading_dwell(word_count=30, has_image=True))

        # Phase 2 — Dual-stage vision AI
        stage1_candidates = await self._gemini_stage1_generate_candidates(media_info)
        await self.human.async_apply_delay(self.human.action_delay(), fast_cap=0.25)
        final_copy = await self._gemini_stage2_select_best_copy(stage1_candidates, media_info)
        sanitized_copy = self.pii_redactor.sanitize_prompt(final_copy["description"])

        # Phase 3 — Multi-platform publishing with human gaps between platforms
        published_posts: Dict[str, Any] = {}
        human_timing: List[Dict[str, Any]] = []
        for i, platform in enumerate(target_platforms):
            if i > 0:
                d = self.human.inter_platform_delay()
                applied = await self.human.async_apply_delay(d, fast_cap=0.4)
                human_timing.append({"between": platform, "planned_sec": round(d, 2), "applied_sec": round(applied, 2)})
            published_posts[platform] = await self._publish_to_platform(
                platform=platform,
                media_info=media_info,
                description=sanitized_copy,
                hashtags=final_copy.get("hashtags", ""),
                mentions=mentions or [],
                collab_user_ids=collab_user_ids or [],
            )
            self.human.record_action(account_id)
            cap = self.human.can_act(account_id)
            if not cap["allowed"]:
                break

        # Phase 4 — Group distribution
        fb_post = published_posts.get("facebook") or {}
        fb_post_id = fb_post.get("post_id") if isinstance(fb_post, dict) else str(fb_post)
        fb_post_url = fb_post.get("permalink") if isinstance(fb_post, dict) else None
        fb_post_url = fb_post_url or f"https://facebook.com/{fb_post_id or 'live_post'}"
        group_share_results = await self._distribute_to_groups(
            fb_post_url, joined_groups, account_id=account_id
        )

        # Phase 5 — Engagement hooks
        seed_wait = self.human.seed_comment_delay()
        seed_applied = await self.human.async_apply_delay(seed_wait, fast_cap=0.35)
        human_timing.append(
            {"seed_comment_wait": True, "planned_sec": round(seed_wait, 2), "applied_sec": round(seed_applied, 2)}
        )
        seed_comment = await self._seed_comment(fb_post_id, sanitized_copy)
        if seed_comment.get("status") in {"POSTED", "SIMULATED"}:
            self.human.record_action(account_id)
        discussion_hooks = await self._generate_discussion_hooks(fb_post_url, sanitized_copy)
        highlight = None
        if pin_highlight and fb_post_id:
            await self.human.async_apply_delay(self.human.action_delay(), fast_cap=0.25)
            highlight = await self._pin_story_highlight(fb_post_id)

        return {
            "status": "SUCCESS",
            "media_info": media_info,
            "final_copy": sanitized_copy,
            "hashtags": final_copy.get("hashtags", ""),
            "stage1_candidates": stage1_candidates,
            "published_posts": published_posts,
            "group_shares": group_share_results,
            "seed_comment": seed_comment,
            "discussion_hooks": discussion_hooks,
            "highlight": highlight,
            "humanization": {
                "profile": self.human.profile.profile_name,
                "cap": self.human.can_act(account_id),
                "timing": human_timing,
                "simulate_fast": self.human.simulate_fast(),
            },
        }

    async def _gemini_stage1_generate_candidates(self, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Stage 1: Generates 3–5 caption variations and hashtag blocks from visual assets."""
        prompt = (
            "You are a social media copywriter. Given this media asset metadata, "
            "generate exactly 4 caption candidates optimized for engagement. "
            f"Aspect: {media_info.get('aspect_ratio_type')}. "
            "Return ONLY JSON array: "
            '[{"caption":"...","hashtags":"#a #b","engagement_score":0.0-1.0,"cta_strength":0.0-1.0}]'
        )
        try:
            content = await self._vision_or_text(prompt, media_info.get("media_path"), media_info.get("is_video"))
            candidates = self._parse_json_array(content)
            if candidates:
                return candidates[:5]
        except Exception as exc:
            print(f"[Module 2] Stage1 AI fallback: {exc}")

        return [
            {
                "caption": "Transform your workflow with autonomous social intelligence.",
                "hashtags": "#Automation #AI #SocialMedia",
                "engagement_score": 0.82,
                "cta_strength": 0.7,
            },
            {
                "caption": "Stop spending hours on manual posts — let AI scale your audience.",
                "hashtags": "#Growth #AIMarketing #Enterprise",
                "engagement_score": 0.91,
                "cta_strength": 0.88,
            },
            {
                "caption": "The future of multi-platform engagement is here. Ready to scale?",
                "hashtags": "#WolfSuite #Automation #Scale",
                "engagement_score": 0.85,
                "cta_strength": 0.8,
            },
            {
                "caption": "One asset. Three platforms. Zero busywork.",
                "hashtags": "#ContentOps #AI #Productivity",
                "engagement_score": 0.79,
                "cta_strength": 0.75,
            },
        ]

    async def _gemini_stage2_select_best_copy(
        self, candidates: List[Dict[str, Any]], media_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Stage 2: Re-evaluate candidates; return exactly 1 best description."""
        prompt = (
            "Select the single best social caption for virality and CTA strength. "
            f"Aspect ratio: {media_info.get('aspect_ratio_type')}. Candidates:\n"
            f"{json.dumps(candidates)}\n"
            'Return ONLY JSON: {"description":"...","hashtags":"...","reason":"..."}'
        )
        try:
            content = await self._chat(prompt)
            data = self._parse_json_object(content)
            if data and data.get("description"):
                return {
                    "description": data["description"],
                    "hashtags": data.get("hashtags", candidates[0].get("hashtags", "")),
                    "reason": data.get("reason", "ai_selected"),
                }
        except Exception as exc:
            print(f"[Module 2] Stage2 AI fallback: {exc}")

        best = max(
            candidates,
            key=lambda c: float(c.get("engagement_score", 0)) * 0.6
            + float(c.get("cta_strength", 0)) * 0.4,
        )
        return {
            "description": best.get("caption", ""),
            "hashtags": best.get("hashtags", "#Automation #AI"),
            "reason": "heuristic_max_score",
        }

    async def _publish_to_platform(
        self,
        platform: str,
        media_info: Dict[str, Any],
        description: str,
        hashtags: str,
        mentions: List[str],
        collab_user_ids: List[str],
    ) -> Dict[str, Any]:
        caption = description
        if hashtags:
            caption = f"{description}\n\n{hashtags}"
        if mentions:
            caption = f"{caption}\n" + " ".join(f"@{m.lstrip('@')}" for m in mentions)

        dry = os.getenv("M2_DRY_RUN", "true").lower() in {"1", "true", "yes"}
        if dry or not self.access_token:
            post_id = f"pub_{platform}_{random.randint(10000, 99999)}"
            return {
                "post_id": post_id,
                "platform": platform,
                "mode": "simulated",
                "permalink": f"https://{platform}.com/{post_id}",
                "mentions": mentions,
                "collaborators": collab_user_ids,
            }

        try:
            if platform == "facebook":
                return await self._publish_facebook(media_info, caption, collab_user_ids)
            if platform == "instagram":
                return await self._publish_instagram(media_info, caption, collab_user_ids)
            if platform == "threads":
                return await self._publish_threads(media_info, caption)
        except Exception as exc:
            return {
                "post_id": None,
                "platform": platform,
                "mode": "error",
                "error": str(exc),
            }

        return {"post_id": None, "platform": platform, "mode": "unsupported"}

    async def _publish_facebook(
        self, media_info: Dict[str, Any], caption: str, collab_user_ids: List[str]
    ) -> Dict[str, Any]:
        import httpx

        page_id = self.page_id or "me"
        base = f"https://graph.facebook.com/{self.GRAPH_VERSION}/{page_id}"
        params = {"access_token": self.access_token, "message": caption}
        if collab_user_ids:
            params["tags"] = json.dumps([{"tag_uid": uid} for uid in collab_user_ids])

        async with httpx.AsyncClient(timeout=90.0) as client:
            if media_info.get("is_video"):
                with open(media_info["media_path"], "rb") as fh:
                    files = {"source": fh}
                    res = await client.post(f"{base}/videos", data=params, files=files)
            else:
                with open(media_info["media_path"], "rb") as fh:
                    files = {"source": fh}
                    res = await client.post(f"{base}/photos", data=params, files=files)
            res.raise_for_status()
            body = res.json()
        post_id = body.get("id") or body.get("post_id")
        return {
            "post_id": post_id,
            "platform": "facebook",
            "mode": "live",
            "permalink": f"https://facebook.com/{post_id}",
            "raw": body,
        }

    async def _publish_instagram(
        self, media_info: Dict[str, Any], caption: str, collab_user_ids: List[str]
    ) -> Dict[str, Any]:
        import httpx

        if not self.ig_user_id:
            raise RuntimeError("IG_USER_ID not configured")
        # Container → publish flow requires a publicly reachable image URL in production.
        # Support optional public URL override for live Graph calls.
        image_url = os.getenv("M2_PUBLIC_MEDIA_URL")
        if not image_url:
            raise RuntimeError("M2_PUBLIC_MEDIA_URL required for live Instagram publish")

        base = f"https://graph.facebook.com/{self.GRAPH_VERSION}/{self.ig_user_id}"
        async with httpx.AsyncClient(timeout=90.0) as client:
            create = await client.post(
                f"{base}/media",
                data={
                    "image_url": image_url,
                    "caption": caption,
                    "access_token": self.access_token,
                    **({"collaborators": json.dumps(collab_user_ids)} if collab_user_ids else {}),
                },
            )
            create.raise_for_status()
            creation_id = create.json().get("id")
            publish = await client.post(
                f"{base}/media_publish",
                data={"creation_id": creation_id, "access_token": self.access_token},
            )
            publish.raise_for_status()
            body = publish.json()
        return {
            "post_id": body.get("id"),
            "platform": "instagram",
            "mode": "live",
            "permalink": f"https://instagram.com/p/{body.get('id')}",
            "raw": body,
        }

    async def _publish_threads(self, media_info: Dict[str, Any], caption: str) -> Dict[str, Any]:
        import httpx

        user_id = self.threads_user_id or self.ig_user_id
        if not user_id:
            raise RuntimeError("THREADS_USER_ID not configured")
        image_url = os.getenv("M2_PUBLIC_MEDIA_URL")
        base = f"https://graph.threads.net/{self.GRAPH_VERSION}/{user_id}"
        async with httpx.AsyncClient(timeout=90.0) as client:
            payload: Dict[str, Any] = {
                "media_type": "IMAGE" if not media_info.get("is_video") else "VIDEO",
                "text": caption,
                "access_token": self.access_token,
            }
            if image_url:
                payload["image_url" if not media_info.get("is_video") else "video_url"] = image_url
            create = await client.post(f"{base}/threads", data=payload)
            create.raise_for_status()
            creation_id = create.json().get("id")
            publish = await client.post(
                f"{base}/threads_publish",
                data={"creation_id": creation_id, "access_token": self.access_token},
            )
            publish.raise_for_status()
            body = publish.json()
        return {
            "post_id": body.get("id"),
            "platform": "threads",
            "mode": "live",
            "permalink": f"https://threads.net/post/{body.get('id')}",
            "raw": body,
        }

    async def _distribute_to_groups(
        self, post_url: str, groups: List[str], account_id: str = "media_main"
    ) -> List[Dict[str, Any]]:
        """Phase 4: Shares post URL across groups with humanized delays (45–120s)."""
        import httpx

        share_logs: List[Dict[str, Any]] = []
        live = bool(self.access_token) and os.getenv("M2_DRY_RUN", "true").lower() not in {
            "1",
            "true",
            "yes",
        }
        for group_id in groups:
            cap = self.human.can_act(account_id)
            if not cap["allowed"]:
                share_logs.append(
                    {"group_id": group_id, "status": "THROTTLED_HUMAN_CAP", "cap": cap}
                )
                break

            delay = self.human.group_share_delay()
            applied = await self.human.async_apply_delay(delay, fast_cap=0.4)
            result: Dict[str, Any] = {
                "group_id": group_id,
                "status": "SHARED",
                "post_url": post_url,
                "delay_planned_sec": round(delay, 2),
                "delay_applied_sec": round(applied, 2),
                "simulated_fast": self.human.simulate_fast(),
                "mode": "simulated",
            }
            if live:
                try:
                    # Spintax share message so each group post isn't identical
                    share_msg = self.human.spintax_expand(
                        "{Sharing this with the group:|Thought this belonged here:|Quick share for everyone:} {url}",
                        url=post_url,
                    )
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        res = await client.post(
                            f"https://graph.facebook.com/{self.GRAPH_VERSION}/{group_id}/feed",
                            data={
                                "link": post_url,
                                "message": share_msg,
                                "access_token": self.access_token,
                            },
                        )
                        res.raise_for_status()
                        result["mode"] = "live"
                        result["message"] = share_msg
                        result["raw"] = res.json()
                except Exception as exc:
                    result["status"] = "ERROR"
                    result["error"] = str(exc)
            self.human.record_action(account_id)
            share_logs.append(result)
        return share_logs

    async def _seed_comment(self, post_id: Optional[str], copy: str) -> Dict[str, Any]:
        if not post_id:
            return {"status": "SKIPPED", "reason": "no_post_id"}
        # Spintax seed comment (human variation) rather than a fixed CTA
        message = self.human.spintax_seed_comment()
        if copy:
            message = f"{message}\n\n({copy[:72]}…)" if len(copy) > 72 else f"{message}\n\n{copy}"
        message = self.pii_redactor.sanitize_prompt(message)
        typing_est = self.human.estimated_typing_duration(message)
        await self.human.async_apply_delay(min(typing_est, 8.0), fast_cap=0.3)

        if not self.access_token or os.getenv("M2_DRY_RUN", "true").lower() in {"1", "true", "yes"}:
            return {
                "status": "SIMULATED",
                "message": message,
                "post_id": post_id,
                "typing_est_sec": round(typing_est, 2),
            }
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    f"https://graph.facebook.com/{self.GRAPH_VERSION}/{post_id}/comments",
                    data={"message": message, "access_token": self.access_token},
                )
                res.raise_for_status()
                return {
                    "status": "POSTED",
                    "raw": res.json(),
                    "message": message,
                    "typing_est_sec": round(typing_est, 2),
                }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc), "message": message}

    async def _pin_story_highlight(self, post_id: str) -> Dict[str, Any]:
        # Graph API highlight pinning varies by product surface; record intent + optional live call.
        if not self.access_token or os.getenv("M2_DRY_RUN", "true").lower() in {"1", "true", "yes"}:
            return {"status": "SIMULATED", "post_id": post_id, "action": "pin_highlight"}
        return {"status": "QUEUED", "post_id": post_id, "action": "pin_highlight"}

    async def _generate_discussion_hooks(self, post_url: str, copy: str) -> List[str]:
        """Phase 5: Prompt AI for 5 high-converting discussion hooks."""
        prompt = (
            "Generate exactly 5 short high-converting discussion reply hooks for this social post. "
            f"Post URL: {post_url}. Caption: {copy}\n"
            'Return ONLY JSON: {"hooks":["...","...","...","...","..."]}'
        )
        try:
            content = await self._chat(prompt)
            data = self._parse_json_object(content)
            hooks = (data or {}).get("hooks") or []
            if isinstance(hooks, list) and len(hooks) >= 3:
                return [str(h) for h in hooks[:5]]
        except Exception as exc:
            print(f"[Module 2] Hooks AI fallback: {exc}")

        return [
            "What feature would help your team most?",
            "How many hours do you currently spend on manual social posts?",
            "Drop your thoughts below!",
            "Have you tried multi-platform AI scheduling yet?",
            "Tag a founder who needs this automation!",
        ]

    async def _chat(self, prompt: str) -> str:
        raw = await self.ai_gateway.chat_completion(
            "module_2",
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=700,
        )
        return raw.get("choices", [{}])[0].get("message", {}).get("content", "")

    async def _vision_or_text(self, prompt: str, media_path: Optional[str], is_video: bool) -> str:
        if is_video or not media_path or not Path(media_path).exists():
            return await self._chat(prompt + f"\nMedia path: {media_path}")

        try:
            b64 = base64.b64encode(Path(media_path).read_bytes()).decode("ascii")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ]
            raw = await self.ai_gateway.chat_completion(
                "module_2", messages, temperature=0.65, max_tokens=900
            )
            return raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception:
            return await self._chat(prompt + f"\n(Image unavailable for vision; path={media_path})")

    @staticmethod
    def _parse_json_array(text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL | re.I)
        blob = fence.group(1) if fence else None
        if not blob:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            blob = m.group(0) if m else None
        if not blob:
            return []
        data = json.loads(blob)
        return data if isinstance(data, list) else []

    @staticmethod
    def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.I)
        blob = fence.group(1) if fence else None
        if not blob:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            blob = m.group(0) if m else None
        if not blob:
            return None
        data = json.loads(blob)
        return data if isinstance(data, dict) else None
