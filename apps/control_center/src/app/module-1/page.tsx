"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchModule1Config,
  fetchModule1Status,
  startModule1,
  stopModule1,
  type Module1Config,
  type Module1Status,
} from "@/lib/api";

export default function Module1DashboardPage() {
  const [config, setConfig] = useState<Module1Config | null>(null);
  const [status, setStatus] = useState<Module1Status>({
    running: false,
    comments: 0,
    likes: 0,
  });
  const [waitTime, setWaitTime] = useState(3);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadConfig = useCallback(async () => {
    try {
      const data = await fetchModule1Config();
      setConfig(data);
      if (typeof data.wait_time === "number") setWaitTime(data.wait_time);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Config load failed");
    }
  }, []);

  const pollStatus = useCallback(async () => {
    try {
      const s = await fetchModule1Status();
      setStatus(s);
    } catch {
      /* ignore transient poll errors */
    }
  }, []);

  useEffect(() => {
    loadConfig();
    pollStatus();
  }, [loadConfig, pollStatus]);

  useEffect(() => {
    const id = setInterval(pollStatus, 1500);
    return () => clearInterval(id);
  }, [pollStatus]);

  async function handleStart() {
    setBusy(true);
    setError(null);
    try {
      const res = await startModule1(waitTime);
      if (res.error) setError(res.error);
      setStatus(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Start failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    setBusy(true);
    setError(null);
    try {
      const res = await stopModule1();
      setStatus(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Stop failed");
    } finally {
      setBusy(false);
    }
  }

  const running = !!status.running;

  return (
    <div className="space-y-8 font-normal">
      <section className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-[#1a2332] via-[#121820] to-[#0c1118] px-8 py-10 md:px-12">
        <div
          className="pointer-events-none absolute inset-0 opacity-30"
          style={{
            backgroundImage:
              "radial-gradient(circle at 18% 20%, rgba(196,163,90,0.2), transparent 42%), radial-gradient(circle at 85% 10%, rgba(45,106,79,0.22), transparent 38%)",
          }}
        />
        <div className="relative flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
          <div className="max-w-2xl">
            <div className="mb-2 flex items-center gap-3">
              <span className="rounded-md border border-wolf-amber/30 bg-wolf-amber/20 px-2.5 py-1 text-xs font-medium uppercase tracking-wide text-wolf-amber">
                Module 1
              </span>
              <span
                className={`text-xs font-medium ${running ? "text-emerald-400" : "text-wolf-mist"}`}
              >
                {running ? `● Running · ${status.phase || "loop"}` : "○ Idle"}
              </span>
            </div>
            <h1 className="mb-3 text-3xl font-semibold leading-tight text-white md:text-4xl">
              Facebook Auto Engagement Bot
            </h1>
            <p className="text-base leading-relaxed text-wolf-mist">
              Existing Chrome profile → Facebook → slow scroll → full-screen shot → Gemini API
              comment → paste + react → unlimited loop.
            </p>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row">
            <button
              onClick={handleStart}
              disabled={busy || running}
              className="rounded-xl bg-wolf-amber px-6 py-3 font-semibold text-wolf-ink transition hover:opacity-90 disabled:opacity-45"
            >
              {running ? "Running…" : "Start"}
            </button>
            <button
              onClick={handleStop}
              disabled={busy || !running}
              className="rounded-xl border border-wolf-danger/50 bg-wolf-danger/15 px-6 py-3 font-semibold text-red-300 transition hover:bg-wolf-danger/25 disabled:opacity-45"
            >
              Stop
            </button>
            <a
              href="/"
              className="rounded-xl border border-white/20 px-6 py-3 text-center text-sm font-medium text-white transition hover:bg-white/5"
            >
              Back to Suite
            </a>
          </div>
        </div>
      </section>

      {error && (
        <p className="rounded-xl border border-wolf-danger/40 p-3 text-sm text-wolf-danger">
          {error}
        </p>
      )}

      {/* Live analytics */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-2xl border border-white/10 bg-wolf-steel/70 p-6">
          <p className="text-xs font-medium uppercase tracking-wide text-wolf-mist">Comments</p>
          <p className="mt-2 text-4xl font-semibold text-wolf-amber">{status.comments ?? 0}</p>
          <p className="mt-1 text-xs text-wolf-mist">Posted this session</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-wolf-steel/70 p-6">
          <p className="text-xs font-medium uppercase tracking-wide text-wolf-mist">Likes / Reacts</p>
          <p className="mt-2 text-4xl font-semibold text-emerald-400">{status.likes ?? 0}</p>
          <p className="mt-1 text-xs text-wolf-mist">Reactions this session</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-wolf-steel/70 p-6">
          <p className="text-xs font-medium uppercase tracking-wide text-wolf-mist">Posts Seen</p>
          <p className="mt-2 text-4xl font-semibold text-white">{status.posts_seen ?? 0}</p>
          <p className="mt-1 text-xs text-wolf-mist">Unlimited loop · no 20-cap</p>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-3">
        <section className="space-y-5 rounded-2xl border border-white/10 bg-wolf-steel/70 p-6 md:col-span-2">
          <h2 className="text-xl font-semibold text-white">Settings</h2>

          <div className="rounded-xl border border-white/10 bg-black/20 p-4">
            <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-wolf-mist">
              Wait Time (seconds)
            </label>
            <input
              type="number"
              min={0}
              step={0.5}
              value={waitTime}
              disabled={running}
              onChange={(e) => setWaitTime(Math.max(0, parseFloat(e.target.value) || 0))}
              className="w-full rounded-lg border border-white/15 bg-wolf-ink px-3 py-2 text-lg font-medium text-white focus:border-wolf-amber focus:outline-none disabled:opacity-50"
            />
            <p className="mt-2 text-xs text-wolf-mist">
              Facebook load hone ke baad, pehle scroll se pehle wait. Default: 3 seconds.
            </p>
          </div>

          <div className="rounded-xl border border-white/10 bg-wolf-ink/40 p-5">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-white">
              Loop steps
            </h3>
            <ol className="grid gap-2 text-xs text-wolf-mist sm:grid-cols-2">
              <li>1. Existing Chrome profile open</li>
              <li>2. Facebook.com load</li>
              <li>3. Wait Time → slow human scroll</li>
              <li>4. Full Facebook screen screenshot</li>
              <li>5. Gemini API → 1-line comment (no #)</li>
              <li>6–7. Paste comment + Enter</li>
              <li>8. Like / relevant React</li>
              <li>9. Wait 3–5s → next post (unlimited)</li>
            </ol>
          </div>

          {status.last_comment ? (
            <div className="rounded-xl border border-white/10 bg-black/20 p-4">
              <p className="text-xs font-medium uppercase text-wolf-mist">Last comment</p>
              <p className="mt-2 text-sm text-white">{status.last_comment}</p>
            </div>
          ) : null}

          {status.last_error ? (
            <div className="rounded-xl border border-wolf-danger/30 bg-wolf-danger/10 p-4">
              <p className="text-xs font-medium uppercase text-red-300">Last error</p>
              <p className="mt-2 text-sm text-red-200">{status.last_error}</p>
            </div>
          ) : null}
        </section>

        <section className="space-y-4 rounded-2xl border border-white/10 bg-wolf-steel/70 p-6">
          <h2 className="text-xl font-semibold text-white">Environment</h2>
          {config ? (
            <div className="space-y-3 text-xs">
              <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                <p className="font-medium uppercase text-wolf-mist">Chrome profile</p>
                <p className="mt-1 break-all font-mono text-[11px] text-white">
                  {config.user_data_dir || "data/browser_profiles/facebook"}
                </p>
              </div>
              <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                <p className="font-medium uppercase text-wolf-mist">Gemini</p>
                <p className="mt-1 font-mono text-[11px] text-white">
                  API · {config.gemini_mode}
                </p>
              </div>
              <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                <p className="font-medium uppercase text-wolf-mist">Loop</p>
                <p className="mt-1 font-semibold text-emerald-400">Unlimited</p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-wolf-mist">Loading…</p>
          )}
        </section>
      </div>
    </div>
  );
}
