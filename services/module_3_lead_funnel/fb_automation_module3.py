"""
Module 3: Lead Capture, Conversational Funnels & Anti-Detection Sentinel.

Spec §5:
  - Comment-to-DM trigger (private Graph message + public Spintax reply)
  - Anti-detection: Gaussian delays, Spintax, 14-day warm-up caps
  - Ad Creative Fatigue Guard (Frequency > 3.5 AND CTR < 1.0% → pause)
  - Auto-Scale Rule (+15% daily budget when 48h ROAS exceeds target)
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Dict, List, Optional

import requests

from services.shared.humanization import HumanizationEngine, humanization


DEFAULT_TRIGGERS = ["pricing", "send link", "info", "price", "details", "dm me"]


class FBAutomationModule3:
    """
    Lead funnel + Ad Guard over Meta Graph API.
    Set M3_DRY_RUN=true to skip live Graph mutations (local/dev).
    """

    GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v19.0")

    def __init__(
        self,
        page_access_token: Optional[str] = None,
        page_id: Optional[str] = None,
        engine: Optional[HumanizationEngine] = None,
    ):
        self.access_token = page_access_token or os.getenv("META_PAGE_ACCESS_TOKEN", "")
        self.page_id = page_id or os.getenv("META_PAGE_ID", "")
        self.graph_url = f"https://graph.facebook.com/{self.GRAPH_VERSION}"
        self.human = engine or humanization
        self.dry_run = os.getenv("M3_DRY_RUN", "true").lower() in {"1", "true", "yes"}
        self.default_roas_target = float(os.getenv("M3_ROAS_TARGET", "2.5"))
        self.budget_scale_pct = float(os.getenv("M3_BUDGET_SCALE_PCT", "15"))
        self.converted_leads: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ helpers
    def _auth_params(self) -> Dict[str, str]:
        return {"access_token": self.access_token}

    def _simulate_human_delay(
        self, min_sec: Optional[float] = None, max_sec: Optional[float] = None
    ) -> float:
        if min_sec is not None and max_sec is not None:
            d = random.uniform(min_sec, max_sec)
            return self.human.apply_delay(d)
        # Spec: stochastic 1.5s–5.5s band via profile action_delay
        return self.human.apply_delay(self.human.action_delay())

    def _post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.graph_url}/{path.lstrip('/')}"
        payload = {**(data or {}), "access_token": self.access_token}
        if self.dry_run or not self.access_token:
            return {"dry_run": True, "path": path, "payload": {k: v for k, v in payload.items() if k != "access_token"}}
        res = requests.post(url, data=payload, timeout=30)
        try:
            return res.json()
        except Exception:
            return {"status_code": res.status_code, "text": res.text[:500]}

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.graph_url}/{path.lstrip('/')}"
        q = {**(params or {}), "access_token": self.access_token}
        if self.dry_run and not self.access_token:
            return {"data": []}
        res = requests.get(url, params=q, timeout=30)
        try:
            return res.json()
        except Exception:
            return {"data": [], "error": {"message": res.text[:300]}}

    # -------------------------------------------------------- comment → DM funnel
    def scan_post_comments_and_convert(
        self,
        post_id: str,
        trigger_keywords: Optional[List[str]] = None,
        lead_link: str = "",
        account_id: str = "page_default",
    ) -> Dict[str, Any]:
        """
        Dual-action response on keyword match:
          1) Private outreach via /me/messages with lead magnet link
          2) Public Spintax confirmation reply on the comment
        """
        keywords = [k.lower() for k in (trigger_keywords or DEFAULT_TRIGGERS)]
        lead_link = lead_link or os.getenv("M3_LEAD_LINK", "https://example.com/offer")

        cap = self.human.can_act(account_id)
        if not cap["allowed"]:
            return {"status": "THROTTLED_HUMAN_CAP", "cap": cap, "converted": 0}

        # Warm-up curve (14-day) is enforced inside HumanizationEngine caps
        warm = self.human.warmup_status(account_id) if hasattr(self.human, "warmup_status") else {}

        body = self._get(f"{post_id}/comments", {"fields": "from,message,id"})
        if body.get("error"):
            return {"status": "ERROR", "error": body["error"], "converted": 0}

        converted = 0
        actions: List[Dict[str, Any]] = []

        for comment in body.get("data", []):
            cap = self.human.can_act(account_id)
            if not cap["allowed"]:
                break

            text = (comment.get("message") or "").lower()
            sender = comment.get("from") or {}
            sender_id = sender.get("id")
            sender_name = sender.get("name") or "there"
            comment_id = comment.get("id")

            if not any(kw in text for kw in keywords):
                continue

            self._simulate_human_delay()

            dm_msg = (
                f"Hi {sender_name}! Thanks for reaching out — "
                f"here is everything you asked for: {lead_link}"
            )
            dm_res = self.send_private_dm(sender_id, dm_msg)

            p = self.human.profile
            self.human.apply_delay(
                random.uniform(p.dm_to_reply_delay_min, p.dm_to_reply_delay_max)
            )

            reply_text = self.human.spintax_reply(sender_name)
            # Spec template flavor when engine returns empty
            if not reply_text:
                reply_text = self.human.spintax_expand(
                    "Hey {name}, just sent the details to your inbox! "
                    "{Check your messages.|Have a look in Messenger.|Inboxed you.}",
                    name=sender_name,
                )
            # Pace public reply like typing
            self.human.apply_delay(
                min(self.human.estimated_typing_duration(reply_text), 6.0),
                fast_cap=0.3,
            )
            reply_res = self.reply_to_comment(comment_id, reply_text)
            self.human.record_action(account_id)
            converted += 1

            lead = {
                "external_user_id": sender_id,
                "display_name": sender_name,
                "source_post_id": post_id,
                "comment_id": comment_id,
                "status": "contacted",
                "lead_link": lead_link,
            }
            self.converted_leads.append(lead)
            actions.append({"lead": lead, "dm": dm_res, "public_reply": reply_res})

        return {
            "status": "OK",
            "converted": converted,
            "actions": actions,
            "warmup": warm,
            "dry_run": self.dry_run,
            "cap": self.human.can_act(account_id),
            "humanization": {
                "profile": self.human.profile.profile_name,
                "simulate_fast": self.human.simulate_fast(),
            },
        }

    @staticmethod
    def _spintax(template: str, name: str) -> str:
        import re

        def pick(m: re.Match) -> str:
            return random.choice(m.group(1).split("|"))

        text = re.sub(r"\{([^{}]+)\}", pick, template)
        return text.replace("{name}", name).replace("{{name}}", name)

    def send_private_dm(self, recipient_id: str, text_message: str) -> Dict[str, Any]:
        """Meta Graph `/me/messages` private outreach."""
        if not recipient_id:
            return {"error": "missing_recipient"}
        if self.dry_run or not self.access_token:
            return {
                "dry_run": True,
                "recipient_id": recipient_id,
                "message": text_message,
            }
        url = f"{self.graph_url}/me/messages"
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text_message},
            "access_token": self.access_token,
        }
        res = requests.post(url, json=payload, timeout=30)
        try:
            return res.json()
        except Exception:
            return {"status_code": res.status_code, "text": res.text[:500]}

    def reply_to_comment(self, comment_id: str, message: str) -> Dict[str, Any]:
        """Public Spintax confirmation reply on the comment thread."""
        if not comment_id:
            return {"error": "missing_comment_id"}
        return self._post(f"{comment_id}/comments", {"message": message})

    # ----------------------------------------------------------- Ad Guard / Scale
    def monitor_ad_fatigue(
        self,
        ad_account_id: str,
        ctr_threshold: float = 1.0,
        freq_threshold: float = 3.5,
    ) -> Dict[str, Any]:
        """
        Fatigue Rule: pause ad if Frequency > 3.5 AND CTR < 1.0%.
        """
        ad_account_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        body = self._get(
            f"{ad_account_id}/insights",
            {
                "level": "ad",
                "fields": "ad_id,ad_name,ctr,frequency,spend",
                "date_preset": "last_7d",
            },
        )
        paused: List[Dict[str, Any]] = []
        inspected = body.get("data", [])

        if self.dry_run and not inspected:
            # Deterministic dry-run sample so suite demos exercise the rule
            inspected = [
                {"ad_id": "dry_ad_fatigued", "ad_name": "Fatigued Creative", "ctr": "0.6", "frequency": "4.2"},
                {"ad_id": "dry_ad_healthy", "ad_name": "Healthy Creative", "ctr": "2.1", "frequency": "1.4"},
            ]

        for ad in inspected:
            try:
                freq = float(ad.get("frequency") or 0)
                ctr = float(ad.get("ctr") or 0)
            except (TypeError, ValueError):
                continue
            if freq > freq_threshold and ctr < ctr_threshold:
                ad_id = ad.get("ad_id")
                result = self._post(str(ad_id), {"status": "PAUSED"}) if ad_id else {}
                paused.append(
                    {
                        "ad_id": ad_id,
                        "ad_name": ad.get("ad_name"),
                        "frequency": freq,
                        "ctr": ctr,
                        "action": "PAUSED",
                        "result": result,
                    }
                )

        return {
            "status": "OK",
            "rule": "fatigue",
            "inspected": len(inspected),
            "paused": paused,
            "dry_run": self.dry_run,
        }

    def auto_scale_budgets(
        self,
        ad_account_id: str,
        roas_target: Optional[float] = None,
        scale_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Auto-Scale Rule: +15% daily budget when 48-hour ROAS exceeds target KPIs.
        """
        target = float(roas_target if roas_target is not None else self.default_roas_target)
        pct = float(scale_pct if scale_pct is not None else self.budget_scale_pct)
        ad_account_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"

        # Pull campaign insights with purchase ROAS over last 2 days
        body = self._get(
            f"{ad_account_id}/insights",
            {
                "level": "campaign",
                "fields": "campaign_id,campaign_name,spend,actions,action_values,purchase_roas",
                "date_preset": "last_2d",
                "time_increment": 1,
            },
        )
        campaigns = body.get("data", [])
        scaled: List[Dict[str, Any]] = []

        if self.dry_run and not campaigns:
            campaigns = [
                {
                    "campaign_id": "dry_camp_winner",
                    "campaign_name": "Winner",
                    "purchase_roas": [{"value": "3.8"}],
                    "spend": "120.00",
                },
                {
                    "campaign_id": "dry_camp_laggard",
                    "campaign_name": "Laggard",
                    "purchase_roas": [{"value": "1.1"}],
                    "spend": "80.00",
                },
            ]

        for row in campaigns:
            roas = self._extract_roas(row)
            if roas is None or roas < target:
                continue
            campaign_id = row.get("campaign_id")
            if not campaign_id:
                continue

            # Read current daily budget from campaign (or adset fallback)
            current = self._get(str(campaign_id), {"fields": "daily_budget,name"})
            try:
                daily = int(float(current.get("daily_budget") or 0))
            except (TypeError, ValueError):
                daily = 0

            if self.dry_run and daily <= 0:
                daily = 5000  # cents placeholder for demo

            if daily <= 0:
                continue

            new_budget = int(round(daily * (1.0 + pct / 100.0)))
            result = self._post(str(campaign_id), {"daily_budget": new_budget})
            scaled.append(
                {
                    "campaign_id": campaign_id,
                    "campaign_name": row.get("campaign_name") or current.get("name"),
                    "roas_48h": roas,
                    "roas_target": target,
                    "daily_budget_before": daily,
                    "daily_budget_after": new_budget,
                    "scale_pct": pct,
                    "result": result,
                }
            )

        return {
            "status": "OK",
            "rule": "auto_scale",
            "inspected": len(campaigns),
            "scaled": scaled,
            "dry_run": self.dry_run,
        }

    @staticmethod
    def _extract_roas(row: Dict[str, Any]) -> Optional[float]:
        pr = row.get("purchase_roas")
        if isinstance(pr, list) and pr:
            try:
                return float(pr[0].get("value", 0))
            except (TypeError, ValueError, AttributeError):
                pass
        if isinstance(pr, (int, float, str)):
            try:
                return float(pr)
            except (TypeError, ValueError):
                return None
        # Fallback: action_values purchase / spend
        try:
            spend = float(row.get("spend") or 0)
            if spend <= 0:
                return None
            values = row.get("action_values") or []
            purchase = 0.0
            for a in values:
                if a.get("action_type") in {"purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"}:
                    purchase += float(a.get("value") or 0)
            return purchase / spend if purchase else None
        except (TypeError, ValueError):
            return None

    def run_ad_guard_cycle(
        self,
        ad_account_id: str,
        ctr_threshold: float = 1.0,
        freq_threshold: float = 3.5,
        roas_target: Optional[float] = None,
        account_id: str = "ads_main",
    ) -> Dict[str, Any]:
        """Hourly-style Ad Guard: fatigue pause + ROAS auto-scale with human pacing."""
        cap = self.human.can_act(account_id)
        if not cap["allowed"]:
            return {"status": "THROTTLED_HUMAN_CAP", "cap": cap}

        self._simulate_human_delay()
        fatigue = self.monitor_ad_fatigue(ad_account_id, ctr_threshold, freq_threshold)
        self._simulate_human_delay(2.0, 5.0)
        scale = self.auto_scale_budgets(ad_account_id, roas_target=roas_target)
        if fatigue.get("paused") or scale.get("scaled"):
            self.human.record_action(account_id)
        return {
            "status": "OK",
            "fatigue": fatigue,
            "auto_scale": scale,
            "humanization": {
                "cap": self.human.can_act(account_id),
                "warmup": self.human.warmup_status(account_id),
                "profile": self.human.profile.profile_name,
            },
        }
