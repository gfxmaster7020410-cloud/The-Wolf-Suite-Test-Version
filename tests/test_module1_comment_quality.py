"""Unit tests for Module 1 PHASE-10 — comment quality suite (D15/D17/D21/D22/D23).

Prompt assembly matrix, shared text helpers, parser strip_tags flag,
bridge forwarding, and engine pass-through of the allow_hashtags setting.
No network, no browser.
"""

from __future__ import annotations

import asyncio
import threading
import unittest

from services.module_1_feed_observer.ai_engine import AIEngine
from services.module_1_feed_observer.config import (
    M1Settings,
    enforce_comment_quality,
    maybe_strip_tags,
)
from services.module_1_feed_observer.gemini_bridge import (
    GeminiWebBridge,
    parse_gemini_reply,
)


# ------------------------------------------------------------ prompt build
class TestPromptAssembly(unittest.TestCase):
    def test_match_post_default_clause_and_image_fallback(self) -> None:
        p = M1Settings().prompt()
        self.assertIn("SAME language", p)
        self.assertIn("infer from the imagery", p)

    def test_urdu_clause_contains_urdu_directive(self) -> None:
        p = M1Settings(comment_language="urdu").prompt()
        self.assertIn("اردو", p)
        self.assertIn("انگیجمنٹ والا کمنٹ", p)

    def test_english_clause(self) -> None:
        self.assertIn("Write the comment in English.",
                      M1Settings(comment_language="english").prompt())

    def test_custom_instruction_used_verbatim(self) -> None:
        p = M1Settings(
            comment_language="custom",
            custom_language_instruction="Reply only in French, s'il vous plaît.",
        ).prompt()
        self.assertIn("s'il vous plaît", p)

    def test_custom_mode_without_instruction_coerced_to_match_post(self) -> None:
        s = M1Settings(comment_language="custom")
        self.assertEqual(s.comment_language, "match_post")  # P01 rule holds

    def test_emoji_clauses_toggle(self) -> None:
        off = M1Settings(emoji_enabled=False).prompt()
        on = M1Settings(emoji_enabled=True).prompt()
        self.assertIn("No emojis.", off)
        self.assertNotIn("At most ONE emoji", off)
        self.assertIn("At most ONE emoji", on)
        self.assertNotIn("No emojis.", on)

    def test_hashtag_clause_only_when_disallowed(self) -> None:
        allowed = M1Settings(allow_hashtags=True).prompt()
        banned = M1Settings(allow_hashtags=False).prompt()
        self.assertNotIn("No hashtags", allowed)
        self.assertIn("No hashtags (#)", banned)

    def test_word_limit_clause_and_zero_bypass(self) -> None:
        self.assertIn("40 words maximum", M1Settings(comment_max_words=40).prompt())
        self.assertNotIn("words maximum", M1Settings(comment_max_words=0).prompt())

    def test_custom_prompt_still_receives_clauses_and_suffix(self) -> None:
        p = M1Settings(custom_prompt="Be kind.", structured_reply=True,
                       comment_max_words=25).prompt()
        self.assertTrue(p.startswith("Be kind."))
        self.assertIn("25 words maximum", p)
        self.assertIn('"reaction"', p)

    def test_plain_mode_excludes_json_even_with_clauses(self) -> None:
        p = M1Settings(structured_reply=False, emoji_enabled=True).prompt()
        self.assertIn("At most ONE emoji", p)
        self.assertNotIn('{"reaction"', p)


# ---------------------------------------------------------------- helpers
class TestMaybeStripTags(unittest.TestCase):
    def test_allowed_keeps_hashtags_verbatim(self) -> None:
        self.assertEqual(maybe_strip_tags("Loving this #sunset #vibes", True),
                         "Loving this #sunset #vibes")

    def test_disallowed_removes_all_and_trims(self) -> None:
        self.assertEqual(maybe_strip_tags("  Great day! #sun #beach  ", False),
                         "Great day!")

    def test_none_safe(self) -> None:
        self.assertEqual(maybe_strip_tags(None, False), "")


class TestEnforceCommentQuality(unittest.TestCase):
    def test_emoji_ban_strips_but_keeps_text(self) -> None:
        out = enforce_comment_quality("Great shot 😀🔥 mate", emojis_allowed=False, max_words=0)
        # emoji glyphs vanish; surrounding single spaces are preserved
        self.assertEqual(out, "Great shot  mate")

    def test_emoji_ban_exact(self) -> None:
        out = enforce_comment_quality("Nice 😀pic 🔥!", emojis_allowed=False, max_words=0)
        self.assertEqual(out, "Nice pic !")
        self.assertNotIn("\U0001F600", out)
        self.assertNotIn("\U0001F525", out)

    def test_emoji_allowed_preserves(self) -> None:
        out = enforce_comment_quality("Nice 😀", emojis_allowed=True, max_words=0)
        self.assertEqual(out, "Nice 😀")

    def test_word_ceiling_boundary_and_cleanup(self) -> None:
        five = "one two three four five"
        six = five + " sixty,"
        self.assertEqual(enforce_comment_quality(five, emojis_allowed=False, max_words=5), five)
        clamped = enforce_comment_quality(six, emojis_allowed=False, max_words=5)
        self.assertEqual(clamped, "one two three four five")  # trailing comma gone

    def test_zero_limit_bypasses_clamp(self) -> None:
        long = " ".join(["word"] * 400)
        out = enforce_comment_quality(long, emojis_allowed=False, max_words=0)
        # word clamp skipped, but the 280-char platform cap ALWAYS applies
        self.assertLessEqual(len(out), 280)
        self.assertTrue(out.startswith("word word"))

    def test_char_cap_280(self) -> None:
        out = enforce_comment_quality("a" * 300, emojis_allowed=False, max_words=0)
        self.assertEqual(len(out), 280)


