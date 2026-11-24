"use client";

import { useEffect, useState } from "react";
import { fetchSuitePreview, type PreviewSession } from "@/lib/api";

function badgeClass(status: string) {
  if (status === "completed") return "bg-wolf-signal text-white";
  if (status === "running") return "bg-wolf-amber text-wolf-ink";
  if (status === "failed") return "bg-wolf-danger text-white";
  return "bg-white/10 text-wolf-mist";
}

export function LivePreviewPanel({
  sessionId,
  onDone,
}: {
  sessionId: string | null;
  onDone?: (session: PreviewSession) => void;
}) {
  const [session, setSession] = useState<PreviewSession | null>(null);

  useEffect(() => {
    if (!sessionId) {
      let alive = true;
      const tick = async () => {
        try {
          const s = await fetchSuitePreview("latest");
          if (alive && s && s.status !== "idle") setSession(s);
        } catch {
          /* ignore */
        }
      };
      tick();
      const t = setInterval(tick, 1500);
      return () => {
        alive = false;
        clearInterval(t);
      };
    }

    let alive = true;
    const poll = async () => {
      try {
        const s = await fetchSuitePreview(sessionId);
        if (!alive) return;
        setSession(s);
        if (s.status === "completed" || s.status === "failed") {
          onDone?.(s);
        }
      } catch {
        /* ignore */
      }
    };
    poll();
    const t = setInterval(poll, 500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [sessionId, onDone]);

  if (!session || session.status === "idle" || session.status === "not_found") {
    return (
      <section className="rounded-3xl border border-dashed border-white/15 bg-wolf-steel/40 p-8 font-normal">
        <p className="font-medium text-xs text-wolf-mist mb-1 uppercase tracking-wide">Live web preview</p>
        <h2 className="text-xl md:text-2xl font-semibold text-white">Waiting for a module run</h2>
        <p className="text-wolf-mist text-sm font-normal mt-2 max-w-xl leading-relaxed">
          Click any module below. Steps stream here in real time while Chromium may also open
          on your desktop.
        </p>
      </section>
    );
  }

  const live = session.status === "running";

  return (
    <section
      id="live-preview"
      className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-[#121820] via-[#0c1118] to-[#1a2332] p-6 md:p-8 font-normal"
    >
      <div className="flex flex-col md:flex-row md:items-start justify-between gap-4 mb-6">
        <div>
          <p className="font-medium text-xs text-wolf-amber mb-1 uppercase tracking-wide">Live web preview</p>
          <h2 className="text-2xl md:text-3xl font-semibold text-white">{session.title}</h2>
          <p className="text-wolf-mist text-sm font-normal mt-1.5">{session.subtitle}</p>
        </div>
        <div
          className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-xs font-semibold uppercase tracking-wide ${
            live
              ? "border-wolf-signal/50 bg-wolf-signal/15 text-emerald-300"
              : session.status === "failed"
                ? "border-wolf-danger/50 bg-wolf-danger/20 text-red-200"
                : "border-white/20 bg-white/5 text-wolf-mist"
          }`}
        >
          {live ? (
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
          ) : null}
          {session.status}
        </div>
      </div>

      {session.cards && session.cards.length > 0 ? (
        <div className="grid sm:grid-cols-3 gap-3 mb-6">
          {session.cards.map((c, i) => (
            <div
              key={`${c.label}-${i}`}
              className="rounded-2xl border border-white/10 bg-white/[0.03] p-4"
            >
              <p className="font-medium text-xs text-wolf-mist uppercase">{c.label}</p>
              <p className="text-white text-lg font-semibold mt-1">{c.value}</p>
              {c.sub ? <p className="text-wolf-amber text-xs font-normal mt-1">{c.sub}</p> : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className="rounded-2xl border border-white/10 bg-black/20 p-4 space-y-2">
        <div className="flex items-center justify-between mb-2">
          <p className="text-white font-semibold text-sm">Pipeline execution stream</p>
          <p className="text-wolf-mist text-xs font-medium uppercase">{session.module_id}</p>
        </div>
        {(session.steps || []).map((st, i) => (
          <div
            key={`${st.name}-${i}`}
            className="flex items-center justify-between gap-3 rounded-xl bg-wolf-steel/50 px-4 py-3 border-l-4"
            style={{
              borderLeftColor:
                st.status === "completed"
                  ? "#2d6a4f"
                  : st.status === "running"
                    ? "#c4a35a"
                    : st.status === "failed"
                      ? "#9b2226"
                      : "#3d4f66",
            }}
          >
            <div className="min-w-0">
              <p className="text-white text-sm font-medium">
                Step {i + 1}: {st.name}
              </p>
              <p className="text-wolf-mist text-xs font-normal mt-0.5 truncate">{st.detail}</p>
            </div>
            <span
              className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase ${badgeClass(st.status)}`}
            >
              {st.status}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
