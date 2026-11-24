"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchHitlQueue, reviewHitl, seedHitlDemo, type HitlTask } from "@/lib/api";

export default function HitlPage() {
  const [items, setItems] = useState<HitlTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const queue = await fetchHitlQueue();
      setItems(queue);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load HITL queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onSeed() {
    setError(null);
    try {
      await seedHitlDemo();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Seed failed");
    }
  }

  async function onReview(taskId: string, approve: boolean) {
    setBusyId(taskId);
    setError(null);
    try {
      await reviewHitl(taskId, approve);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-8 font-normal">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold text-white mb-2">HITL Review Queue</h1>
          <p className="text-wolf-mist text-base font-normal">
            Live Module 6 queue — approve or reject before outbound execution.
          </p>
        </div>
        <button
          onClick={onSeed}
          className="px-4 py-2 rounded-lg border border-wolf-amber/40 text-wolf-amber text-sm font-medium hover:bg-wolf-amber/10 transition"
        >
          Seed demo escalation
        </button>
      </div>

      {error && (
        <p className="text-sm font-normal text-wolf-danger border border-wolf-danger/40 rounded-lg p-3">{error}</p>
      )}

      {loading ? (
        <p className="text-wolf-mist font-normal">Loading queue…</p>
      ) : items.length === 0 ? (
        <p className="text-wolf-mist font-normal">No pending HITL tasks. Seed a demo escalation to test.</p>
      ) : (
        <div className="space-y-4 font-normal">
          {items.map((item) => (
            <article
              key={item.task_id}
              className="rounded-xl border border-white/10 bg-wolf-steel/70 p-5 flex flex-col md:flex-row md:items-center justify-between gap-4"
            >
              <div>
                <p className="text-sm text-wolf-amber font-medium">{item.task_id}</p>
                <p className="text-white mt-1 font-medium">{item.account_id}</p>
                <p className="text-sm text-wolf-mist font-normal mt-1">
                  {item.reason.replaceAll("_", " ")}
                  {item.confidence != null
                    ? ` · confidence ${(item.confidence * 100).toFixed(0)}%`
                    : ""}
                </p>
              </div>
              <div className="flex gap-3">
                <button
                  disabled={busyId === item.task_id}
                  onClick={() => onReview(item.task_id, true)}
                  className="px-4 py-2 rounded-lg bg-wolf-signal text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 transition"
                >
                  Approve
                </button>
                <button
                  disabled={busyId === item.task_id}
                  onClick={() => onReview(item.task_id, false)}
                  className="px-4 py-2 rounded-lg border border-wolf-danger/60 text-wolf-danger text-sm font-medium hover:bg-wolf-danger/10 disabled:opacity-50 transition"
                >
                  Reject
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
