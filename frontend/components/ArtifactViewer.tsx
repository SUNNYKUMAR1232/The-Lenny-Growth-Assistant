"use client";

import { useMemo, useState } from "react";
import type { Artifact } from "@/lib/types";
import Markdown from "./Markdown";

/**
 * Artifact Viewer — the second half of the artifact isolation story.
 *
 * HTML artifacts render inside an iframe with `sandbox` set to nothing at all
 * (`sandbox=""`), which means: no scripts, no same-origin access, no forms,
 * no top-level navigation, no popups. Combined with the server-side
 * sanitizer and the CSP the document carries, an artifact cannot read this
 * page's DOM, cannot touch its cookies or localStorage, and cannot call out
 * to the network.
 *
 * `srcdoc` is used rather than a blob URL so the frame inherits an opaque
 * origin and never gets a URL a user could be tricked into opening directly.
 */
export default function ArtifactViewer({
  artifact,
  onClose,
}: {
  artifact: Artifact | null;
  onClose?: () => void;
}) {
  const [tab, setTab] = useState<"preview" | "source">("preview");

  const sanitizationNote = useMemo(() => {
    const report = (artifact?.metadata as any)?.sanitization;
    if (!report) return null;
    const bits: string[] = [];
    if (report.had_script) bits.push("script tags removed");
    if (report.had_event_handlers) bits.push("event handlers removed");
    if (report.removed_urls?.length) bits.push(`${report.removed_urls.length} remote URL(s) blocked`);
    if (report.removed_style_declarations) bits.push("unsafe CSS removed");
    return bits.length ? bits.join(" · ") : null;
  }, [artifact]);

  if (!artifact) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-line bg-surface-raised text-ink-faint">
          <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M4 5h16v14H4z" strokeLinejoin="round" />
            <path d="M4 9h16M8 9v10" strokeLinejoin="round" />
          </svg>
        </div>
        <p className="text-sm font-medium text-ink">No artifact yet</p>
        <p className="max-w-xs text-sm text-ink-muted">
          Ask for a document, a checklist, or an HTML one-pager and it renders here beside
          the chat.
        </p>
      </div>
    );
  }

  const download = () => {
    const blob = new Blob([artifact.content], {
      type: artifact.type === "html" ? "text/html" : "text/markdown",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${artifact.title.replace(/[^\w\- ]+/g, "").trim() || "artifact"}.${
      artifact.type === "html" ? "html" : "md"
    }`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="flex h-full flex-col" aria-label="Artifact viewer">
      <header className="flex items-start gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="chip uppercase tracking-wide">{artifact.type}</span>
            <h2 className="truncate text-sm font-semibold" title={artifact.title}>
              {artifact.title}
            </h2>
          </div>
          {sanitizationNote && (
            <p className="mt-1 text-xs text-ink-faint" title="Applied before rendering">
              Sanitized: {sanitizationNote}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <div
            role="tablist"
            aria-label="Artifact view"
            className="flex rounded-lg border border-line p-0.5"
          >
            {(["preview", "source"] as const).map((value) => (
              <button
                key={value}
                role="tab"
                aria-selected={tab === value}
                onClick={() => setTab(value)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors ${
                  tab === value ? "bg-surface-sunken text-ink" : "text-ink-muted hover:text-ink"
                }`}
              >
                {value}
              </button>
            ))}
          </div>
          <button className="btn px-2 py-1.5 text-xs" onClick={download} title="Download artifact">
            Download
          </button>
          {onClose && (
            <button
              className="btn px-2 py-1.5 text-xs lg:hidden"
              onClick={onClose}
              aria-label="Close artifact viewer"
            >
              Close
            </button>
          )}
        </div>
      </header>

      <div className="scroll-area min-h-0 flex-1 bg-surface">
        {tab === "source" ? (
          <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed text-ink-muted">
            {artifact.content}
          </pre>
        ) : artifact.type === "html" ? (
          <iframe
            title={`Artifact preview: ${artifact.title}`}
            srcDoc={artifact.content}
            sandbox=""
            referrerPolicy="no-referrer"
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <div className="mx-auto max-w-3xl px-6 py-6">
            <Markdown>{artifact.content}</Markdown>
          </div>
        )}
      </div>

      {artifact.type === "html" && tab === "preview" && (
        <footer className="border-t border-line px-4 py-2 text-xs text-ink-faint">
          Rendered in a sandboxed frame: scripts, forms, and network requests are disabled.
        </footer>
      )}
    </section>
  );
}
