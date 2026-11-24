"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchKillEvents, issueKillSwitch, type KillSwitchEvent } from "@/lib/api";

export default function KillSwitchPage() {
  const [level, setLevel] = useState(1);
  const [accountId, setAccountId] = useState("acct_wolf_01");
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState<KillSwitchEvent[]>([]);

  const loadEvents = useCallback(async () => {
    try {
      const data = await fetchKillEvents();
      setEvents(data);
    } catch {
      /* backend may be offline on first paint */
    }
  }, []);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  async function issue() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const event = await issueKillSwitch({
        level,
        reason,
        account_id: level === 1 ? accountId : undefined,
        tenant_id: "org_wolf_demo",
      });
      const labels = {
        1: "Level 1 — Account pause",
        2: "Level 2 — Tenant workspace pause",
        3: "Level 3 — Global system freeze",
      } as const;
      setResult(
        `${labels[level as 1 | 2 | 3]} issued (${event.event_id}). Reason: ${event.reason}`
      );
      await loadEvents();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Kill-switch request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8 max-w-2xl font-normal">
      <div>
        <h1 className="text-3xl font-semibold text-white mb-2">Emergency Kill-Switch</h1>
        <p className="text-wolf-mist text-base font-normal">
          Live Module 6 controls — Pub/Sub halt signals to Module 4 dispatch.
        </p>
      </div>

      <div className="rounded-2xl border border-white/10 bg-wolf-steel/80 p-6 space-y-5">
        <label className="block text-sm font-medium text-wolf-mist">
          Kill level
          <select
            className="mt-2 w-full rounded-lg bg-wolf-ink border border-white/10 px-3 py-2 text-white font-normal"
            value={level}
            onChange={(e) => setLevel(Number(e.target.value))}
          >
            <option value={1}>Level 1 — Single profile</option>
            <option value={2}>Level 2 — Tenant workspace</option>
            <option value={3}>Level 3 — Global freeze</option>
          </select>
        </label>

        {level === 1 && (
          <label className="block text-sm font-medium text-wolf-mist">
            Account ID
            <input
              className="mt-2 w-full rounded-lg bg-wolf-ink border border-white/10 px-3 py-2 text-white font-normal"
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
            />
          </label>
        )}

        <label className="block text-sm font-medium text-wolf-mist">
          Reason
          <textarea
            className="mt-2 w-full rounded-lg bg-wolf-ink border border-white/10 px-3 py-2 text-white font-normal min-h-[100px]"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="CAPTCHA storm / compliance hold / operator intervention"
          />
        </label>

        <button
          onClick={issue}
          disabled={busy}
          className="w-full py-3 rounded-lg bg-wolf-danger text-white font-semibold hover:opacity-90 disabled:opacity-50 transition"
        >
          {busy ? "Issuing…" : "Issue Kill-Switch"}
        </button>

        {error && (
          <p className="text-sm font-normal text-wolf-danger border border-wolf-danger/40 rounded-lg p-3">
            {error}
          </p>
        )}
        {result && (
          <p className="text-sm font-normal text-wolf-amber border border-wolf-amber/30 rounded-lg p-3">
            {result}
          </p>
        )}
      </div>

      {events.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-white">Recent kill events</h2>
          {events
            .slice()
            .reverse()
            .map((ev) => (
              <div
                key={ev.event_id}
                className="rounded-xl border border-white/10 bg-wolf-steel/60 px-4 py-3 text-sm font-normal"
              >
                <span className="text-wolf-amber font-medium">{ev.event_id}</span>
                <span className="text-wolf-mist"> · L{ev.level} · </span>
                <span className="text-white">{ev.reason}</span>
              </div>
            ))}
        </section>
      )}
    </div>
  );
}
