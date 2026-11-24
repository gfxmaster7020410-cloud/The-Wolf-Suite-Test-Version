"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchSuiteOverview,
  runSuiteModule,
  updateHumanization,
  type SuiteModule,
  type SuiteOverview,
} from "@/lib/api";
import { LivePreviewPanel } from "@/components/LivePreviewPanel";

function statusColor(status: string) {
  if (status === "online" || status === "ready") return "bg-wolf-signal";
  if (status === "freeze" || status === "gated") return "bg-wolf-danger";
  return "bg-wolf-mist";
}

export default function WolfSuiteMainScreen() {
  const [suite, setSuite] = useState<SuiteOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [runningAll, setRunningAll] = useState(false);
  const [previewSessionId, setPreviewSessionId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchSuiteOverview();
      setSuite(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load Wolf Suite");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  async function onRun(mod: SuiteModule) {
    setBusyId(mod.id);
    setError(null);
    setLog((prev) => [
      `${new Date().toLocaleTimeString()} · ${mod.name}: starting web preview…`,
      ...prev.slice(0, 11),
    ]);
    try {
      const result = await runSuiteModule(mod.id);
      if (result.preview_session_id) {
        setPreviewSessionId(result.preview_session_id);
        document.getElementById("live-preview")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      setLog((prev) => [
        `${new Date().toLocaleTimeString()} · ${mod.name}: preview ${result.preview_session_id || "started"}`,
        ...prev.slice(0, 11),
      ]);
      setTimeout(() => setBusyId(null), 800);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : `Run failed for ${mod.id}`);
      setBusyId(null);
    }
  }

  async function onRunAll() {
    if (!suite) return;
    setRunningAll(true);
    setError(null);
    const order = ["m8", "m4", "m2", "m5", "m6", "m7", "m1", "m3", "ai_gateway", "security"];
    for (const id of order) {
      const mod = suite.modules.find((m) => m.id === id);
      if (!mod) continue;
      setBusyId(id);
      try {
        const result = await runSuiteModule(id);
        if (result.preview_session_id) setPreviewSessionId(result.preview_session_id);
        if (result.preview_session_id) {
          for (let i = 0; i < 120; i++) {
            await new Promise((r) => setTimeout(r, 500));
            const { fetchSuitePreview } = await import("@/lib/api");
            const s = await fetchSuitePreview(result.preview_session_id);
            if (s.status === "completed" || s.status === "failed") break;
          }
        }
        setLog((prev) => [
          `${new Date().toLocaleTimeString()} · ${mod.name}: ok`,
          ...prev.slice(0, 11),
        ]);
      } catch (e) {
        setLog((prev) => [
          `${new Date().toLocaleTimeString()} · ${mod.name}: failed`,
          ...prev.slice(0, 11),
        ]);
      }
    }
    setBusyId(null);
    setRunningAll(false);
    await load();
  }

  return (
    <div className="space-y-8 font-normal">
      {/* Brand + CTA */}
      <section className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-[#1a2332] via-[#121820] to-[#0c1118] px-8 py-10 md:px-12 md:py-12">
        <div
          className="pointer-events-none absolute inset-0 opacity-40"
          style={{
            backgroundImage:
              "radial-gradient(circle at 20% 20%, rgba(196,163,90,0.18), transparent 40%), radial-gradient(circle at 80% 0%, rgba(45,106,79,0.2), transparent 35%)",
          }}
        />
        <div className="relative flex flex-col md:flex-row md:items-end justify-between gap-8">
          <div className="max-w-2xl">
            <p className="font-medium text-xs text-wolf-mist mb-2 uppercase tracking-wide">
              Enterprise automation platform
            </p>
            <h1 className="font-semibold text-4xl md:text-5xl text-white leading-tight mb-3">
              Wolf Suite
            </h1>
            <p className="text-wolf-mist text-base font-normal leading-relaxed">
              One main screen. Click any module to open a live browser window and watch
              every step on screen — feed, media, leads, orchestration, analytics,
              governance, webhooks, multi-agent, AI gateway, and security.
            </p>
            {suite && (
              <p className="mt-4 text-sm font-normal">
                Dispatch:{" "}
                <span className={`font-medium ${suite.dispatch_allowed ? "text-wolf-signal" : "text-wolf-danger"}`}>
                  {suite.dispatch_allowed ? "open" : "gated"}
                </span>
                {suite.global_freeze ? (
                  <span className="ml-3 text-wolf-danger font-medium text-xs tracking-wide uppercase">
                    Global freeze
                  </span>
                ) : null}
              </p>
            )}
          </div>
          <div className="flex flex-col sm:flex-row gap-3">
            <button
              onClick={onRunAll}
              disabled={runningAll || !suite}
              className="px-6 py-3 rounded-xl bg-wolf-amber text-wolf-ink font-semibold hover:opacity-90 disabled:opacity-50 transition"
            >
              {runningAll ? "Running suite…" : "Run all modules"}
            </button>
            <button
              onClick={load}
              className="px-6 py-3 rounded-xl border border-white/20 text-white text-sm font-medium hover:bg-white/5 transition"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      {error && (
        <p className="text-sm font-normal text-wolf-danger border border-wolf-danger/40 rounded-xl p-3">
          {error}
        </p>
      )}

      <LivePreviewPanel
        sessionId={previewSessionId}
        onDone={() => {
          load();
        }}
      />

      {/* Humanization control */}
      {suite?.humanization && (
        <section className="rounded-2xl border border-wolf-amber/30 bg-gradient-to-r from-wolf-steel/90 to-wolf-ink p-6 md:p-8 space-y-5">
          <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
            <div>
              <p className="font-medium text-xs text-wolf-amber mb-1 uppercase tracking-wide">
                Humanization engine
              </p>
              <h2 className="text-2xl md:text-3xl font-semibold text-white">Run like a human</h2>
              <p className="text-wolf-mist text-sm font-normal mt-1.5 max-w-xl leading-relaxed">
                Bezier cursor paths, Gaussian typing, reading dwell, Spintax, warm-up caps, and
                natural delays are active across Modules 1–4.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {(["cautious", "natural", "active"] as const).map((name) => (
                <button
                  key={name}
                  onClick={async () => {
                    try {
                      await updateHumanization({ profile_name: name });
                      await load();
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "Profile update failed");
                    }
                  }}
                  className={`px-4 py-2 rounded-lg text-sm font-medium capitalize border transition ${
                    suite.humanization?.profile_name === name
                      ? "bg-wolf-amber text-wolf-ink border-wolf-amber font-semibold"
                      : "border-white/15 text-wolf-mist hover:text-white"
                  }`}
                >
                  {name}
                </button>
              ))}
            </div>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-sm font-normal">
            <div className="rounded-xl bg-black/20 border border-white/10 p-4">
              <p className="text-wolf-mist text-xs font-medium uppercase tracking-wide">Warm-up</p>
              <p className="text-white text-xl font-semibold mt-1">
                Day {suite.humanization.cap?.warmup_day ?? 0}
              </p>
              <p className="text-wolf-mist text-xs mt-1 font-normal">
                ×{(suite.humanization.warmup_multiplier ?? 1).toFixed(2)} capacity
              </p>
            </div>
            <div className="rounded-xl bg-black/20 border border-white/10 p-4">
              <p className="text-wolf-mist text-xs font-medium uppercase tracking-wide">Hourly cap</p>
              <p className="text-white text-xl font-semibold mt-1">
                {suite.humanization.cap?.hourly_used ?? 0}/
                {suite.humanization.effective_max_actions_per_hour}
              </p>
            </div>
            <div className="rounded-xl bg-black/20 border border-white/10 p-4">
              <p className="text-wolf-mist text-xs font-medium uppercase tracking-wide">Typing</p>
              <p className="text-white text-base font-medium mt-1">
                {Math.round((suite.humanization.key_delay_min || 0.08) * 1000)}–
                {Math.round((suite.humanization.key_delay_max || 0.22) * 1000)}ms
              </p>
              <p className="text-wolf-mist text-xs mt-1 font-normal">
                typos {(suite.humanization.typo_probability * 100).toFixed(0)}%
              </p>
            </div>
            <div className="rounded-xl bg-black/20 border border-white/10 p-4">
              <p className="text-wolf-mist text-xs font-medium uppercase tracking-wide">Action delay</p>
              <p className="text-white text-base font-medium mt-1">
                {suite.humanization.action_delay_min}–{suite.humanization.action_delay_max}s
              </p>
              <p className="text-wolf-mist text-xs mt-1 font-normal">
                groups {suite.humanization.group_share_delay_min}–
                {suite.humanization.group_share_delay_max}s
              </p>
            </div>
          </div>

          <ul className="grid md:grid-cols-2 gap-2 text-xs font-normal text-wolf-mist">
            {(suite.humanization.behaviors || []).map((b) => (
              <li key={b} className="flex gap-2">
                <span className="text-wolf-signal">●</span>
                <span>{b}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Connection strip */}
      {suite && (
        <section className="rounded-2xl border border-white/10 bg-wolf-steel/40 px-5 py-4 overflow-x-auto font-normal">
          <p className="font-medium text-xs text-wolf-mist mb-2 uppercase tracking-wide">Module graph</p>
          <div className="flex items-center gap-2 text-xs md:text-sm text-wolf-mist whitespace-nowrap min-w-max font-medium">
            <span className="text-wolf-amber">M8</span>
            <span>→</span>
            <span className="text-wolf-amber">M4</span>
            <span>→</span>
            <span>M1 / M2 / M3</span>
            <span className="mx-2 text-white/20">|</span>
            <span className="text-wolf-amber">M5</span>
            <span>→</span>
            <span className="text-wolf-amber">M6</span>
            <span>→</span>
            <span>M4 gate</span>
            <span className="mx-2 text-white/20">|</span>
            <span>AI Gateway</span>
            <span>+</span>
            <span>Security</span>
          </div>
          <p className="mt-2 text-xs text-wolf-mist font-normal">
            {suite.connections.length} live links · tenant {suite.tenant_id}
          </p>
        </section>
      )}

      {/* Module grid */}
      <section className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {(suite?.modules || []).map((mod) => (
          <article
            key={mod.id}
            id={`module-${mod.id}`}
            className="rounded-2xl border border-white/10 bg-wolf-steel/70 p-5 flex flex-col gap-4 hover:border-wolf-amber/40 transition"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-medium text-xs text-wolf-mist uppercase">{mod.id}</p>
                <h2 className="text-xl font-semibold text-white mt-1">{mod.name}</h2>
              </div>
              <span className="flex items-center gap-2 text-xs font-medium text-wolf-mist">
                <span className={`h-2 w-2 rounded-full ${statusColor(mod.status)}`} />
                {mod.status}
              </span>
            </div>
            <p className="text-sm font-normal text-wolf-mist leading-relaxed flex-1">{mod.role}</p>
            {mod.metric ? <p className="text-xs font-medium text-wolf-amber">{mod.metric}</p> : null}
            {mod.depends_on.length > 0 ? (
              <p className="text-[11px] font-normal text-wolf-mist">
                Depends: {mod.depends_on.join(" · ")}
              </p>
            ) : (
              <p className="text-[11px] font-normal text-wolf-mist">Foundation layer</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => onRun(mod)}
                disabled={busyId === mod.id || runningAll}
                className="flex-1 py-2 rounded-lg bg-white/10 text-white text-sm font-medium hover:bg-white/15 disabled:opacity-50 transition"
              >
                {busyId === mod.id ? "Running…" : "Run"}
              </button>
              {(mod.href.startsWith("/") || mod.href === "/hitl" || mod.href === "/kill-switch") && (
                <a
                  href={mod.href}
                  className="px-3 py-2 rounded-lg border border-white/15 text-sm font-medium text-wolf-mist hover:text-white transition"
                >
                  Open
                </a>
              )}
            </div>
          </article>
        ))}
      </section>

      {!suite && !error && <p className="text-wolf-mist font-normal">Loading Wolf Suite…</p>}

      {/* Live activity */}
      <section className="rounded-2xl border border-white/10 bg-wolf-ink/60 p-6 font-normal">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-white">Suite activity</h2>
          <div className="flex gap-3 text-sm font-medium">
            <a href="/hitl" className="text-wolf-amber hover:underline">
              HITL queue
            </a>
            <a href="/kill-switch" className="text-wolf-amber hover:underline">
              Kill switch
            </a>
          </div>
        </div>
        {log.length === 0 ? (
          <p className="text-sm text-wolf-mist font-normal">
            Run a module or “Run all modules” to see live results here.
          </p>
        ) : (
          <ul className="space-y-2 font-mono text-xs text-wolf-mist font-normal">
            {log.map((line, i) => (
              <li key={`${i}-${line.slice(0, 24)}`} className="border-b border-white/5 pb-2">
                {line}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
