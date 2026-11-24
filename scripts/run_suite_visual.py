"""
Wolf Suite — On-Screen Visual Module Runner.

Executes all modules one by one or a specific module in visual headful mode,
opening browser windows on screen so the user can watch every action live.

Examples:
  python scripts/run_suite_visual.py --module all
  python scripts/run_suite_visual.py --module m1
  python scripts/run_suite_visual.py --module m2
  python scripts/run_suite_visual.py --module m3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run_visual_module(module_id: str, account_id: str = "acct_wolf_01") -> dict:
    mid = module_id.lower().replace("module_", "").replace("m", "")
    full_mid = f"m{mid}"

    print(f"\n=======================================================")
    print(f"  LAUNCHING VISUAL SESSION FOR MODULE {full_mid.upper()}")
    print(f"=======================================================\n")

    if full_mid == "m1":
        from services.module_1_feed_observer.observer import Module1FeedObserver
        obs = Module1FeedObserver()
        res = await obs.run_observer_session(max_posts=1, account_id=account_id, dry_run=False)
        return {"module": "m1", "result": res}

    elif full_mid == "m2":
        from services.module_2_media_engine.media_processor import Module2MediaEngine
        eng = Module2MediaEngine()
        return await eng.run_visual_session(account_id=account_id)

    elif full_mid == "m3":
        from services.module_3_lead_funnel.fb_automation_module3 import FBAutomationModule3
        fun = FBAutomationModule3()
        return await fun.run_visual_session(account_id=account_id)

    elif full_mid == "m4":
        from services.module_4_orchestrator.dispatcher import orchestrator
        return await orchestrator.run_visual_session(account_id=account_id)

    elif full_mid == "m5":
        from services.module_5_analytics.telemetry import Module5TelemetryEngine
        tel = Module5TelemetryEngine()
        return await tel.run_visual_session(account_id=account_id)

    elif full_mid == "m6":
        from services.module_6_control_center.governance import control_center
        return await control_center.run_visual_session(account_id=account_id)

    elif full_mid == "m7":
        from services.module_7_webhooks.webhook_router import gateway
        return await gateway.run_visual_session()

    elif full_mid == "m8":
        from services.module_8_multi_agent.agent_orchestrator import Module8MultiAgentOrchestrator
        orc = Module8MultiAgentOrchestrator()
        return await orc.run_visual_session(account_id=account_id)

    else:
        raise ValueError(f"Unknown module target: {module_id}")


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Wolf Suite On-Screen Visual Module Runner")
    parser.add_argument(
        "--module",
        default="all",
        help="Module to run visually: m1, m2, m3, m4, m5, m6, m7, m8, or 'all'",
    )
    parser.add_argument("--account-id", default="acct_wolf_01", help="Account ID for caps and telemetry")
    args = parser.parse_args()

    target = args.module.lower()
    if target in {"all", "suite"}:
        modules = ["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"]
    else:
        modules = [target]

    results = {}
    for mod in modules:
        try:
            res = await run_visual_module(mod, account_id=args.account_id)
            results[mod] = {"status": "SUCCESS", "details": res}
            print(f"[OK] Module {mod.upper()} visual session complete.")
            await asyncio.sleep(1.5)
        except Exception as exc:
            print(f"[ERROR] Module {mod.upper()} visual session error: {exc}")
            results[mod] = {"status": "ERROR", "error": str(exc)}

    print("\n=======================================================")
    print("  WOLF SUITE ON-SCREEN VISUAL RUN COMPLETE")
    print("=======================================================")
    print(json.dumps(results, indent=2, default=str))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