class TestParserStripFlag(unittest.TestCase):
    _JSON = '{"reaction":"LOVE","comment":"Stunning view #nature"}'

    def test_default_strips_in_structured_branch(self) -> None:
        d = parse_gemini_reply(self._JSON, structured=True)
        self.assertEqual(d["comment"], "Stunning view")

    def test_false_preserves_in_structured_branch(self) -> None:
        d = parse_gemini_reply(self._JSON, structured=True, strip_tags=False)
        self.assertEqual(d["comment"], "Stunning view #nature")

    def test_plain_fallback_respects_flag(self) -> None:
        raw = "Sure!\nHere you go: Beautiful morning #coffee"
        d = parse_gemini_reply(raw, structured=False, strip_tags=False)
        self.assertIn("#coffee", d["comment"])
        d2 = parse_gemini_reply(raw, structured=False)  # default True
        self.assertNotIn("#coffee", d2["comment"])


class TestBridgeForwarding(unittest.TestCase):
    def _bridge_with_reply(self, reply: str) -> GeminiWebBridge:
        b = GeminiWebBridge(context=None)

        async def fake_generate_comment(path):
            return reply

        b.generate_comment = fake_generate_comment  # type: ignore[method-assign]
        return b

    def test_default_bridge_strips_tags(self) -> None:
        b = self._bridge_with_reply('{"reaction":"LIKE","comment":"wow #epic"}')
        d = asyncio.run(b.generate_decision("x.png"))
        self.assertEqual(d["source"], "gemini_web")
        self.assertNotIn("#epic", d["comment"])

    def test_strip_tags_false_end_to_end_via_bridge(self) -> None:
        b = self._bridge_with_reply('{"reaction":"WOW","comment":"wow #epic"}')
        b.strip_tags = False
        d = asyncio.run(b.generate_decision("x.png"))
        self.assertIn("#epic", d["comment"])
        self.assertEqual(d["reaction"], "WOW")


class TestEnginePassThrough(unittest.TestCase):
    class _Store:
        def __init__(self, s):
            self._s = s

        def get(self):
            return self._s

    class _FakeBridge:
        def __init__(self):
            self.ready = False

        async def ensure_ready(self):
            self.ready = True

        async def generate_decision(self, path):
            return {"reaction": "LIKE", "comment": "web", "confidence": 0.9}

    def test_web_transport_receives_tag_setting(self) -> None:
        # tags BANNED (allow_hashtags=False) -> bridge must STRIP (True)
        s = M1Settings(ai_mode="web_only", allow_hashtags=False)
        bridge = self._FakeBridge()
        eng = AIEngine(self._Store(s), web_bridge_factory=lambda: bridge,
                       cancel_event=threading.Event())

        async def run():
            await eng.ensure_ready()
            return await eng.decide("x.png")

        asyncio.run(run())
        self.assertTrue(bridge.strip_tags is True)
        self.assertTrue(bridge.structured is True)
        self.assertIn("No hashtags (#)", bridge.prompt)

        # tags ALLOWED -> bridge must PRESERVE (False)
        s2 = M1Settings(ai_mode="web_only", allow_hashtags=True)
        bridge2 = self._FakeBridge()
        eng2 = AIEngine(self._Store(s2), web_bridge_factory=lambda: bridge2,
                        cancel_event=threading.Event())

        async def run2():
            await eng2.ensure_ready()
            await eng2.decide("x.png")  # attrs assigned per-decision

        asyncio.run(run2())
        self.assertIs(bridge2.strip_tags, False)
        self.assertNotIn("No hashtags", bridge2.prompt)

    def test_api_transport_forwards_kwarg(self) -> None:
        from services.module_1_feed_observer import gemini_api as g

        captured = {}

        async def fake_v2(path, **kw):
            captured.update(kw)
            return {"reaction": "LIKE", "comment": "api", "confidence": 0.9}

        orig = g.generate_engagement_v2
        g.generate_engagement_v2 = fake_v2
        try:
            s = M1Settings(ai_mode="api_primary_web_fallback",
                           allow_hashtags=True,
                           gemini_api_key="sk-x",
                           custom_prompt="Hello there.")
            eng = AIEngine(self._Store(s), web_bridge_factory=lambda: None)
            asyncio.run(eng.decide("x.png"))
        finally:
            g.generate_engagement_v2 = orig

        self.assertIs(captured.get("strip_tags"), False)  # allowed => no strip
        self.assertEqual(captured.get("api_key"), "sk-x")
        self.assertEqual(captured.get("prompt"), s.prompt())


if __name__ == "__main__":
    unittest.main(verbosity=2)
