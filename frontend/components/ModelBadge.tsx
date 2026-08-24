"use client";

import { useState } from "react";
import type { HealthResponse, ModelInfo } from "@/lib/types";

/**
 * Model indicator.
 *
 * The active provider is always visible, and it tells the truth when the
 * model is unreachable — an evaluator running the demo without `ollama serve`
 * should learn that here, not from a failed message.
 */
export default function ModelBadge({
  model,
  health,
  onConfigure,
}: {
  model: ModelInfo | null;
  health: HealthResponse | null;
  onConfigure?: () => void;
}) {
  const [open, setOpen] = useState(false);

  if (!model) {
    return <span className="chip animate-pulse-dot">Checking model…</span>;
  }

  const state = !model.available ? "down" : health?.status === "degraded" ? "degraded" : "ok";
  const dot =
    state === "ok" ? "bg-ok" : state === "degraded" ? "bg-warn" : "bg-danger";

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="chip hover:text-ink"
        title="Active model and dependency status"
      >
        <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
        {/* Truncated on phones so it cannot squeeze the product name out of
            the header; the full label is in the panel below. */}
        <span className="max-w-[7.5rem] truncate font-mono sm:max-w-none">{model.label}</span>
      </button>

      {open && (
        <div className="card absolute right-0 z-20 mt-2 w-80 p-3 text-sm shadow-lg">
          <dl className="space-y-1.5">
            <Row label="Provider" value={model.provider} />
            <Row label="Model" value={model.model} mono />
            {model.cloud_provider && <Row label="Cloud vendor" value={model.cloud_provider} />}
            <Row
              label="Embeddings"
              value={`${model.embedding_provider} · ${model.embedding_model}`}
            />
            {health &&
              Object.entries(health.components).map(([name, component]) => (
                <Row
                  key={name}
                  label={name}
                  value={`${component.status}${component.detail ? ` — ${component.detail}` : ""}`}
                />
              ))}
          </dl>
          {!model.available && (
            <p className="mt-2 rounded-lg bg-surface-sunken p-2 text-xs text-warn">
              {model.detail}
              {model.fallback ? ` ${model.fallback}` : ""}
            </p>
          )}
          {model.source === "runtime" && (
            <p className="mt-2 rounded-lg bg-surface-sunken p-2 text-xs text-ink-muted">
              Configured from this UI. Restarting the backend restores the{" "}
              <code className="font-mono">.env</code> configuration.
            </p>
          )}

          {model.configurable && onConfigure ? (
            <button
              className="btn mt-3 w-full justify-center px-2.5 py-1.5 text-xs"
              onClick={() => {
                setOpen(false);
                onConfigure();
              }}
            >
              Configure model / connect a cloud provider
            </button>
          ) : (
            <p className="mt-2 text-xs text-ink-faint">
              Switch models with <code className="font-mono">LLM_PROVIDER</code> in{" "}
              <code className="font-mono">.env</code> and restart the backend.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-xs uppercase tracking-wide text-ink-faint">{label}</dt>
      <dd className={`text-right text-xs text-ink-muted ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}
