"use client";

import type { Message } from "@/lib/types";
import Markdown from "./Markdown";
import SourcesList from "./SourcesList";

const ROUTE_LABEL: Record<string, string> = {
  KNOWLEDGE_Q: "Grounded answer",
  SHIP30: "Ship 30 essay",
  ARTIFACT: "Artifact",
};

export default function MessageBubble({
  message,
  onOpenArtifact,
}: {
  message: Message;
  onOpenArtifact?: (artifactId: string) => void;
}) {
  const isUser = message.role === "user";
  const meta = message.metadata ?? {};
  const grounding = meta.grounding;

  if (isUser) {
    return (
      <div className="flex justify-end animate-fade-up">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[15px] leading-relaxed text-white sm:max-w-[75%]">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <article className="animate-fade-up" aria-label="Assistant response">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        {meta.route && <span className="chip">{ROUTE_LABEL[meta.route] ?? meta.route}</span>}
        {meta.model_label && (
          <span className="text-xs font-medium text-ink-faint" title="Model that wrote this">
            {meta.model_label}
          </span>
        )}
        {grounding && grounding.action === "annotated" && (
          <span className="chip border-warn/40 text-warn" title="Some claims weakly supported">
            weakly grounded ({grounding.supported_claims}/{grounding.checked_claims})
          </span>
        )}
        {grounding && grounding.action === "refused" && (
          <span className="chip border-danger/40 text-danger">answer withheld</span>
        )}
        {grounding && grounding.action === "accepted" && grounding.checked_claims > 0 && (
          <span className="chip border-ok/30 text-ok" title="Claims matched to retrieved evidence">
            grounded {grounding.supported_claims}/{grounding.checked_claims}
          </span>
        )}
        {typeof meta.retrieval_latency_ms === "number" && (
          <span className="text-xs text-ink-faint">
            retrieval {Math.round(meta.retrieval_latency_ms)}ms
          </span>
        )}
      </div>

      <div className="rounded-2xl rounded-tl-md border border-line bg-surface-raised px-4 py-3">
        <Markdown>{message.content}</Markdown>

        {meta.artifact_id && onOpenArtifact && (
          <button
            className="btn mt-3 px-2.5 py-1.5 text-xs"
            onClick={() => onOpenArtifact(meta.artifact_id!)}
          >
            Open in Artifact Viewer
          </button>
        )}

        {meta.memories_used && meta.memories_used.length > 0 && (
          <p className="mt-3 text-xs text-ink-faint">
            Personalized using {meta.memories_used.length} remembered detail
            {meta.memories_used.length === 1 ? "" : "s"}:{" "}
            {meta.memories_used.map((m) => m.key).join(", ")} — personalization only, not
            evidence.
          </p>
        )}

        <SourcesList evidence={meta.evidence ?? []} />
      </div>
    </article>
  );
}
