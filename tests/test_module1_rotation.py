"""Unit tests for Module 1 PHASE-13 — feed rotation (D7/D8/D9).

Ordered / random / home_first / manual_queue source picking with
posts-or-minutes quotas, live mid-run edits, no-consecutive-repeat for
random, and graceful fallbacks. Decision logic is pure — tests drive a
RotationController against a fake settings store and a deterministic
ticking clock; no browser is launched.
"""

from __future__ import annotations

import asyncio
import random
import unittest
from typing import Optional
from unittest import mock

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.rotation import HOME_KEY, RotationController

G1 = "https://www.facebook.com/groups/g1"
G2 = "https://www.facebook.com/groups/g2"
G3 = "https://www.facebook.com/groups/g3"


class FakeStore:
    def __init__(self, settings: M1Settings):
        self._s = settings

    def get(self) -> M1Settings:
        return self._s

    def set(self, s: M1Settings) -> None:
        self._s = s


def _s(
    mode: str = "ordered",
    groups=(),
    queue=(),
    unit: str = "posts",
    value: int = 3,
    home_first: int = 5,
    **kw,
) -> M1Settings:
    return M1Settings(
        feed_rotation_mode=mode,
        feed_group_urls=list(groups),
        feed_manual_queue=list(queue),
        source_quota_unit=unit,
        source_quota_value=value,
        feed_home_first_count=home_first,
        **kw,
    )


class TickingClock:
    """Deterministic rotation-module time.time replacement."""

    def __init__(self, start: float = 1000.0, step: float = 30.0):
        self.t = start - step
        self.step = step

    def time(self) -> float:
        self.t += self.step
        return self.t


def _cycle(rot: RotationController, s: M1Settings, engaged: int = 1) -> str:
    """Mirrors the worker loop-top protocol; returns the ACTIVE key."""
    if rot.quota_exhausted(s):
        rot.advance()
    cur = rot.current()
    assert cur is not None
    if rot.needs_navigation(cur):
        rot.mark_arrived(cur)  # worker would goto + settle cooldown here
    for _ in range(engaged):
        rot.record_engagement()
    return cur.key


def _cycle_nav(rot: RotationController, s: M1Settings, engaged: int = 1) -> Optional[str]:
    """Like _cycle but returns the key ONLY on an actual navigation."""
    if rot.quota_exhausted(s):
        rot.advance()
    cur = rot.current()
    assert cur is not None
    if not rot.needs_navigation(cur):
        for _ in range(engaged):
            rot.record_engagement()
        return None
    rot.mark_arrived(cur)
    for _ in range(engaged):
        rot.record_engagement()
    return cur.key


# ------------------------------------------------------------ intake
class TestCandidateIntake(unittest.TestCase):
    def test_empty_groups_single_home_source(self) -> None:
        rot = RotationController(FakeStore(_s()))
        srcs = rot._candidates(M1Settings())
        self.assertEqual([x.kind for x in srcs], ["home"])

    def test_group_normalization_and_dedupe_preserves_order(self) -> None:
        rot = RotationController(FakeStore(_s()))
        s = _s(groups=[G1 + "/", G2 + "?utm=x", G1, G2 + "#section", G3])
        urls = [x.url for x in rot._candidates(s)][1:]
        self.assertEqual(urls, [G1, G2, G3])

    def test_validator_strips_query_and_trailing_slash(self) -> None:
        s = M1Settings(feed_group_urls=[G1 + "/", G2 + "?utm=x", G2])
        self.assertEqual(s.feed_group_urls, [G1, G2])

    def test_validator_drops_non_facebook_and_home_from_groups(self) -> None:
        s = M1Settings(feed_group_urls=["https://twitter.com/x", "home"])
        self.assertEqual(s.feed_group_urls, [])

    def test_home_key_constant(self) -> None:
        self.assertEqual(HOME_KEY, "home")


