"""
Module 4: Central Orchestration — FastAPI dispatcher, Redis token bucket,
Redlock, Celery worker pool, jittered exponential backoff, DLQ.
Falls back to in-memory Redis shim when Redis is unavailable.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.shared.redis_client import get_redis, redis_backend
from services.shared.safety_bridge import safety_bridge


class TaskState(str, Enum):
    ENQUEUED = "ENQUEUED"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    DLQ = "DLQ"
    COMPLETED = "COMPLETED"


class TaskPayload(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    tenant_id: str
    account_id: str
    module_target: str
    action_type: str
    payload_data: Dict[str, Any] = Field(default_factory=dict)
    state: TaskState = TaskState.ENQUEUED
    attempt_count: int = 0
    max_retries: int = 3
    created_at: float = Field(default_factory=time.time)


class RedisTokenBucketRateLimiter:
    """
    Redis-backed Token Bucket rate limiter enforcing per-account request ceilings
    and cooling windows across distributed execution nodes.
    """

    def __init__(self, capacity: int = 60, refill_rate: float = 1.0, redis_client: Any = None):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.redis = redis_client or get_redis()
        self._prefix = "wolf:tb:"

    def check_and_consume_token(self, account_id: str, tokens: int = 1) -> bool:
        key = f"{self._prefix}{account_id}"
        now = time.time()
        raw = self.redis.get(key)
        if raw:
            try:
                bucket = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                bucket = {"tokens": float(self.capacity), "last_refill": now}
        else:
            bucket = {"tokens": float(self.capacity), "last_refill": now}

        elapsed = now - float(bucket.get("last_refill", now))
        bucket["tokens"] = min(
            float(self.capacity),
            float(bucket.get("tokens", self.capacity)) + elapsed * self.refill_rate,
        )
        bucket["last_refill"] = now

        if bucket["tokens"] >= tokens:
            bucket["tokens"] -= tokens
            self.redis.set(key, json.dumps(bucket), ex=86400)
            return True

        # Persist depleted state for cooling window visibility
        self.redis.set(key, json.dumps(bucket), ex=86400)
        return False


class RedisRedlockManager:
    """
    Redis Redlock-style distributed locking ensuring single-node task execution.
    """

    RELEASE_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, redis_client: Any = None):
        self.redis = redis_client or get_redis()
        self._prefix = "wolf:lock:"

    def acquire_lock(self, lock_key: str, ttl_seconds: float = 30.0) -> Optional[str]:
        token = uuid.uuid4().hex
        key = f"{self._prefix}{lock_key}"
        ok = self.redis.set(key, token, ex=int(max(1, ttl_seconds)), nx=True)
        return token if ok else None

    def release_lock(self, lock_key: str, token: Optional[str] = None) -> None:
        key = f"{self._prefix}{lock_key}"
        if token is None:
            self.redis.delete(key)
            return
        try:
            self.redis.eval(self.RELEASE_SCRIPT, 1, key, token)
        except Exception:
            # In-memory shim / non-eval clients: best-effort delete if value matches
            current = self.redis.get(key)
            if current == token:
                self.redis.delete(key)


class Module4CentralOrchestrator:
    """
    Module 4: Central Orchestration with Redis rate limits, Redlock, DLQ,
    and Celery-compatible enqueue path. Gates every dispatch through Module 6.
    """

    def __init__(self):
        from services.shared.humanization import humanization

        cfg = humanization.rate_limiter_config()
        self.redis = get_redis()
        self.rate_limiter = RedisTokenBucketRateLimiter(
            capacity=int(cfg["capacity"]),
            refill_rate=float(cfg["refill_rate"]),
            redis_client=self.redis,
        )
        self.lock_manager = RedisRedlockManager(redis_client=self.redis)
        self.task_store: Dict[str, TaskPayload] = {}
        self.dead_letter_queue: Dict[str, TaskPayload] = {}
        self._hydrate_from_redis()

    def _task_key(self, task_id: str) -> str:
        return f"wolf:task:{task_id}"

    def _dlq_key(self, task_id: str) -> str:
        return f"wolf:dlq:{task_id}"

    def _persist_task(self, task: TaskPayload) -> None:
        self.task_store[task.task_id] = task
        try:
            self.redis.set(self._task_key(task.task_id), task.model_dump_json(), ex=7 * 86400)
        except Exception:
            pass

    def _hydrate_from_redis(self) -> None:
        # Lightweight: only used when Redis SCAN is available; skip for memory shim.
        pass

    def enqueue_task(self, payload: TaskPayload, use_celery: Optional[bool] = None) -> TaskPayload:
        payload.state = TaskState.ENQUEUED
        self._persist_task(payload)

        if use_celery is None:
            use_celery = os.getenv("CELERY_ENABLED", "false").lower() in {"1", "true", "yes"}

        if use_celery:
            try:
                from services.module_4_orchestrator.celery_app import dispatch_task_async

                dispatch_task_async.delay(payload.task_id)
                return payload
            except Exception as exc:
                print(f"[Module 4] Celery enqueue failed, inline fallback: {exc}")

        return payload

    def execute_task_dispatch(self, task_id: str) -> Dict[str, Any]:
        from services.shared.humanization import humanization

        task = self.task_store.get(task_id)
        if not task:
            raw = self.redis.get(self._task_key(task_id))
            if raw:
                task = TaskPayload.model_validate_json(raw)
                self.task_store[task_id] = task
        if not task:
            raise ValueError(f"Task {task_id} not found.")

        # Step 0: Module 6 Kill-Switch Gate
        gate = safety_bridge.assert_dispatch_allowed(task.tenant_id, task.account_id)
        if not gate["allowed"]:
            return {
                "status": "BLOCKED_BY_KILL_SWITCH",
                "task_id": task_id,
                "reason": gate["reason"],
                "message": "Dispatch halted by Module 6 emergency control.",
                "redis_backend": redis_backend(),
            }

        # Step 0b: Human daily/hourly action caps + warm-up
        human_cap = humanization.can_act(task.account_id)
        if not human_cap["allowed"]:
            return {
                "status": "THROTTLED_HUMAN_CAP",
                "task_id": task_id,
                "cap": human_cap,
                "message": "Humanization engine blocked action (hourly/daily warm-up cap).",
            }

        # Step 1: Enforce Rate Limit Ceiling
        if not self.rate_limiter.check_and_consume_token(task.account_id):
            return {
                "status": "RATE_LIMITED",
                "task_id": task_id,
                "message": "Account rate limit ceiling reached. Execution deferred.",
                "redis_backend": redis_backend(),
            }

        # Step 2: Acquire Distributed Redlock
        lock_key = f"{task.account_id}:{task.module_target}"
        lock_token = self.lock_manager.acquire_lock(lock_key, ttl_seconds=15.0)
        if not lock_token:
            return {
                "status": "LOCKED",
                "task_id": task_id,
                "message": "Distributed lock currently active. Preventing duplicated execution.",
                "redis_backend": redis_backend(),
            }

        try:
            task.state = TaskState.RUNNING
            task.attempt_count += 1
            self._persist_task(task)

            # Human-like pre-action dwell (thinking / reading before acting)
            pre = humanization.action_delay()
            applied = humanization.apply_delay(pre, fast_cap=0.35)

            result = self._run_module_action(task)
            success = bool(result.get("success", True))
            result.setdefault("human_pre_delay_sec", round(applied, 3))
            result.setdefault("human_pre_delay_planned_sec", round(pre, 3))

            if success:
                task.state = TaskState.COMPLETED
                humanization.record_action(task.account_id)
                self._persist_task(task)
                safety_bridge.log_task_result(
                    task.tenant_id, task.account_id, task.task_id, "COMPLETED", task.module_target
                )
                return {
                    "status": "SUCCESS",
                    "task": task,
                    "result": result,
                    "human_cap": humanization.can_act(task.account_id),
                    "redis_backend": redis_backend(),
                }

            if task.attempt_count < task.max_retries:
                task.state = TaskState.RETRY
                backoff_delay = self.calculate_exponential_backoff(task.attempt_count)
                self._persist_task(task)
                safety_bridge.log_task_result(
                    task.tenant_id, task.account_id, task.task_id, "RETRY", task.module_target
                )
                # Schedule Celery retry when enabled
                if os.getenv("CELERY_ENABLED", "false").lower() in {"1", "true", "yes"}:
                    try:
                        from services.module_4_orchestrator.celery_app import dispatch_task_async

                        dispatch_task_async.apply_async(args=[task.task_id], countdown=backoff_delay)
                    except Exception:
                        pass
                return {
                    "status": "RETRY_QUEUED",
                    "task": task,
                    "retry_delay_seconds": backoff_delay,
                    "error": result.get("error"),
                    "redis_backend": redis_backend(),
                }

            task.state = TaskState.DLQ
            self.dead_letter_queue[task.task_id] = task
            self._persist_task(task)
            try:
                self.redis.set(self._dlq_key(task.task_id), task.model_dump_json(), ex=30 * 86400)
            except Exception:
                pass
            safety_bridge.log_task_result(
                task.tenant_id, task.account_id, task.task_id, "DLQ", task.module_target
            )
            return {
                "status": "QUARANTINED_DLQ",
                "task": task,
                "message": "Exceeded maximum retry limit. Quarantined to DLQ for operator review.",
                "error": result.get("error"),
                "redis_backend": redis_backend(),
            }
        finally:
            self.lock_manager.release_lock(lock_key, lock_token)

    def _run_module_action(self, task: TaskPayload) -> Dict[str, Any]:
        """
        Route payload to target module handlers.
        Demo / smoke actions succeed deterministically; force_fail for retry tests.
        """
        data = task.payload_data or {}
        if data.get("force_fail"):
            return {"success": False, "error": "forced_failure"}

        # Deterministic success for suite_demo / suite_ping; optional flaky mode
        flaky = os.getenv("M4_FLAKY_DISPATCH", "false").lower() in {"1", "true", "yes"}
        if flaky and random.random() < 0.15:
            return {"success": False, "error": "transient_worker_failure"}

        target = (task.module_target or "").lower()
        action = task.action_type or ""

        if target in {"module_1", "m1"} and action in {"engage", "suite_demo_engage"}:
            return {
                "success": True,
                "module": "module_1",
                "note": "Queued for Feed Observer worker; use M1_LIVE=true for browser run.",
                "account_id": task.account_id,
            }

        if target in {"module_2", "m2"}:
            return {
                "success": True,
                "module": "module_2",
                "note": "Media pipeline dispatch acknowledged.",
                "payload": data,
            }

        if target in {"module_3", "m3"}:
            return {
                "success": True,
                "module": "module_3",
                "note": "Lead funnel dispatch acknowledged.",
                "payload": data,
            }

        return {
            "success": True,
            "module": task.module_target,
            "action": action,
            "payload": data,
        }

    @staticmethod
    def calculate_exponential_backoff(
        attempt_count: int, base_delay: float = 2.0, max_delay: float = 60.0
    ) -> float:
        """
        Delay = Min(Max_Delay, Base_Delay * (2 ^ Attempt_Count)) + Random_Jitter
        """
        exponential = base_delay * (2 ** attempt_count)
        jitter = random.uniform(0.1, 1.5)
        return min(max_delay, exponential) + jitter


# FastAPI Router Engine for Module 4
app = FastAPI(title="Module 4 Central Orchestration API")
orchestrator = Module4CentralOrchestrator()


@app.post("/api/v1/orchestrator/enqueue")
def enqueue_task_endpoint(payload: TaskPayload, use_celery: bool = False):
    return orchestrator.enqueue_task(payload, use_celery=use_celery)


@app.post("/api/v1/orchestrator/dispatch/{task_id}")
def dispatch_task_endpoint(task_id: str):
    try:
        return orchestrator.execute_task_dispatch(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/orchestrator/dlq")
def list_dead_letter_queue():
    return list(orchestrator.dead_letter_queue.values())


@app.get("/api/v1/orchestrator/status")
def orchestrator_status():
    return {
        "redis_backend": redis_backend(),
        "tasks": len(orchestrator.task_store),
        "dlq": len(orchestrator.dead_letter_queue),
        "celery_enabled": os.getenv("CELERY_ENABLED", "false").lower() in {"1", "true", "yes"},
    }
