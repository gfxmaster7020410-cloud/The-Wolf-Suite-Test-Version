"""Unit tests for Module 1 PHASE-07 — video/reel policy (D12).

Covers classify_post heuristics (href -> <video> -> aria 'reel' -> text)
and all _handle_video_policy outcomes: engage fall-through, skip,
like_only contradiction-as-skip, like success bookkeeping (P05 counting,
seen-marking, checkpoint scan, long break), and failed-Like edge case.
No browser; fakes only.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from services.module_1_feed_observer.config import M1Settings
from services.module_1_feed_observer.fb import FacebookAutomation
from services.module_1_feed_observer.worker import Module1Worker


# ------------------------------------------------------- classification
class FakePost:
    def __init__(self, counts: dict):
        self.counts = counts

    def locator(self, selector: str):
        outer = self

        class L:
            async def count(self):
                return outer.counts.get(selector, 0)

        return L()


class TestClassifyPost(unittest.TestCase):
    def setUp(self) -> None:
        self.fb = FacebookAutomation.__new__(FacebookAutomation)

    def _classify(self, post, url):
        return asyncio.run(self.fb.classify_post(post, url))

    def test_permalink_hints_win_language_independent(self) -> None:
        for hint in ("/videos/", "/reel/", "/watch/"):
            url = f"https://www.facebook.com/x{hint}123"
            self.assertEqual(
                self._classify(FakePost({}), url), "video", hint
            )

    def test_video_element_detected_without_url(self) -> None:
        post = FakePost({"video": 1})
        self.assertEqual(self._classify(post, None), "video")

    def test_aria_reel_detected_when_no_href_no_video_tag(self) -> None:
        post = FakePost({"video": 0, "[aria-label*='reel' i]": 2})
        self.assertEqual(self._classify(post, ""), "video")

    def test_plain_text_default(self) -> None:
        post = FakePost({})
        self.assertEqual(self._classify(post, "https://www.facebook.com/permalink/1"), "text")

    def test_broken_locator_falls_back_to_text(self) -> None:
        class Exploder:
            def locator(self, sel):
                raise RuntimeError("detached")

        self.assertEqual(self._classify(Exploder(), None), "text")


# ---------------------------------------------------------- policy gate
class FakeSeen:
    def __init__(self):
        self.added: list[str] = []

    def add(self, url):
        if url:
            self.added.append(url)

    def count(self):
        return len(self.added)


class FakeFb:
    def __init__(self, react_ok=True):
        self.react_calls: list[str] = []
        self.scrolls = 0
        self.checkpoint_scans = 0
        self._react_ok = react_ok

    def current_post(self):
        return object()

    async def react(self, post, reaction):
        self.react_calls.append(reaction)
        return self._react_ok

    async def scroll_to_next_post(self):
        self.scrolls += 1

    async def checkpoint_detected(self):
        self.checkpoint_scans += 1
        return False


class StubWorker(Module1Worker):
    """Module1Worker with IO-ish collaborators stubbed for policy tests."""

    def __init__(self, settings: M1Settings):
        w = Module1Worker.__new__(Module1Worker)
        self.__dict__.update(w.__dict__)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pause_gate = threading.Event()
        self._pause_gate.set()
        self._legacy_mp_logged = False
        self._action_times = []
        self._snapshot = {
            "posts_seen": 1,
            "posts_engaged": 0,
            "posts_skipped": 0,
            "comments_done": 0,
            "likes_done": 0,
            "seen_count": 0,
            "phase": "scanning",
            "started_at": time_start(),
        }
        self.settings_ref = settings
        self.phases: list[tuple[str, str]] = []
        self.sleeps: list[float] = []
        self.checkpoints_handled = 0

    # stubs overriding worker methods
    async def _set_phase(self, phase, detail=None):  # type: ignore[override]
        self.phases.append((phase, detail or ""))

    def _set_snapshot(self, **kw):  # type: ignore[override]
        with self._lock:
            self._snapshot.update(kw)

    async def _sleep(self, seconds):  # type: ignore[override]
        self.sleeps.append(seconds)

    async def _checkpoint(self):  # type: ignore[override]
        return None

    async def _handle_checkpoint(self, fb, settings):  # type: ignore[override]
        self.checkpoints_handled += 1


def time_start():
    import time

    return time.time()


def _shot(tmp: Path) -> dict:
    f = tmp / "shot.png"
    f.write_bytes(b"\x89PNG fake")
    return {"ok": True, "screenshot": str(f), "url": "u", "kind": "video"}


class TestVideoPolicyGate(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_engage_falls_through_unconsumed(self) -> None:
        s = M1Settings(video_policy="engage")
        w = StubWorker(s)
        fb = FakeFb()
        with tempfile.TemporaryDirectory() as td:
            shot = _shot(Path(td))
            consumed = self._run(
                w._handle_video_policy(fb, s, FakeSeen(), "u", shot)
            )
            self.assertFalse(consumed)
            self.assertEqual(fb.scrolls, 0)
            self.assertTrue(Path(shot["screenshot"]).exists())  # main flow owns it

    def test_skip_consumes_and_never_marks_seen(self) -> None:
        s = M1Settings(video_policy="skip")
        w = StubWorker(s)
        fb = FakeFb()
        seen = FakeSeen()
        with tempfile.TemporaryDirectory() as td:
            shot = _shot(Path(td))
            consumed = self._run(w._handle_video_policy(fb, s, seen, "u", shot))
            self.assertFalse(Path(shot["screenshot"]).exists())  # deleted
        self.assertTrue(consumed)
        self.assertEqual(fb.scrolls, 1)
        self.assertEqual(seen.added, [])
        self.assertEqual(w._snapshot["posts_skipped"], 1)
        self.assertEqual(w._snapshot["posts_engaged"], 0)
        self.assertEqual(w.phases[-1][0], "skipping_video")

    def test_like_only_with_reaction_none_behaves_as_skip(self) -> None:
        s = M1Settings(video_policy="like_only", reaction_mode="none")
        w = StubWorker(s)
        fb = FakeFb()
        seen = FakeSeen()
        with tempfile.TemporaryDirectory() as td:
            shot = _shot(Path(td))
            consumed = self._run(w._handle_video_policy(fb, s, seen, "u", shot))
        self.assertTrue(consumed)
        self.assertEqual(fb.react_calls, [])       # never clicked
        self.assertEqual(seen.added, [])
        self.assertEqual(w._snapshot["posts_skipped"], 1)

    def test_like_only_success_full_bookkeeping(self) -> None:
        s = M1Settings(
            video_policy="like_only",
            reaction_mode="auto",
            long_break_every=1,
            long_break_min=0.01,
            long_break_max=0.02,
            gap_between_posts_min=0.01,
            gap_between_posts_max=0.02,
        )
        w = StubWorker(s)
        fb = FakeFb(react_ok=True)
        seen = FakeSeen()

        with tempfile.TemporaryDirectory() as td:
            shot = _shot(Path(td))
            consumed = self._run(w._handle_video_policy(fb, s, seen, "vid-url", shot))
            self.assertFalse(Path(shot["screenshot"]).exists())

        self.assertTrue(consumed)
        self.assertEqual(fb.react_calls, ["LIKE"])
        self.assertEqual(w._snapshot["likes_done"], 1)
        self.assertEqual(w._snapshot["posts_engaged"], 1)
        self.assertEqual(w._snapshot["last_reaction"], "LIKE")
        self.assertEqual(seen.added, ["vid-url"])
        self.assertEqual(len(w._action_times), 1)          # P05: one unit
        self.assertGreaterEqual(fb.checkpoint_scans, 1)     # safety net ran
        details = " ".join(d for _, d in w.phases)
        self.assertIn("Long human-like break", details)     # every=1 triggered

    def test_like_only_failed_click_counts_skip_not_engaged(self) -> None:
        s = M1Settings(video_policy="like_only")
        w = StubWorker(s)
        fb = FakeFb(react_ok=False)
        seen = FakeSeen()
        with tempfile.TemporaryDirectory() as td:
            shot = _shot(Path(td))
            consumed = self._run(w._handle_video_policy(fb, s, seen, "u", shot))
        self.assertTrue(consumed)
        self.assertEqual(w._snapshot["posts_engaged"], 0)
        self.assertEqual(w._snapshot["posts_skipped"], 1)
        self.assertEqual(seen.added, [])
        self.assertEqual(len(w._action_times), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
