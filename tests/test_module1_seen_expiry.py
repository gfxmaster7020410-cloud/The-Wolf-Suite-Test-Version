"""Unit tests for Module 1 PHASE-09 — seen-store expiry (D14).

Entries older than seen_expiry_days stop blocking and are purged;
expiry=0 keeps pure legacy semantics. Covers startup purge, lazy drop on
contains(), boundary ages, future timestamps, legacy list-format files,
count-trim interplay, and the default-0 backward compatibility.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services.module_1_feed_observer import seen_store as ss_mod
from services.module_1_feed_observer.seen_store import SeenStore

DAY = 86400


def _seed(path: Path, entries: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


class TestSeenExpiry(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "seen.json"
        self.old_url = "https://www.facebook.com/posts/old"
        self.fresh_url = "https://www.facebook.com/posts/fresh"
        _seed(
            self.path,
            {
                self.old_url: time.time() - 40 * DAY,
                self.fresh_url: time.time() - 2 * DAY,
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_purge_removes_only_expired_and_rewrites_disk(self) -> None:
        store = SeenStore(path=self.path, expiry_days=30)
        purged = store.purge()
        self.assertEqual(purged, 1)
        self.assertFalse(store.contains(self.old_url))
        self.assertTrue(store.contains(self.fresh_url))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn(self.old_url, raw)
        self.assertIn(self.fresh_url, raw)

    def test_expiry_zero_keeps_everything_blocking(self) -> None:
        store = SeenStore(path=self.path, expiry_days=0)
        self.assertEqual(store.purge(), 0)
        self.assertTrue(store.contains(self.old_url))
        self.assertTrue(store.contains(self.fresh_url))

    def test_lazy_drop_via_contains_without_explicit_purge(self) -> None:
        store = SeenStore(path=self.path, expiry_days=30)
        # No purge() call: contains() itself must treat expired as absent
        self.assertFalse(store.contains(self.old_url))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn(self.old_url, raw)

    def test_boundary_age_strict_greater_than(self) -> None:
        just_inside = time.time() - (30 * DAY) + 60
        just_outside = time.time() - (30 * DAY) - 60
        p = Path(self._tmp.name) / "b.json"
        _seed(p, {"in": just_inside, "out": just_outside})
        store = SeenStore(path=p, expiry_days=30)
        self.assertTrue(store.contains("in"))     # honored through window
        self.assertFalse(store.contains("out"))   # strictly past it

    def test_future_timestamp_reads_fresh(self) -> None:
        p = Path(self._tmp.name) / "f.json"
        _seed(p, {"skew": time.time() + DAY})
        store = SeenStore(path=p, expiry_days=30)
        self.assertEqual(store.purge(), 0)
        self.assertTrue(store.contains("skew"))

    def test_legacy_list_format_loads_and_counts(self) -> None:
        p = Path(self._tmp.name) / "legacy.json"
        p.write_text(json.dumps(["u1", "u2"]), encoding="utf-8")
        store = SeenStore(path=p, expiry_days=30)
        self.assertEqual(store.count(), 2)
        self.assertEqual(store.purge(), 0)  # timestamps are now => fresh
        self.assertTrue(store.contains("u1"))

    def test_add_still_trims_by_count_after_expiry_layer(self) -> None:
        # LEGACY SOFT BOUND (documented, deliberately unchanged per P09):
        # cutoff keeps entries with ts >= oldest-of-excess, so steady state
        # is max_entries+1. Deterministic injected clock — real time.time()
        # can collide on rapid adds, which made this flaky once already.
        p = Path(self._tmp.name) / "trim.json"
        store = SeenStore(path=p, max_entries=2, expiry_days=30)

        clock = {"t": time.time()}  # anchored to NOW so post-restore
        # expiry checks still see fresh entries

        def spaced_time():
            clock["t"] += 10_000.0
            return clock["t"]

        with mock.patch(
            "services.module_1_feed_observer.seen_store.time.time", spaced_time
        ):
            for u in ("a", "b", "c", "d"):
                store.add(u)

        self.assertEqual(store.count(), 3)      # {b,c,d} = max+1 steady state
        self.assertTrue(store.contains("d"))    # newest always survives
        self.assertFalse(store.contains("a"))   # oldest evicted

    def test_default_construction_is_legacy_safe(self) -> None:
        # Module-level singleton and any other legacy callers construct
        # without expiry args: must behave exactly as before (never expire).
        s = SeenStore()
        self.assertEqual(s._expiry_sec, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
