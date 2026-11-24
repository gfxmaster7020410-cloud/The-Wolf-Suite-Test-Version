"""Unit tests for Module 1 PHASE-06 — scroll profile presets (D10).

Covers: preset table values, effective_scroll resolver, write-through on
profile selection, explicit-delta wins (custom state), unrelated edits
never resetting custom deltas, pair coercion safety, and real wheel
behavior through FacebookAutomation with a faked page.
"""

from __future__ import annotations

import asyncio
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.module_1_feed_observer.config import (
    M1Settings,
    M1SettingsStore,
    SCROLL_PRESETS,
)
from services.module_1_feed_observer.fb import FacebookAutomation


class TestPresetTable(unittest.TestCase):
    def test_preset_values_match_spec(self) -> None:
        self.assertEqual(SCROLL_PRESETS["lazy"], (420, 780, 1.0))
        self.assertEqual(SCROLL_PRESETS["slow"], (200, 400, 1.5))
        self.assertEqual(SCROLL_PRESETS["fast"], (800, 1400, 0.7))


class TestEffectiveScroll(unittest.TestCase):
    def test_each_named_profile_reports_multiplier(self) -> None:
        for name, (pmin, pmax, mul) in SCROLL_PRESETS.items():
            s = M1Settings(scroll_profile=name, scroll_delta_min=pmin, scroll_delta_max=pmax)
            self.assertEqual(s.effective_scroll(), (pmin, pmax, mul), name)

    def test_hand_edited_deltas_stay_live_under_named_profile(self) -> None:
        # Custom state: named profile but owner-tuned pixels.
        s = M1Settings(scroll_profile="lazy", scroll_delta_min=500, scroll_delta_max=900)
        dmin, dmax, mul = s.effective_scroll()
        self.assertEqual((dmin, dmax), (500, 900))
        self.assertEqual(mul, 1.0)  # personality multiplier still applies


class TestWriteThrough(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "s.json"
        self.store = M1SettingsStore(path=self.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_profile_selection_snaps_pixels(self) -> None:
        self.store.update({"scroll_profile": "slow"})
        s = self.store.get()
        self.assertEqual((s.scroll_delta_min, s.scroll_delta_max), (200, 400))
        self.store.update({"scroll_profile": "fast"})
        s = self.store.get()
        self.assertEqual((s.scroll_delta_min, s.scroll_delta_max), (800, 1400))

    def test_write_through_hits_disk_json_reality(self) -> None:
        self.store.update({"scroll_profile": "fast"})
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["scroll_delta_min"], 800)
        self.assertEqual(raw["scroll_delta_max"], 1400)

    def test_explicit_deltas_with_profile_win_verbatim(self) -> None:
        res = self.store.update(
            {
                "scroll_profile": "lazy",
                "scroll_delta_min": 500,
                "scroll_delta_max": 900,
            }
        )
        self.assertTrue(res["ok"])
        s = self.store.get()
        self.assertEqual(s.scroll_profile, "lazy")
        self.assertEqual((s.scroll_delta_min, s.scroll_delta_max), (500, 900))

    def test_unrelated_edit_never_resets_custom_deltas(self) -> None:
        self.store.update({"scroll_delta_min": 500, "scroll_delta_max": 900})
        self.store.update({"gap_between_posts_min": 6.0})
        s = self.store.get()
        self.assertEqual((s.scroll_delta_min, s.scroll_delta_max), (500, 900))

    def test_inverted_explicit_pair_coerced_safely(self) -> None:
        self.store.update({"scroll_delta_min": 700, "scroll_delta_max": 300})
        s = self.store.get()
        self.assertEqual((s.scroll_delta_min, s.scroll_delta_max), (700, 700))

    def test_unknown_patch_key_still_warns_not_fatal(self) -> None:
        res = self.store.update({"scroll_profile": "slow", "nope": 1})
        self.assertTrue(res["ok"])
        self.assertTrue(any("unknown setting" in w for w in res["warnings"]))


# ------------------------------------------------------------ fb consumer
class _RecorderMouse:
    def __init__(self, page):
        self._page = page

    async def wheel(self, dx, dy):
        self._page.wheels.append(int(dy))


class _FakePage:
    def __init__(self):
        self.wheels: list[int] = []
        self.sleeps: list[float] = []
        self.viewport_size = {"width": 1365, "height": 900}

    def is_closed(self) -> bool:  # Playwright API is SYNC here
        return False

    async def bring_to_front(self):
        pass

    @property
    def mouse(self):
        return _RecorderMouse(self)


async def _drive_scroll(fb) -> tuple[int, list]:
    page = fb.page
    with mock.patch("services.module_1_feed_observer.fb.asyncio.sleep",
                    side_effect=lambda s: _nap(page, s)):
        await fb.scroll_to_next_post()
    return sum(page.wheels), page.sleeps


def _nap(page, seconds):
    # record then yield synchronously via asyncio.sleep(0)
    page.sleeps.append(seconds)
    return _zero()


async def _zero():
    await asyncio.sleep(0)


class TestFacebookConsumer(unittest.TestCase):
    def _fb(self, profile: str) -> FacebookAutomation:
        pmin, pmax, _ = SCROLL_PRESETS[profile]
        s = M1Settings(scroll_profile=profile, scroll_delta_min=pmin, scroll_delta_max=pmax)
        fb = FacebookAutomation.__new__(FacebookAutomation)
        fb.settings = s
        fb.page = _FakePage()
        fb.on_phase = None
        fb.cancel_event = None
        fb._active_post = None
        return fb

    def test_fast_profile_wheels_within_fast_range(self) -> None:
        totals = []
        for _ in range(12):
            total, _ = asyncio.run(_drive_scroll(self._fb("fast")))
            totals.append(total)
        self.assertGreaterEqual(min(totals), 800)
        self.assertLessEqual(max(totals), 1400)

    def test_slow_profile_wheels_within_slow_range_and_slower_pauses(self) -> None:
        slow_totals, slow_sleeps = asyncio.run(_drive_scroll(self._fb("slow")))
        self.assertGreaterEqual(slow_totals, 200)
        self.assertLessEqual(slow_totals, 400)
        # last sleep is the post-scroll settle (0.4-0.8s) — exclude positionally
        chunk_sleeps = slow_sleeps[:-1]
        self.assertGreater(len(chunk_sleeps), 0)
        self.assertTrue(all(s >= 0.08 * 1.5 - 1e-9 for s in chunk_sleeps))
        self.assertLessEqual(max(chunk_sleeps), 0.28 * 1.5 + 1e-9)

    def test_lazy_default_matches_legacy_range(self) -> None:
        for _ in range(8):
            total, _ = asyncio.run(_drive_scroll(self._fb("lazy")))
            self.assertGreaterEqual(total, 420)
            self.assertLessEqual(total, 780)


if __name__ == "__main__":
    unittest.main(verbosity=2)
