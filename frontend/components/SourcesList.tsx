"use client";

import { useState } from "react";
import type { EvidenceItem } from "@/lib/types";

/**
 * Source citations.
 *
 * Every grounded answer exposes exactly what it was given: the episode, the
 * guest, the retrieval signal that surfaced it, and a link that jumps to the
 * moment in the episode. An evaluator should be able to check a claim in two
 * clicks — that is the whole point of the evidence pack.
 */
export default function SourcesList({ evidence }: { evidence: EvidenceItem[] }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  if (!evidence.length) return null;

  const episodes = new Set(evidence.map((item) => item.title)).size;

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted transition-colors hover:text-ink"
      >
        <svg
          viewBox="0 0 24 24"
          className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-90" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {evidence.length} source{evidence.length === 1 ? "" : "s"} from {episodes} episode
        {episodes === 1 ? "" : "s"}
      </button>

      {open && (
        <ol className="mt-2 space-y-2">
          {evidence.map((item) => {
            const isOpen = expanded === item.chunk_id;
            return (
              <li key={item.chunk_id} className="card p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="chip font-mono">{item.source_id}</span>
                      <span className="truncate text-sm font-medium">{item.title}</span>
                    </div>
                    <p className="mt-0.5 text-xs text-ink-muted">
                      {item.guest ? `${item.guest} · ` : ""}chunk {item.chunk_index} ·{" "}
                      <span title="Which retrieval leg surfaced this chunk">{item.retrieval}</span>{" "}
                      · score {item.score.toFixed(3)}
                    </p>
                  </div>
                  {item.source_url && (
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn shrink-0 px-2 py-1 text-xs"
                      title="Open the episode at this moment"
                    >
                      Listen
                    </a>
                  )}
                </div>

                <p
                  className={`mt-2 text-sm leading-relaxed text-ink-muted ${
                    isOpen ? "" : "line-clamp-3"
                  }`}
                >
                  {item.text}
                </p>
                <button
                  onClick={() => setExpanded(isOpen ? null : item.chunk_id)}
                  className="mt-1 text-xs font-medium text-accent"
                >
                  {isOpen ? "Show less" : "Show full excerpt"}
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
