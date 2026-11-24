"""
Wolf Suite — Module 1 Feed Observer continuous runner (Phase 4)

Cycle:
  1) capture_feed_post_screenshot()
  2) gemini_bridge.generate_comment(screenshot)
  3) log comment
  4) engage_post(comment)
  5) scroll + human read dwell → next post

Examples:
  python scripts/run_feed_observer.py
  python scripts/run_feed_observer.py --max-posts 5
  python scripts/run_feed_observer.py --dry-run --max-posts 2
  python scripts/run_feed_observer.py --once
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run_loop(
    max_posts: int | None,
    dry_run: bool,
    facebook_url: str | None,
) -> int:
    from services.module_1_feed_observer.gemini_bridge import GeminiWebBridge
    from services.module_1_feed_observer.observer import FeedObserver
    from services.shared.humanization import humanization

    observer = FeedObserver(facebook_url=facebook_url)
    bridge: GeminiWebBridge | None = None
    processed = 0

    try:
        await observer.start()
        assert observer.context is not None

        bridge = GeminiWebBridge(observer.context)
        await bridge.ensure_ready()

        print("[Runner] Starting continuous Feed Observer loop…")
        print("[Runner] Ctrl+C to stop.\n")

        while True:
            if max_posts is not None and processed >= max_posts:
                print(f"[Runner] Reached max_posts={max_posts}. Stopping.")
                break

            shot_path = None
            try:
                # Step 1 — Capture
                print(f"[Runner] Step 1 · Capture post #{processed + 1}")
                shot_path = await observer.capture_feed_post_screenshot()

                # Step 2 — Gemini
                print("[Runner] Step 2 · Gemini Web comment generation")
                try:
                    comment = await bridge.generate_comment(shot_path)
                except Exception as gemini_exc:
                    print(f"[Runner] Gemini failed — skip post & scroll: {gemini_exc}")
                    await observer.scroll_to_next_post()
                    await asyncio.sleep(random.uniform(3.0, 6.0))
                    continue

                # Step 3 — Log
                print(f"[Runner] Step 3 · Comment: {comment}")

                # Step 4 — Engage
                if dry_run:
                    print("[Runner] Step 4 · Dry-run — skip engage")
                else:
                    print("[Runner] Step 4 · Humanized engage on Facebook")
                    await observer.page.bring_to_front()  # type: ignore[union-attr]
                    ok = await observer.engage_post(comment)
                    print(f"[Runner] Engage result: {ok}")

                processed += 1

                # Step 5 — Scroll + read dwell before next capture
                print("[Runner] Step 5 · Scroll to next post / read dwell")
                await observer.scroll_to_next_post()
                dwell = random.uniform(5.0, 15.0)
                if humanization.simulate_fast():
                    dwell = min(dwell, 2.0)
                print(f"[Runner] Waiting {dwell:.1f}s before next capture…")
                await asyncio.sleep(dwell)

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                msg = str(exc).lower()
                print(f"[Runner] ERROR: {exc}")
                if any(
                    k in msg
                    for k in ("login", "unauthenticated", "logged out", "closed", "target")
                ):
                    print("[Runner] Unrecoverable session error — breaking loop.")
                    break
                # Recoverable: try next post
                try:
                    await observer.scroll_to_next_post()
                except Exception:
                    pass
                await asyncio.sleep(3.0)
            finally:
                if shot_path:
                    try:
                        Path(shot_path).unlink(missing_ok=True)
                    except OSError:
                        pass

        print(f"[Runner] Done. Posts processed: {processed}")
        return 0
    except KeyboardInterrupt:
        print("\n[Runner] Interrupted by user.")
        return 0
    except Exception as exc:
        print(f"[Runner] Fatal: {exc}")
        return 1
    finally:
        if bridge is not None:
            await bridge.close()
        await observer.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Wolf Suite Module 1 continuous Feed Observer")
    parser.add_argument(
        "--max-posts",
        type=int,
        default=None,
        help="Stop after N posts (default: infinite until Ctrl+C)",
    )
    parser.add_argument(
        "--posts",
        type=int,
        default=None,
        help="Alias for --max-posts",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process exactly one post then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Capture + Gemini only (no Facebook comment)",
    )
    parser.add_argument("--url", default=None, help="Facebook URL override")
    args = parser.parse_args()

    max_posts = args.max_posts if args.max_posts is not None else args.posts
    if args.once:
        max_posts = 1
    if max_posts is None and os.getenv("M1_SUITE_POSTS"):
        try:
            max_posts = int(os.getenv("M1_SUITE_POSTS", "0")) or None
        except ValueError:
            max_posts = None

    return asyncio.run(
        run_loop(
            max_posts=max_posts,
            dry_run=args.dry_run,
            facebook_url=args.url,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
