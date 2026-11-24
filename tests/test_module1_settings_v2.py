"""Unit tests for Module 1 settings Schema v2 (IMPLEMENTATION_ROADMAP PHASE-01).

Covers: v2 defaults, legacy-file migration, corrupt-key pruning,
secret masking + set_secret, enum coercions, numeric clamps,
feed-source list sanitization, and the runner config contract.
No browser, no network, no writes to the real data/ directory.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.module_1_feed_observer.config import (
    AI_MODES,
    COMMENT_STYLES,
    M1Settings,
    M1SettingsStore,
    SECRET_FIELDS,
)


def _write(tmp: Path, name: str, payload) -> Path:
    p = tmp / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


LEGACY_FILE = {
    "feed_url": "https://www.facebook.com",
    "max_posts_per_run": 7,
    "gap_between_posts_min": 6.0,
    "gap_between_posts_max": 12.0,
    "typing_style": "type",
}


class TestV2Defaults(unittest.TestCase):
    def test_ai_engine_group(self) -> None:
        s = M1Settings()
        self.assertEqual(s.ai_mode, "api_primary_web_fallback")
        self.assertIn(s.ai_mode, AI_MODES)
        self.assertEqual(s.gemini_api_key, "")
        self.assertEqual(s.gemini_api_model, "")
        self.assertEqual(s.gemini_reply_wait_sec, 60)
        self.assertTrue(s.api_failure_is_fatal_for_post)

    def test_rotation_group(self) -> None:
        s = M1Settings()
        self.assertEqual(s.feed_rotation_mode, "ordered")
        self.assertEqual(s.feed_home_first_count, 20)
        self.assertEqual(s.feed_group_urls, [])
        self.assertEqual(s.feed_manual_queue, [])
        self.assertEqual(s.source_quota_unit, "posts")
        self.assertEqual(s.source_quota_value, 25)
        self.assertEqual(s.source_switch_cooldown_sec, 45)

    def test_policy_groups(self) -> None:
        s = M1Settings()
        self.assertEqual(s.scroll_profile, "lazy")
        self.assertEqual(s.video_policy, "like_only")
        self.assertEqual(s.capture_failure_policy, "instant_skip")
        self.assertEqual(s.capture_retry_limit, 3)
        self.assertEqual(s.seen_expiry_days, 30)
        self.assertEqual(s.action_count_mode, "per_post")
        self.assertEqual(s.checkpoint_policy, "wait_indefinitely")

    def test_quality_breaker_runshape_groups(self) -> None:
        s = M1Settings()
        self.assertEqual(s.comment_language, "match_post")
        self.assertEqual(s.custom_language_instruction, "")
        self.assertTrue(s.allow_hashtags)
        self.assertFalse(s.emoji_enabled)
        self.assertEqual(s.comment_max_words, 40)
        self.assertFalse(s.circuit_breaker_enabled)
        self.assertEqual(s.circuit_breaker_threshold, 5)
        self.assertEqual(s.circuit_breaker_cooldown_min, 10.0)
        self.assertEqual(s.run_length_mode, "unlimited")
        self.assertEqual(s.run_time_limit_min, 120)
        self.assertEqual(s.run_end_policy, "close_browser")
        self.assertEqual(s.schedule_mode, "manual")
        self.assertEqual(s.daily_start_time, "")
        self.assertEqual(s.interval_run_hours, 2.0)
        self.assertEqual(s.interval_sleep_hours, 4.0)

    def test_engagement_style_registered(self) -> None:
        self.assertIn("engagement", COMMENT_STYLES)
        s = M1Settings(comment_style="engagement")
        self.assertIn("sparks connection", s.prompt())
        self.assertIn('"reaction"', s.prompt())  # structured suffix intact


class TestLegacyMigration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_old_file_loads_with_v2_defaults(self) -> None:
        path = _write(self.tmp, "m.json", LEGACY_FILE)
        s = M1SettingsStore(path=path).get()
        # preserved legacy values
        self.assertEqual(s.max_posts_per_run, 7)
        self.assertEqual(s.typing_style, "type")
        self.assertEqual(s.gap_between_posts_min, 6.0)
        # injected v2 defaults
        self.assertEqual(s.ai_mode, "api_primary_web_fallback")
        self.assertEqual(s.video_policy, "like_only")
        self.assertEqual(s.seen_expiry_days, 30)
        self.assertEqual(s.schedule_mode, "manual")

    def test_corrupt_key_pruned_not_fatal(self) -> None:
        bad = dict(LEGACY_FILE)
        bad["seen_expiry_days"] = "soon"  # wrong type on purpose
        path = _write(self.tmp, "c.json", bad)
        s = M1SettingsStore(path=path).get()
        self.assertEqual(s.max_posts_per_run, 7)  # good keys survive
        self.assertEqual(s.seen_expiry_days, 30)  # bad key back to default

    def test_multiple_corrupt_keys_all_pruned(self) -> None:
        bad = dict(LEGACY_FILE)
        bad["ai_mode"] = ["not", "a", "string"]
        bad["interval_sleep_hours"] = object.__doc__  # junk value
        path = _write(self.tmp, "c2.json", bad)
        s = M1SettingsStore(path=path).get()
        self.assertIn(s.ai_mode, AI_MODES)
        self.assertGreaterEqual(s.interval_sleep_hours, 0.25)
        self.assertEqual(s.max_posts_per_run, 7)

    def test_garbage_file_rejected_wholesale(self) -> None:
        p = self.tmp / "g.json"
        p.write_text("{not json", encoding="utf-8")
        s = M1SettingsStore(path=p).get()
        self.assertEqual(s.ai_mode, "api_primary_web_fallback")


class TestSecretMasking(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "s.json"
        self.store = M1SettingsStore(path=self.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dump_and_compact_never_leak_key(self) -> None:
        self.store.set_secret("gemini_api_key", "sk-live-abc123")
        d = self.store.dump()
        self.assertEqual(d["gemini_api_key"], "configured")
        self.assertNotIn("sk-live-abc123", json.dumps(d))
        c = M1SettingsStore(path=self.path).dump()["gemini_api_key"]
        self.assertEqual(c, "configured")

    def test_raw_value_on_disk_and_in_get(self) -> None:
        self.store.set_secret("gemini_api_key", " sk-live-xyz ")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["gemini_api_key"], "sk-live-xyz")  # stripped
        self.assertEqual(self.store.get().gemini_api_key, "sk-live-xyz")

    def test_mask_echo_is_noop(self) -> None:
        self.store.set_secret("gemini_api_key", "sk-real-1")
        res = self.store.update({"gemini_api_key": "configured"})
        self.assertTrue(res["ok"])
        self.assertEqual(self.store.get().gemini_api_key, "sk-real-1")

    def test_set_secret_rejects_unknown_name_and_clears(self) -> None:
        self.assertFalse(self.store.set_secret("not_a_secret", "x")["ok"])
        self.assertTrue(SECRET_FIELDS == frozenset({"gemini_api_key"}))
        self.store.set_secret("gemini_api_key", "temp")
        self.store.set_secret("gemini_api_key", "")
        self.assertEqual(self.store.dump()["gemini_api_key"], "")

    def test_update_returns_masked_settings_even_on_failure(self) -> None:
        self.store.set_secret("gemini_api_key", "sk-hidden")
        res = self.store.update({"gap_between_posts_min": -5})
        self.assertFalse(res["ok"])
        self.assertEqual(res["settings"]["gemini_api_key"], "configured")


class TestCoercionsAndClamps(unittest.TestCase):
    def test_enum_fallbacks_v2(self) -> None:
        s = M1Settings(
            ai_mode="nonsense",
            feed_rotation_mode="zigzag",
            scroll_profile="warp",
            video_policy="maybe",
            capture_failure_policy="forever",
            comment_language="klingon",
            checkpoint_policy="panic",
            run_length_mode="eternal",
            run_end_policy="explode",
            schedule_mode="whenever",
            action_count_mode="chaos",
            source_quota_unit="lightyears",
        )
        self.assertEqual(s.ai_mode, "api_primary_web_fallback")
        self.assertEqual(s.feed_rotation_mode, "ordered")
        self.assertEqual(s.scroll_profile, "lazy")
        self.assertEqual(s.video_policy, "like_only")
        self.assertEqual(s.capture_failure_policy, "instant_skip")
        self.assertEqual(s.comment_language, "match_post")
        self.assertEqual(s.checkpoint_policy, "wait_indefinitely")
        self.assertEqual(s.run_length_mode, "unlimited")
        self.assertEqual(s.run_end_policy, "close_browser")
        self.assertEqual(s.schedule_mode, "manual")
        self.assertEqual(s.action_count_mode, "per_post")
        self.assertEqual(s.source_quota_unit, "posts")

    def test_numeric_clamps(self) -> None:
        s = M1Settings(
            capture_retry_limit=99,
            seen_expiry_days=-3,
            comment_max_words=10_000,
            run_time_limit_min=0,
            interval_run_hours=0,
            interval_sleep_hours=200,
            source_quota_value=0,
            feed_home_first_count=0,
            circuit_breaker_threshold=1,
        )
        self.assertEqual(s.capture_retry_limit, 10)
        self.assertEqual(s.seen_expiry_days, 0)
        self.assertEqual(s.comment_max_words, 120)
        self.assertEqual(s.run_time_limit_min, 5)
        self.assertEqual(s.interval_run_hours, 0.25)
        self.assertEqual(s.interval_sleep_hours, 72.0)
        self.assertEqual(s.source_quota_value, 1)
        self.assertEqual(s.feed_home_first_count, 1)
        self.assertEqual(s.circuit_breaker_threshold, 2)

    def test_custom_language_requires_instruction(self) -> None:
        s = M1Settings(comment_language="custom", custom_language_instruction="")
        self.assertEqual(s.comment_language, "match_post")
        s2 = M1Settings(
            comment_language="custom",
            custom_language_instruction="Reply in French.",
        )
        self.assertEqual(s2.comment_language, "custom")

    def test_daily_start_time_format(self) -> None:
        self.assertEqual(M1Settings(daily_start_time="07:05").daily_start_time, "07:05")
        self.assertEqual(M1Settings(daily_start_time="7h5").daily_start_time, "")
        self.assertEqual(M1Settings(daily_start_time="").daily_start_time, "")


class TestFeedSourceLists(unittest.TestCase):
    def test_group_urls_filtered_deduped_capped(self) -> None:
        urls = [
            "https://www.facebook.com/groups/a/",
            "junk",
            "",
            "https://www.facebook.com/groups/a/",
            "https://facebook.com/groups/b",
        ] * 20  # force the 50-entry ceiling logic
        s = M1Settings(feed_group_urls=urls)
        # PHASE-13 refined intake: query/fragment + trailing slash stripped
        # BEFORE dedupe, so slash/query variants collapse to one entry.
        self.assertEqual(
            s.feed_group_urls[:2],
            ["https://www.facebook.com/groups/a", "https://facebook.com/groups/b"],
        )
        self.assertLessEqual(len(s.feed_group_urls), 50)

    def test_manual_queue_allows_home_token(self) -> None:
        s = M1Settings(
            feed_manual_queue=[
                "home",
                "https://www.facebook.com/groups/g1",
                "home",
                "nope",
            ]
        )
        self.assertEqual(
            s.feed_manual_queue,
            ["home", "https://www.facebook.com/groups/g1"],
        )

    def test_list_patch_via_update(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = M1SettingsStore(path=Path(td) / "u.json")
            res = store.update({"feed_group_urls": ["https://www.facebook.com/groups/z"]})
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(store.get().feed_group_urls, ["https://www.facebook.com/groups/z"])


class TestCompactContract(unittest.TestCase):
    def test_superset_of_legacy_keys(self) -> None:
        c = M1Settings().compact()
        for key in (
            "gap_between_posts_min",
            "reaction_mode",
            "comment_style",
            "max_posts_per_run",
            "typing_style",
            "pause_on_checkpoint",
            "skip_already_commented",
        ):
            self.assertIn(key, c)
        for key in (
            "ai_mode",
            "scroll_profile",
            "video_policy",
            "capture_failure_policy",
            "seen_expiry_days",
            "comment_language",
            "emoji_enabled",
            "allow_hashtags",
            "comment_max_words",
            "action_count_mode",
            "checkpoint_policy",
            "run_length_mode",
            "run_end_policy",
            "schedule_mode",
        ):
            self.assertIn(key, c)

    def test_runner_config_exposes_v2_fields_masked(self) -> None:
        from services.module_1_feed_observer.runner import Module1Runner

        with tempfile.TemporaryDirectory() as td:
            store = M1SettingsStore(path=Path(td) / "r.json")
            store.set_secret("gemini_api_key", "sk-runner-check")
            cfg = Module1Runner(store=store).config()
            self.assertIn("ai_mode", cfg)
            self.assertEqual(cfg["gemini_api_key"], "configured")
            self.assertNotIn("sk-runner-check", json.dumps(cfg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
