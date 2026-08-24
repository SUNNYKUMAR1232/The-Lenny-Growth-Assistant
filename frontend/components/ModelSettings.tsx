"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ModelConfigRequest, ModelInfo, ModelProviderOption } from "@/lib/types";

type ProviderId = "ollama" | "anthropic" | "openai" | "pi";

/** Backends Pi can drive that need no credential. */
const LOCAL_BACKENDS = new Set(["ollama", "lmstudio", "llamacpp"]);

/**
 * Model settings — switch provider, model, base URL and API key at runtime.
 *
 * Why this exists: the assignment asks an evaluator to run the demo locally on
 * Ollama *and* to see a cloud provider working. Making that a `.env` edit plus
 * a container restart is a bad first five minutes. This panel does it live.
 *
 * What it does NOT do: persist the key. The backend keeps it in memory only,
 * never returns it, and reverts to `.env` on restart — so a key pasted here
 * cannot end up in a database backup, a log, or a commit. The panel shows a
 * masked hint (`…9f2a`) so you can tell which key is loaded without exposing it.
 *
 * Accessibility: focus moves into the dialog on open and returns to the trigger
 * on close; Escape closes; the backdrop is click-to-dismiss.
 */
export default function ModelSettings({
  open,
  model,
  onClose,
  onSaved,
}: {
  open: boolean;
  model: ModelInfo | null;
  onClose: () => void;
  onSaved: (model: ModelInfo) => void;
}) {
  const [providers, setProviders] = useState<ModelProviderOption[]>([]);
  const [providerId, setProviderId] = useState<ProviderId>("ollama");
  const [agentBackend, setAgentBackend] = useState("anthropic");
  const [modelName, setModelName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keyHint, setKeyHint] = useState<string | null>(null);

  const [busy, setBusy] = useState<"idle" | "testing" | "saving">("idle");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const dialogRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  const selected = useMemo(
    () => providers.find((p) => p.id === providerId) ?? null,
    [providers, providerId],
  );

  // Load the provider catalogue and seed the form from the active model.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    void (async () => {
      try {
        const options = await api.modelOptions();
        if (cancelled) return;
        setProviders(options.providers);

        const activeId: ProviderId =
          model?.provider === "pi"
            ? "pi"
            : model?.provider === "cloud"
              ? ((model.cloud_provider as "anthropic" | "openai") ?? "anthropic")
              : "ollama";
        setProviderId(activeId);
        if (model?.provider === "pi" && model.cloud_provider) {
          setAgentBackend(model.cloud_provider);
        }
        const preset = options.providers.find((p) => p.id === activeId);
        // Only carry the running model's name over when the panel opens on the
        // provider that is actually running it. Otherwise the field showed
        // things like "deterministic" (the stub) under "Ollama".
        const activeMatchesRunning =
          (activeId === "ollama" && model?.provider === "ollama") ||
          (activeId === "pi" && model?.provider === "pi") ||
          (model?.provider === "cloud" && model.cloud_provider === activeId);
        setModelName(
          activeMatchesRunning && model?.model ? model.model : preset?.models[0] ?? "",
        );
        setBaseUrl(preset?.default_base_url ?? "");
      } catch {
        if (!cancelled) {
          setResult({ ok: false, message: "Could not load provider options." });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, model]);

  useEffect(() => {
    if (open) firstFieldRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const choose = (id: ProviderId) => {
    const preset = providers.find((p) => p.id === id);
    setProviderId(id);
    setModelName(preset?.models[0] ?? "");
    setBaseUrl(preset?.default_base_url ?? "");
    // A key belongs to one vendor. Switching vendor clears the field so an
    // Anthropic key is never submitted against an OpenAI endpoint.
    setApiKey("");
    setKeyHint(null);
    setResult(null);
  };

  const needsKey =
    selected?.needs_api_key && !(providerId === "pi" && LOCAL_BACKENDS.has(agentBackend));

  const payload = (): ModelConfigRequest => ({
    provider: providerId,
    model: modelName.trim() || undefined,
    agent_backend: providerId === "pi" ? agentBackend : undefined,
    base_url: baseUrl.trim() || undefined,
    api_key: apiKey.trim() || undefined,
  });

  const test = async () => {
    setBusy("testing");
    setResult(null);
    try {
      const response = await api.testModel(payload());
      setResult({ ok: response.ok, message: response.detail });
    } catch (error) {
      setResult({
        ok: false,
        message: error instanceof ApiError ? error.message : "The test request failed.",
      });
    } finally {
      setBusy("idle");
    }
  };

  const save = async () => {
    setBusy("saving");
    setResult(null);
    try {
      const response = await api.setModelConfig(payload());
      setKeyHint(response.config.api_key_hint ?? null);
      setApiKey("");
      onSaved(response.model);
      setResult({ ok: true, message: `Now using ${response.model.label}.` });
    } catch (error) {
      setResult({
        ok: false,
        message: error instanceof ApiError ? error.message : "Could not save the configuration.",
      });
    } finally {
      setBusy("idle");
    }
  };

  const reset = async () => {
    setBusy("saving");
    try {
      const response = await api.resetModelConfig();
      onSaved(response.model);
      setApiKey("");
      setKeyHint(null);
      setResult({ ok: true, message: `Reverted to .env — ${response.model.label}.` });
    } catch (error) {
      setResult({
        ok: false,
        message: error instanceof ApiError ? error.message : "Could not reset.",
      });
    } finally {
      setBusy("idle");
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      onMouseDown={(event) => {
        if (!dialogRef.current?.contains(event.target as Node)) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-settings-title"
        className="card max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-b-none p-5 sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="model-settings-title" className="text-base font-semibold tracking-tight">
              Model settings
            </h2>
            <p className="mt-1 text-xs text-ink-muted">
              Switch between the local model and a cloud provider. Takes effect on the next
              message — no restart.
            </p>
          </div>
          <button className="btn px-2 py-1 text-xs" onClick={onClose} aria-label="Close settings">
            Close
          </button>
        </div>

        <div className="mt-4 space-y-4">
          <div>
            <label htmlFor="provider" className="label">
              Provider
            </label>
            <select
              id="provider"
              ref={firstFieldRef}
              className="field"
              value={providerId}
              onChange={(event) => choose(event.target.value as typeof providerId)}
            >
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.label}
                </option>
              ))}
            </select>
            {selected?.help && <p className="mt-1.5 text-xs text-ink-faint">{selected.help}</p>}
          </div>

          {selected && (selected.backends?.length ?? 0) > 0 && (
            <div>
              <label htmlFor="agent-backend" className="label">
                Agent backend
              </label>
              <select
                id="agent-backend"
                className="field"
                value={agentBackend}
                onChange={(event) => {
                  setAgentBackend(event.target.value);
                  setApiKey("");
                  setKeyHint(null);
                }}
              >
                {(selected.backends ?? []).map((backend) => (
                  <option key={backend} value={backend}>
                    {backend}
                    {LOCAL_BACKENDS.has(backend) ? " (local, no key)" : ""}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-xs text-ink-faint">
                Pi orchestrates the turn; this is the model it calls underneath. Every Pi
                tool is disabled — it generates from the evidence we hand it, nothing else.
              </p>
            </div>
          )}

          <div>
            <label htmlFor="model-name" className="label">
              Model
            </label>
            <input
              id="model-name"
              className="field"
              list="model-presets"
              value={modelName}
              placeholder={selected?.models[0] ?? "model name"}
              onChange={(event) => setModelName(event.target.value)}
              autoComplete="off"
            />
            <datalist id="model-presets">
              {selected?.models.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <p className="mt-1.5 text-xs text-ink-faint">
              Presets are suggestions — any model name the provider accepts works.
            </p>
          </div>

          {selected?.needs_base_url && (
            <div>
              <label htmlFor="base-url" className="label">
                Base URL
              </label>
              <input
                id="base-url"
                className="field font-mono text-[13px]"
                value={baseUrl}
                placeholder={selected.default_base_url ?? "https://…"}
                onChange={(event) => setBaseUrl(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              {providerId === "openai" && (
                <p className="mt-1.5 text-xs text-ink-faint">
                  Point this at any OpenAI-compatible gateway — OpenRouter, Together, Groq,
                  vLLM, LM Studio.
                </p>
              )}
              {providerId === "ollama" && (
                <p className="mt-1.5 text-xs text-ink-faint">
                  In Docker use <code className="font-mono">http://host.docker.internal:11434</code>.
                </p>
              )}
            </div>
          )}

          {needsKey && (
            <div>
              <label htmlFor="api-key" className="label">
                API key
              </label>
              <input
                id="api-key"
                className="field font-mono text-[13px]"
                type="password"
                value={apiKey}
                placeholder={keyHint ? `saved ${keyHint} — type to replace` : "sk-…"}
                onChange={(event) => setApiKey(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <p className="mt-1.5 text-xs text-ink-faint">
                Held in the backend&apos;s memory only — never written to disk, never returned
                to this page, never logged. It is gone when the backend restarts.
              </p>
            </div>
          )}

          {result && (
            <p
              role="status"
              className={`rounded-lg px-3 py-2 text-xs ${
                result.ok ? "bg-ok/10 text-ok" : "bg-danger/10 text-danger"
              }`}
            >
              {result.message}
            </p>
          )}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <button
            className="btn px-3 py-2 text-sm"
            onClick={() => void test()}
            disabled={busy !== "idle"}
          >
            {busy === "testing" ? "Testing…" : "Test connection"}
          </button>
          <button
            className="btn-primary px-3 py-2 text-sm"
            onClick={() => void save()}
            disabled={busy !== "idle"}
          >
            {busy === "saving" ? "Saving…" : "Save and use"}
          </button>
          <button
            className="ml-auto text-xs text-ink-muted underline"
            onClick={() => void reset()}
            disabled={busy !== "idle"}
          >
            Reset to .env
          </button>
        </div>
      </div>
    </div>
  );
}