# ------------------------------------------------------------ ordered
class TestOrderedRotation(unittest.TestCase):
    def test_posts_quota_cycles_home_g1_g2(self) -> None:
        """Acceptance: posts-quota 3 alternates home->g1->g2, cycling."""
        s = _s(value=3, groups=[G1, G2])
        rot = RotationController(FakeStore(s))
        keys = [_cycle(rot, s, engaged=3) for _ in range(7)]
        self.assertEqual(keys[:6], [HOME_KEY, G1, G2, HOME_KEY, G1, G2])
        self.assertEqual(keys[6], HOME_KEY)

    def test_first_visit_flags_navigation_then_settles(self) -> None:
        rot = RotationController(FakeStore(_s(groups=[G1])))
        cur = rot.current()
        self.assertTrue(rot.needs_navigation(cur))  # group never visited
        rot.mark_arrived(cur)
        self.assertFalse(rot.needs_navigation(cur))
        same = rot.current()
        self.assertIs(same, cur)
        self.assertFalse(rot.needs_navigation(same))

    def test_worker_seed_prevents_home_double_navigation(self) -> None:
        """Worker seeds arrival on home right after fb.start(); the loop
        must not re-navigate the page it already sits on."""
        rot = RotationController(FakeStore(_s()))
        first = rot.current()
        rot.mark_arrived(first)
        self.assertFalse(rot.needs_navigation(first))
        self.assertFalse(rot.needs_navigation(rot.current()))

    def test_skips_never_satisfy_posts_quota(self) -> None:
        rot = RotationController(FakeStore(_s(value=2)))
        _cycle(rot, _s(value=2), engaged=0)  # arrived, nothing engaged
        self.assertFalse(rot.quota_exhausted(_s(value=2)))

    def test_cursor_cycle_on_single_home_never_re_navigates(self) -> None:
        """Edge case: quota flips the cursor but the picked source is the
        one we already occupy -> no redundant reload (spec: 'no nav')."""
        rot = RotationController(FakeStore(_s()))
        s = _s()
        for _ in range(6):
            key = _cycle(rot, s, engaged=99)
            self.assertEqual(key, HOME_KEY)


# ------------------------------------------------------------ quotas
class TestMinuteQuotas(unittest.TestCase):
    def test_minutes_quota_flips_regardless_of_engagement(self) -> None:
        clock = TickingClock(step=60.0)
        rot = RotationController(FakeStore(_s(unit="minutes", value=5)))
        s = _s(unit="minutes", value=5)
        with mock.patch("services.module_1_feed_observer.rotation.time") as mtime:
            mtime.time = clock.time
            cur = rot.current()
            rot.mark_arrived(cur)  # since = t0
            for _ in range(10):
                rot.record_engagement()  # heavy engagement changes nothing
            # ~1 minute in: under the 5-minute budget
            self.assertFalse(rot.quota_exhausted(s))
            clock.t += 360.0  # jump well past the 5-minute window
            self.assertTrue(rot.quota_exhausted(s))

    def test_minutes_budget_floors_at_one_minute(self) -> None:
        clock = TickingClock(step=61.0)
        rot = RotationController(FakeStore(_s(unit="minutes", value=0)))
        with mock.patch("services.module_1_feed_observer.rotation.time") as mtime:
            mtime.time = clock.time
            cur = rot.current()
            rot.mark_arrived(cur)
            self.assertTrue(rot.quota_exhausted(_s(unit="minutes", value=0)))


# ------------------------------------------------------------ random
class TestRandomRotation(unittest.TestCase):
    def test_never_repeats_consecutively_multi_source(self) -> None:
        """Acceptance: random never NAVIGATES to the same source twice in a
        row unless it is the only source."""
        rng_state = random.getstate()
        try:
            random.seed(1234)
            s = _s(mode="random", groups=[G1, G2])
            rot = RotationController(FakeStore(s))
            last: Optional[str] = None
            for _ in range(200):
                key = _cycle_nav(rot, s)
                if key is None:
                    continue  # stayed put within a quota window — allowed
                self.assertNotEqual(key, last, f"consecutive navigation to {key}")
                last = key
            self.assertIsNotNone(last)
        finally:
            random.setstate(rng_state)

    def test_single_source_always_returned(self) -> None:
        rot = RotationController(FakeStore(_s(mode="random")))
        s = _s(mode="random")
        for _ in range(10):
            self.assertEqual(_cycle(rot, s, engaged=99), HOME_KEY)


# ------------------------------------------------------------ home_first
class TestHomeFirstRotation(unittest.TestCase):
    def test_spends_count_on_home_then_rotates_groups(self) -> None:
        s = _s(mode="home_first", groups=[G1, G2], home_first=4)
        rot = RotationController(FakeStore(s))
        keys = [_cycle(rot, s, engaged=1) for _ in range(8)]
        self.assertEqual(keys[:4], [HOME_KEY] * 4)  # home-first budget
        self.assertEqual(keys[4:7], [G1] * 3)       # group quota window
        self.assertEqual(keys[7], G2)

    def test_no_groups_stays_home_forever(self) -> None:
        rot = RotationController(FakeStore(_s(mode="home_first")))
        s = _s(mode="home_first")
        for _ in range(12):
            self.assertEqual(_cycle(rot, s, engaged=1), HOME_KEY)

    def test_zero_count_coerces_to_at_least_one(self) -> None:
        s = _s(mode="home_first", groups=[G1], home_first=0)
        rot = RotationController(FakeStore(s))
        self.assertEqual(_cycle(rot, s, engaged=1), HOME_KEY)   # 1st post home
        self.assertEqual(_cycle_nav(rot, s, engaged=99), G1)    # then groups


