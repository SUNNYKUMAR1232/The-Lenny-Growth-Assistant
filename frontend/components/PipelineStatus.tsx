"use client";

/**
 * Retrieval / pipeline state.
 *
 * A local 8B model can take 10-30 seconds. An opaque spinner for that long
 * reads as "broken"; naming the stage the controller is in reads as "working",
 * and doubles as a live view of the architecture during a demo.
 */
export type PipelineStage =
  | "idle"
  | "routing"
  | "memory"
  | "retrieving"
  | "generating"
  | "validating";

const LABELS: Record<Exclude<PipelineStage, "idle">, string> = {
  routing: "Classifying the request…",
  memory: "Checking what I remember about you…",
  retrieving: "Searching the transcript corpus…",
  generating: "Writing from the evidence…",
  validating: "Checking claims against the sources…",
};

export default function PipelineStatus({
  stage,
  detail,
}: {
  stage: PipelineStage;
  detail?: string;
}) {
  if (stage === "idle") return null;

  return (
    <div
      className="flex items-center gap-2.5 px-1 py-2 text-sm text-ink-muted"
      role="status"
      aria-live="polite"
    >
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent"
            style={{ animationDelay: `${index * 160}ms` }}
          />
        ))}
      </span>
      <span>{LABELS[stage]}</span>
      {detail && <span className="text-xs text-ink-faint">{detail}</span>}
    </div>
  );
}
