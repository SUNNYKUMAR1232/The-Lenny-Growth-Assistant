"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { MemoryRecord } from "@/lib/types";

/**
 * Memory controls.
 *
 * Personalization the user cannot see or delete is a dark pattern, so the
 * panel shows every stored memory with its confidence and lets the user remove
 * any of them. The copy is explicit that memory never counts as evidence.
 */
export default function MemoryPanel({
  userId,
  open,
  onClose,
}: {
  userId: string;
  open: boolean;
  onClose: () => void;
}) {
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api
      .listMemories(userId)
      .then((data) => {
        if (cancelled) return;
        setMemories(data.memories);
        setEnabled(data.enabled);
        setError(null);
      })
      .catch((err: ApiError) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open, userId]);

  if (!open) return null;

  const remove = async (id: string) => {
    setMemories((current) => current.filter((memory) => memory.id !== id));
    try {
      await api.deleteMemory(id);
    } catch (err) {
      setError((err as ApiError).message);
    }
  };

  const clearAll = async () => {
    try {
      await api.clearMemories(userId);
      setMemories([]);
    } catch (err) {
      setError((err as ApiError).message);
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/30"
      role="dialog"
      aria-modal="true"
      aria-label="What the assistant remembers"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-md flex-col border-l border-line bg-surface"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold">What I remember about you</h2>
            <p className="mt-0.5 text-xs text-ink-muted">
              Personalization only. Never used as evidence for what guests said.
            </p>
          </div>
          <button className="btn px-2 py-1 text-xs" onClick={onClose} aria-label="Close memory panel">
            Close
          </button>
        </header>

        <div className="scroll-area min-h-0 flex-1 p-4">
          {!enabled && (
            <p className="card p-3 text-sm text-ink-muted">
              Memory is disabled (<code className="font-mono">MEMORY_ENABLED=false</code>). The
              assistant still answers from transcript evidence and session context.
            </p>
          )}
          {error && <p className="card border-danger/40 p-3 text-sm text-danger">{error}</p>}
          {loading && <p className="text-sm text-ink-muted">Loading…</p>}

          {!loading && enabled && memories.length === 0 && !error && (
            <p className="text-sm text-ink-muted">
              Nothing remembered yet. Mention your role, your product, or how you like answers
              written, and it will show up here.
            </p>
          )}

          <ul className="space-y-2">
            {memories.map((memory) => (
              <li key={memory.id} className="card p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-mono text-xs text-ink-faint">{memory.key}</p>
                    <p className="mt-0.5 text-sm">{memory.value}</p>
                    <p className="mt-1 text-xs text-ink-faint">
                      {memory.type} · confidence {(memory.confidence * 100).toFixed(0)}% ·
                      importance {(memory.importance * 100).toFixed(0)}%
                    </p>
                  </div>
                  <button
                    className="btn shrink-0 px-2 py-1 text-xs hover:text-danger"
                    onClick={() => remove(memory.id)}
                  >
                    Forget
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {memories.length > 0 && (
          <footer className="border-t border-line p-3">
            <button className="btn w-full text-danger" onClick={clearAll}>
              Forget everything
            </button>
          </footer>
        )}
      </div>
    </div>
  );
}