# ------------------------------------------------------------ manual_queue
class TestManualQueueRotation(unittest.TestCase):
    def test_queue_strictly_alternates_with_wraparound(self) -> None:
        """Acceptance: ['home','gB'] alternates forever."""
        s = _s(mode="manual_queue", groups=[G2], queue=["home", G2])
        rot = RotationController(FakeStore(s))
        keys = [_cycle(rot, s, engaged=99) for _ in range(6)]
        self.assertEqual(keys, [HOME_KEY, G2] * 3)

    def test_three_entry_queue_cycles_in_saved_order(self) -> None:
        s = _s(mode="manual_queue", groups=[G1, G2], queue=["home", G2, G1])
        rot = RotationController(FakeStore(s))
        keys = [_cycle(rot, s, engaged=99) for _ in range(6)]
        self.assertEqual(keys, [HOME_KEY, G2, G1] * 2)

    def test_unmatched_entries_degrade_to_ordered_cycling(self) -> None:
        # facebook.com URL that is NOT in the saved groups list
        s = _s(mode="manual_queue", groups=[G1], queue=["https://www.facebook.com/marketplace"])
        rot = RotationController(FakeStore(s))
        keys = [_cycle(rot, s, engaged=99) for _ in range(4)]
        # queue entry unmatched -> ordered fallback cycles [home, g1]
        self.assertEqual(keys, [HOME_KEY, G1, HOME_KEY, G1])


# ------------------------------------------------------------ live edits
class TestMidRunEdits(unittest.TestCase):
    def test_appended_group_picked_up_on_next_switch(self) -> None:
        store = FakeStore(_s(value=1))
        rot = RotationController(store)
        self.assertEqual(_cycle(rot, store.get(), engaged=1), HOME_KEY)
        store.set(_s(value=1, groups=[G2]))
        self.assertEqual(_cycle(rot, store.get(), engaged=1), G2)

    def test_edited_home_url_keeps_identity_and_marks_nav_pending(self) -> None:
        new_url = "https://www.facebook.com/?sk=h_chr"
        store = FakeStore(_s(value=999999))
        rot = RotationController(store)
        first = rot.current()
        rot.mark_arrived(first)
        store.set(
            M1Settings(feed_rotation_mode="ordered", feed_url=new_url, source_quota_value=999999)
        )
        cur = rot.current()
        self.assertEqual(cur.key, HOME_KEY)          # identity survives
        self.assertEqual(cur.url, new_url)           # live truth wins
        self.assertTrue(rot.needs_navigation(cur))   # so the worker reloads


# ------------------------------------------------------------ fb contract
class FakeGotoPage:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.gotos: list[str] = []

    def is_closed(self) -> bool:
        return False  # Playwright's is_closed is SYNC — keep fake sync too

    async def bring_to_front(self) -> None:
        pass

    async def goto(self, url, wait_until=None, timeout=None):
        self.gotos.append(url)
        if self.fail:
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")


class TestGotoFeedContract(unittest.TestCase):
    def _fb(self, page: FakeGotoPage):
        from services.module_1_feed_observer.fb import FacebookAutomation

        fb = FacebookAutomation.__new__(FacebookAutomation)
        fb.page = page
        fb.settings = M1Settings()

        async def _ready(timeout_ms: int = 90_000) -> None:
            pass

        fb._wait_feed_ready = _ready  # type: ignore[method-assign]
        return fb

    def test_navigates_and_clears_active_post(self) -> None:
        page = FakeGotoPage()
        fb = self._fb(page)
        fb._active_post = object()
        asyncio.run(fb.goto_feed(G1))
        self.assertEqual(page.gotos, [G1])
        self.assertIsNone(fb._active_post)

    def test_bad_url_never_raises(self) -> None:
        page = FakeGotoPage(fail=True)
        fb = self._fb(page)
        asyncio.run(fb.goto_feed("https://www.facebook.com/groups/broken"))
        self.assertEqual(len(page.gotos), 1)  # attempted, swallowed


class TestSurfaceContract(unittest.TestCase):
    def test_phase_string_registered(self) -> None:
        from services.module_1_feed_observer.worker import PHASES

        self.assertIn("switching_source", PHASES)
        self.assertEqual(PHASES["switching_source"], "Switching feed source")

    def test_video_policy_accepts_rotation_kwarg(self) -> None:
        import inspect

        from services.module_1_feed_observer.worker import Module1Worker

        params = inspect.signature(Module1Worker._handle_video_policy).parameters
        self.assertIn("rot", params)
        self.assertIs(params["rot"].default, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
