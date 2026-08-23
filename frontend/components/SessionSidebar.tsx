"use client";

import type { SessionSummary } from "@/lib/types";

export default function SessionSidebar({
  sessions,
  activeId,
  onSelect,
  onCreate,
  onDelete,
  corpus,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  corpus: { documents: number; chunks: number; guests: number } | null;
}) {
  return (
    <nav className="flex h-full flex-col" aria-label="Chat sessions">
      <div className="p-3">
        <button className="btn btn-primary w-full" onClick={onCreate}>
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" strokeLinecap="round" />
          </svg>
          New chat
        </button>
      </div>

      <div className="scroll-area min-h-0 flex-1 px-2 pb-2">
        {sessions.length === 0 ? (
          <p className="px-2 py-4 text-sm text-ink-faint">
            No conversations yet. Ask something to start one.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((session) => {
              const active = session.id === activeId;
              return (
                <li key={session.id} className="group relative">
                  <button
                    onClick={() => onSelect(session.id)}
                    aria-current={active ? "page" : undefined}
                    className={`w-full truncate rounded-lg px-3 py-2 pr-8 text-left text-sm transition-colors ${
                      active
                        ? "bg-surface-sunken font-medium text-ink"
                        : "text-ink-muted hover:bg-surface-sunken hover:text-ink"
                    }`}
                    title={session.title}
                  >
                    {session.title}
                    <span className="ml-2 text-xs text-ink-faint">
                      {session.message_count > 0 ? `${session.message_count}` : ""}
                    </span>
                  </button>
                  <button
                    onClick={() => onDelete(session.id)}
                    aria-label={`Delete session: ${session.title}`}
                    className="absolute right-1 top-1.5 rounded-md p-1.5 text-ink-faint opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
                    </svg>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {corpus && (
        <div className="border-t border-line px-4 py-3 text-xs text-ink-faint">
          <p className="font-medium text-ink-muted">Knowledge base</p>
          <p className="mt-1">
            {corpus.documents.toLocaleString()} episodes · {corpus.chunks.toLocaleString()} chunks
          </p>
          {corpus.documents === 0 && (
            <p className="mt-1 text-warn">
              Empty — run <code className="font-mono">make ingest</code>.
            </p>
          )}
        </div>
      )}
    </nav>
  );
}
