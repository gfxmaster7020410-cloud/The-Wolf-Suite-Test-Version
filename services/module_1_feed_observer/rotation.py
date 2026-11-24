"""
Module 1 — Feed rotation controller (PHASE-13, decisions D7/D8/D9).

Operates across the home feed AND a saved list of group feeds with
owner-controlled ordering and per-source session budgets measured in
posts or minutes.

Design rules (roadmap P13):
- Decision logic is PURE with respect to IO: sources are rebuilt from the
  LIVE store on every call, so mid-run list edits apply on the next switch
  without restart; only wall-clock reads touch the outside world.
- Unknown/junk inputs degrade gracefully (fallback to ordered pick),
  never fatal — golden rule.
- State survives per identity: home is keyed by a fixed token so mid-run
  feed_url edits keep history; groups are keyed by normalized URL.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Optional

from services.module_1_feed_observer.config import M1Settings

HOME_KEY = "home"


@dataclass
class SourceState:
    """One navigable feed plus its live session counters."""

    kind: str          # "home" | "group"
    url: str
    key: str           # stable identity across live edits
    engaged: int = 0   # posts ENGAGED this visit (skips never count)
    since: float = 0.0  # navigation-completion wall-clock timestamp


class RotationController:
    """
    Live-settings-driven source picker + quota bookkeeping.

    Worker protocol per cycle:
        quota_exhausted(settings) -> advance() when the budget is spent
        current()                 -> active source (may flip -> nav pending)
        needs_navigation(src)     -> True after a flip / first discovery
        mark_arrived(src)         -> worker finished goto + settle cooldown
        record_engagement()       -> one post ENGAGED on the active source
    """

    def __init__(self, store: Any):
        self.store = store
        self.state: dict[str, SourceState] = {}
        self.order_cursor = 0
        self._nav_pending = True
        self._active_key = ""
        self._last_navigated_key = ""

    # ------------------------------------------------------------------ sources
    def _candidates(self, s: M1Settings) -> list[SourceState]:
        """Home first, then normalized/deduped group URLs (order kept)."""
        out = [SourceState("home", s.feed_url, HOME_KEY)]
        seen = {s.feed_url}
        for u in s.feed_group_urls or []:
            u2 = self._norm_group(u)
            if u2 and u2 not in seen:
                seen.add(u2)
                out.append(SourceState("group", u2, u2))
        return out

    @staticmethod
    def _norm_group(u: Any) -> str:
        """Strip query/fragment + trailing slash; drop junk/'home' entries."""
        s = str(u or "").strip()
        if not s or s.lower() == HOME_KEY:
            return ""
        return s.split("#", 1)[0].split("?", 1)[0].rstrip("/")

    # ------------------------------------------------------------------ protocol
    def current(self) -> Optional[SourceState]:
        s = self.store.get()
        srcs = self._candidates(s)
        if not srcs:
            return None
        picked = self._pick(s, srcs)
        st = self.state.get(picked.key)
        if st is None:
            st = picked
            self.state[picked.key] = st
            self._nav_pending = True  # never visited this session
        else:
            changed = st.url != picked.url or st.kind != picked.kind
            # Live truth wins: an edited feed_url/group URL must navigate.
            st.url = picked.url
            st.kind = picked.kind
            if changed:
                self._nav_pending = True
            elif picked.key == self._last_navigated_key:
                # Cursor re-picked the source we already occupy (e.g. a
                # single-home run cycling its quota): staying put is not a
                # navigation (roadmap edge case: 'same source -> no nav').
                self._nav_pending = False
        self._active_key = picked.key
        return st

    def needs_navigation(self, _cur: Optional[SourceState] = None) -> bool:
        return bool(self._nav_pending)

    def mark_arrived(self, cur: SourceState) -> None:
        cur.since = time.time()
        cur.engaged = 0
        self._nav_pending = False
        self._last_navigated_key = cur.key

    def record_engagement(self) -> None:
        cur = self.state.get(self._active_key)
        if cur is not None:
            cur.engaged += 1

    def quota_exhausted(self, s: M1Settings) -> bool:
        cur = self.state.get(self._active_key)
        if cur is None or cur.since <= 0:
            return False
        unit = (str(s.source_quota_unit or "posts")).strip().lower()
        if unit == "minutes":
            # Budget floor is 60 SECONDS total (roadmap sketch), not 60 min.
            budget = max(60, int(s.source_quota_value) * 60)
            return (time.time() - cur.since) >= budget
        return cur.engaged >= max(1, int(s.source_quota_value))

    def advance(self) -> None:
        self.order_cursor += 1
        self._nav_pending = True

    # ------------------------------------------------------------------ picking
    def _pick(self, s: M1Settings, srcs: list[SourceState]) -> SourceState:
        mode = (str(s.feed_rotation_mode or "ordered")).strip().lower()
        groups = [x for x in srcs if x.kind == "group"]
        home = srcs[0]

        if mode == "random":
            pool = [x for x in srcs if x.key != self._last_navigated_key]
            return random.choice(pool or srcs)

        if mode == "home_first":
            home_st = self.state.get(HOME_KEY)
            home_used = home_st.engaged if home_st else 0
            if home_used < max(1, int(s.feed_home_first_count)):
                return home
            if groups:
                return groups[self.order_cursor % len(groups)]
            return home  # no groups saved -> stay home forever

        if mode == "manual_queue":
            queue = [str(q).strip() for q in (s.feed_manual_queue or []) if str(q).strip()]
            if queue:
                entry = queue[self.order_cursor % len(queue)]
                is_home = entry.lower() == HOME_KEY
                if is_home:
                    match = home
                else:
                    want = self._norm_group(entry)
                    match = next(
                        (x for x in groups if x.url == want),
                        None,
                    )
                if match is not None:
                    return match
                # Unknown/unmatched entry -> degrade to ordered cycling.

        return srcs[self.order_cursor % len(srcs)]  # ordered (+ fallback)
