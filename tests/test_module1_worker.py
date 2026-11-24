"""Unit tests for Module 1 — Manual Worker (config, worker logic, no browser)."""

from __future__ import annotations

import unittest

from services.module_1_feed_observer.config import M1Settings, M1SettingsStore
from services.module_1_feed_observer.worker import Module1Worker


class TestM1SettingsValidation(unittest.TestCase):
    def test_defaults(self) -> None:
        s = M1Settings()
        self.assertEqual(s.typing_style, "paste")
        self.assertEqual(s.reaction_mode, "auto")
        self.assertEqual(s.fixed_reaction, "LIKE")
        self.assertTrue(s.comment_enabled)
        self.assertEqual(s.max_posts_per_run, 0)

    def test_enum_fallbacks(self) -> None:
        s = M1Settings(typing_style="hover", reaction_mode="sometimes", comment_style="loud")
        self.assertEqual(s.typing_style, "paste")
        self.assertEqual(s.reaction_mode, "auto")
        self.assertEqual(s.comment_style, "casual")

    def test_reaction_whitelist(self) -> None:
        s = M1Settings(fixed_reaction="ANGRY")
        self.assertEqual(s.fixed_reaction, "LIKE")
        s2 = M1Settings(fixed_reaction="love")
        self.assertEqual(s2.fixed_reaction, "LOVE")

    def test_range_clamping(self) -> None:
        s = M1Settings(gap_between_posts_min=10, gap_between_posts_max=4)
        self.assertEqual(s.gap_between_posts_max, 10)
        s2 = M1Settings(scroll_delta_min=1000, scroll_delta_max=200)
        self.assertEqual(s2.scroll_delta_max, 1000)

    def test_work_hours_format(self) -> None:
        self.assertEqual(M1Settings(work_hours_start="09:30").work_hours_start, "09:30")
        self.assertEqual(M1Settings(work_hours_start="banana").work_hours_start, "")
        self.assertEqual(M1Settings(work_hours_start="").work_hours_start, "")

    def test_structured_prompt_contains_json(self) -> None:
        s = M1Settings(structured_reply=True)
        self.assertIn('"reaction"', s.prompt())
        self.assertIn('"comment"', s.prompt())

    def test_custom_prompt_overrides_style(self) -> None:
        s = M1Settings(custom_prompt="Say something kind.")
        self.assertIn("Say something kind.", s.prompt())

    def test_plain_prompt_has_no_json(self) -> None:
        s = M1Settings(structured_reply=False)
        self.assertNotIn('{"reaction"', s.prompt())

    def test_compact_subset(self) -> None:
        s = M1Settings()
        c = s.compact()
        self.assertIn("gap_between_posts_min", c)
        self.assertIn("reaction_mode", c)
        self.assertIn("comment_style", c)


class TestM1SettingsStore(unittest.TestCase):
    def test_update_valid_and_ignored_keys(self) -> None:
        store = M1SettingsStore(path="data/test_m1_settings.json")
        try:
            res = store.update({"max_posts_per_run": 7, "not_a_setting": 1})
            self.assertTrue(res["ok"])
            self.assertEqual(res["settings"]["max_posts_per_run"], 7)
            self.assertTrue(any("unknown setting" in w for w in res["warnings"]))
        finally:
            import pathlib

            pathlib.Path("data/test_m1_settings.json").unlink(missing_ok=True)

    def test_invalid_value_rejected(self) -> None:
        store = M1SettingsStore(path="data/test_m1_settings.json")
        try:
            res = store.update({"gap_between_posts_min": -5})
            self.assertFalse(res["ok"])
        finally:
            import pathlib

            pathlib.Path("data/test_m1_settings.json").unlink(missing_ok=True)


class TestWorkHoursWindow(unittest.TestCase):
    def test_disabled_when_empty(self) -> None:
        s = M1Settings(work_hours_start="", work_hours_end="")
        self.assertEqual(Module1Worker._work_hours_wait(s), 0.0)

    def test_parse_errors_safe(self) -> None:
        s = M1Settings(work_hours_start="xx", work_hours_end="yy")
        self.assertEqual(Module1Worker._work_hours_wait(s), 0.0)

    def test_same_window_never_waits(self) -> None:
        s = M1Settings(work_hours_start="09:00", work_hours_end="09:00")
        self.assertEqual(Module1Worker._work_hours_wait(s), 0.0)

    def test_overnight_window_logic(self) -> None:
        # 22:00 → 06:00 must not be a negative window
        s = M1Settings(work_hours_start="22:00", work_hours_end="06:00")
        wait = Module1Worker._work_hours_wait(s)
        self.assertGreaterEqual(wait, 0.0)
        self.assertLess(wait, 24 * 3600)


class TestReactionResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = Module1Worker.__new__(Module1Worker)

    def test_fixed_mode(self) -> None:
        s = M1Settings(reaction_mode="fixed", fixed_reaction="LOVE")
        self.assertEqual(self.worker._resolve_reaction(s, "WOW"), "LOVE")

    def test_none_mode(self) -> None:
        s = M1Settings(reaction_mode="none")
        self.assertEqual(self.worker._resolve_reaction(s, "LIKE"), "NONE")

    def test_auto_mode_valid(self) -> None:
        s = M1Settings(reaction_mode="auto")
        self.assertEqual(self.worker._resolve_reaction(s, "HAHA"), "HAHA")

    def test_auto_mode_fallback(self) -> None:
        s = M1Settings(reaction_mode="auto")
        self.assertEqual(self.worker._resolve_reaction(s, "ANGRY"), "LIKE")
        self.assertEqual(self.worker._resolve_reaction(s, ""), "LIKE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
