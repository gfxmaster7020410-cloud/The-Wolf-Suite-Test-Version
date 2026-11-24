"""Unit tests for Module 1 — Feed Observer (no browser / no network)."""

from __future__ import annotations

import asyncio
import unittest

from services.module_1_feed_observer.gemini_api import (
    VALID_REACTIONS as API_REACTIONS,
    parse_api_reply,
)
from services.module_1_feed_observer.gemini_bridge import (
    VALID_REACTIONS,
    parse_decision_payload,
    parse_gemini_reply,
)
from services.module_1_feed_observer.observer import (
    BezierMouseController,
    Module1FeedObserver,
)


class _ThrottledHuman:
    """Fake humanization engine that blocks all actions."""

    def can_act(self, account_id: str) -> dict:
        return {
            "allowed": False,
            "hourly_used": 999,
            "hourly_cap": 1,
            "daily_used": 999,
            "daily_cap": 1,
            "warmup_multiplier": 1.0,
            "warmup_day": 0,
        }

    def record_action(self, account_id: str) -> None:
        pass


class TestParseGeminiReply(unittest.TestCase):
    def test_structured_json_fenced(self) -> None:
        raw = '```json\n{"reaction":"LOVE","comment":"Great post!"}\n```'
        decision = parse_gemini_reply(raw, structured=True)
        self.assertEqual(decision["reaction"], "LOVE")
        self.assertEqual(decision["comment"], "Great post!")

    def test_structured_json_plain(self) -> None:
        raw = '{"reaction":"WOW","comment":"Amazing share"}'
        decision = parse_gemini_reply(raw)
        self.assertEqual(decision["reaction"], "WOW")
        self.assertEqual(decision["comment"], "Amazing share")

    def test_invalid_reaction_falls_back(self) -> None:
        raw = '{"reaction":"ANGRY","comment":"Nice"}'
        decision = parse_gemini_reply(raw, structured=True)
        self.assertEqual(decision["reaction"], "LIKE")
        self.assertEqual(decision["comment"], "Nice")

    def test_hashtags_removed(self) -> None:
        raw = '{"reaction":"CARE","comment":"Hang in there #staystrong #love"}'
        decision = parse_gemini_reply(raw, structured=True)
        self.assertNotIn("#", decision["comment"])

    def test_ui_noise_rejected(self) -> None:
        raw = "Settings\nNew chat\nAbout Gemini"
        decision = parse_gemini_reply(raw)
        self.assertNotEqual(decision["comment"], "Settings")
        self.assertTrue(decision["comment"])

    def test_empty_input_default(self) -> None:
        decision = parse_gemini_reply("")
        self.assertEqual(decision["reaction"], "LIKE")
        self.assertEqual(decision["comment"], "Nice!")

    def test_decision_payload_alias(self) -> None:
        self.assertIs(parse_decision_payload, parse_gemini_reply)


class TestParseApiReply(unittest.TestCase):
    def test_fenced_json(self) -> None:
        raw = '```json\n{"comment":"one line","reaction":"HAHA"}\n```'
        decision = parse_api_reply(raw)
        self.assertEqual(decision["comment"], "one line")
        self.assertEqual(decision["reaction"], "HAHA")

    def test_plain_text(self) -> None:
        decision = parse_api_reply("Just a plain comment line")
        self.assertEqual(decision["comment"], "Just a plain comment line")
        self.assertEqual(decision["reaction"], "LIKE")

    def test_invalid_reaction_default(self) -> None:
        decision = parse_api_reply('{"comment":"hi","reaction":"ANGRY"}')
        self.assertEqual(decision["reaction"], "LIKE")

    def test_empty_fallback(self) -> None:
        decision = parse_api_reply("")
        self.assertEqual(decision["comment"], "Really nice — thanks for sharing!")

    def test_multiline_takes_first_line(self) -> None:
        decision = parse_api_reply("first line\nsecond line")
        self.assertEqual(decision["comment"], "first line")


class TestReactionWhitelist(unittest.TestCase):
    def test_valid_set(self) -> None:
        self.assertEqual(VALID_REACTIONS, {"LIKE", "LOVE", "CARE", "HAHA", "WOW"})
        self.assertEqual(API_REACTIONS, VALID_REACTIONS)

    def test_member_check(self) -> None:
        for r in ("LIKE", "LOVE", "CARE", "HAHA", "WOW"):
            self.assertIn(r, VALID_REACTIONS)
        self.assertNotIn("ANGRY", VALID_REACTIONS)


class TestBezierPath(unittest.TestCase):
    def test_path_endpoints_and_shape(self) -> None:
        start, end = (10.0, 20.0), (90.0, 80.0)
        path = BezierMouseController.generate_bezier_path(start, end, num_points=12)
        # bezier_path emits n+1 samples and applies per-point mouse jitter
        self.assertEqual(len(path), 13)
        tol = 3.0  # humanization.profile.mouse_jitter_px ceiling
        self.assertLess(abs(path[0][0] - start[0]), tol)
        self.assertLess(abs(path[0][1] - start[1]), tol)
        self.assertLess(abs(path[-1][0] - end[0]), tol)
        self.assertLess(abs(path[-1][1] - end[1]), tol)
        for x, y in path:
            self.assertIsInstance(x, float)
            self.assertIsInstance(y, float)


class TestThrottleBranch(unittest.TestCase):
    def test_throttled_session_returns_without_browser(self) -> None:
        observer = Module1FeedObserver(engine=_ThrottledHuman())

        async def _run() -> dict:
            return await observer.run_observer_session(
                max_posts=2, account_id="throttled_acct", dry_run=True
            )

        result = asyncio.run(_run())
        self.assertEqual(result["status"], "THROTTLED_HUMAN_CAP")
        self.assertEqual(result["posts_processed"], 0)
        self.assertIn("cap", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
