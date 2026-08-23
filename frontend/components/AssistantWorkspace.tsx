"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, streamMessage } from "@/lib/api";
import type {
  Artifact,
  HealthResponse,
  Message,
  ModelInfo,
  SessionSummary,
} from "@/lib/types";
import ArtifactViewer from "./ArtifactViewer";
import MemoryPanel from "./MemoryPanel";
import MessageBubble from "./MessageBubble";
import ModelBadge from "./ModelBadge";
import PipelineStatus, { type PipelineStage } from "./PipelineStatus";
import SessionSidebar from "./SessionSidebar";

const USER_KEY = "lga-user-id";
const THEME_KEY = "lga-theme";

const STARTERS = [
  "How do you know when you have product-market fit?",
  "What do Lenny's guests say about improving activation?",
  "Compare how different guests think about retention",
  "Write a Ship 30 for 30 essay about onboarding as a growth lever",
  "Build an HTML one-pager summarising a growth review agenda",
];

function localUserId(): string {
  if (typeof window === "undefined") return "local-demo-user";
  let id = window.localStorage.getItem(USER_KEY);
  if (!id) {
    id = `user-${crypto.randomUUID().slice(0, 8)}`;
    window.localStorage.setItem(USER_KEY, id);
  }
  return id;
}

export default function AssistantWorkspace() {
  const [userId, setUserId] = useState("local-demo-user");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [corpus, setCorpus] = useState<{ documents: number; chunks: number; guests: number } | null>(
    null,
  );

  const [input, setInput] = useState("");
  const [stage, setStage] = useState<PipelineStage>("idle");
  const [stageDetail, setStageDetail] = useState<string>("");
  const [streamed, setStreamed] = useState("");
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [dark, setDark] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [artifactOpen, setArtifactOpen] = useState(false);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const busy = stage !== "idle";

  // ---------------------------------------------------------------- bootstrap
  useEffect(() => {
    setUserId(localUserId());
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  useEffect(() => {
    if (userId === "local-demo-user") return;
    void refreshStatus();
    void loadSessions(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamed, stage]);

  const refreshStatus = useCallback(async () => {
    const [modelInfo, healthInfo, stats] = await Promise.allSettled([
      api.model(),
      api.health(),
      api.ingestionStats(),
    ]);
    if (modelInfo.status === "fulfilled") setModel(modelInfo.value);
    if (healthInfo.status === "fulfilled") setHealth(healthInfo.value);
    if (stats.status === "fulfilled") setCorpus(stats.value);
    if (modelInfo.status === "rejected") {
      const err = modelInfo.reason as ApiError;
      setError({ code: err.code, message: err.message });
    }
  }, []);

  const loadSessions = useCallback(
    async (openLatest: boolean) => {
      try {
        const data = await api.listSessions(userId);
        setSessions(data.sessions);
        if (openLatest && data.sessions.length > 0) {
          await openSession(data.sessions[0].id);
        }
      } catch (err) {
        const apiError = err as ApiError;
        setError({ code: apiError.code, message: apiError.message });
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [userId],
  );

  const openSession = useCallback(async (sessionId: string) => {
    try {
      const detail = await api.getSession(sessionId);
      setActiveSession(sessionId);
      setMessages(detail.messages);
      setStreamed("");
      setSidebarOpen(false);

      const latest = detail.artifacts.at(-1);
      if (latest) {
        const full = await api.getArtifact(latest.id);
        setArtifact(full);
      } else {
        setArtifact(null);
      }
    } catch (err) {
      const apiError = err as ApiError;
      setError({ code: apiError.code, message: apiError.message });
    }
  }, []);

  const newSession = useCallback(async () => {
    try {
      const session = await api.createSession(userId);
      setSessions((current) => [session, ...current]);
      setActiveSession(session.id);
      setMessages([]);
      setArtifact(null);
      setStreamed("");
      setSidebarOpen(false);
      inputRef.current?.focus();
      return session.id;
    } catch (err) {
      const apiError = err as ApiError;
      setError({ code: apiError.code, message: apiError.message });
      return null;
    }
  }, [userId]);

  const removeSession = useCallback(
    async (sessionId: string) => {
      try {
        await api.deleteSession(sessionId);
        setSessions((current) => current.filter((session) => session.id !== sessionId));
        if (sessionId === activeSession) {
          setActiveSession(null);
          setMessages([]);
          setArtifact(null);
        }
      } catch (err) {
        const apiError = err as ApiError;
        setError({ code: apiError.code, message: apiError.message });
      }
    },
    [activeSession],
  );

  // -------------------------------------------------------------------- send
  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || busy) return;

      let sessionId = activeSession;
      if (!sessionId) {
        sessionId = await newSession();
        if (!sessionId) return;
      }

      setError(null);
      setInput("");
      setStreamed("");
      setStage("routing");
      setStageDetail("");

      const optimistic: Message = {
        id: `pending-${Date.now()}`,
        session_id: sessionId,
        role: "user",
        content,
        created_at: new Date().toISOString(),
        metadata: {},
      };
      setMessages((current) => [...current, optimistic]);

      try {
        await streamMessage(sessionId, content, (event) => {
          switch (event.type) {
            case "route":
              setStage("memory");
              setStageDetail(`route: ${event.route} (${event.method})`);
              break;
            case "memory":
              setStage("retrieving");
              setStageDetail(event.count ? `${event.count} memories in context` : "");
              break;
            case "retrieval":
              setStage("retrieving");
              break;
            case "evidence":
              setStage("generating");
              setStageDetail(
                `${event.count} excerpt${event.count === 1 ? "" : "s"} · ${event.strategy}`,
              );
              break;
            case "token":
              setStreamed((current) => current + event.text);
              break;
            case "final": {
              const response = event.response;
              setStage("validating");
              setMessages((current) => [
                ...current.filter((message) => message.id !== optimistic.id),
                response.user_message,
                response.message,
              ]);
              setStreamed("");
              if (response.artifact) {
                setArtifact(response.artifact);
                setArtifactOpen(true);
              }
              if (response.warnings.length) {
                setStageDetail(response.warnings.join(" "));
              }
              void loadSessions(false);
              break;
            }
            case "error":
              setError(event.error);
              setMessages((current) => current.filter((message) => message.id !== optimistic.id));
              break;
          }
        });
      } catch (err) {
        const apiError = err as ApiError;
        setError({ code: apiError.code, message: apiError.message });
        setMessages((current) => current.filter((message) => message.id !== optimistic.id));
      } finally {
        setStage("idle");
        setStreamed("");
        void refreshStatus();
      }
    },
    [activeSession, busy, loadSessions, newSession, refreshStatus],
  );

  const openArtifact = useCallback(async (artifactId: string) => {
    try {
      setArtifact(await api.getArtifact(artifactId));
      setArtifactOpen(true);
    } catch (err) {
      const apiError = err as ApiError;
      setError({ code: apiError.code, message: apiError.message });
    }
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    window.localStorage.setItem(THEME_KEY, next ? "dark" : "light");
  };

  const emptyState = useMemo(() => messages.length === 0 && !busy, [messages.length, busy]);

  return (
    <div className="flex h-dvh flex-col bg-surface">
      {/* ------------------------------------------------------------ header */}
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-3 py-2.5 sm:px-4">
        <button
          className="btn px-2 py-1.5 lg:hidden"
          onClick={() => setSidebarOpen((value) => !value)}
          aria-label="Toggle session list"
          aria-expanded={sidebarOpen}
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" />
          </svg>
        </button>

        <h1 className="mr-auto truncate text-sm font-semibold tracking-tight sm:text-base">
          Lenny Growth Assistant
        </h1>

        <ModelBadge model={model} health={health} />

        <button className="btn px-2 py-1.5 text-xs" onClick={() => setMemoryOpen(true)}>
          Memory
        </button>
        <button
          className="btn px-2 py-1.5"
          onClick={toggleTheme}
          aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
        >
          {dark ? (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" strokeLinecap="round" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" strokeLinejoin="round" />
            </svg>
          )}
        </button>
        <button
          className="btn px-2 py-1.5 text-xs lg:hidden"
          onClick={() => setArtifactOpen((value) => !value)}
          aria-expanded={artifactOpen}
        >
          Artifact
        </button>
      </header>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-3 border-b border-danger/30 bg-danger/5 px-4 py-2.5 text-sm"
        >
          <span className="font-mono text-xs text-danger">{error.code}</span>
          <p className="flex-1 text-ink">{error.message}</p>
          <button className="text-xs text-ink-muted underline" onClick={() => setError(null)}>
            dismiss
          </button>
        </div>
      )}

      {/* --------------------------------------------------------- main grid */}
      <div className="flex min-h-0 flex-1">
        <aside
          className={`${
            sidebarOpen ? "absolute inset-y-0 left-0 z-30 w-72 shadow-xl" : "hidden"
          } shrink-0 border-r border-line bg-surface-raised lg:static lg:block lg:w-64 lg:shadow-none`}
          style={{ top: sidebarOpen ? "0" : undefined }}
        >
          <SessionSidebar
            sessions={sessions}
            activeId={activeSession}
            onSelect={openSession}
            onCreate={newSession}
            onDelete={removeSession}
            corpus={corpus}
          />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="scroll-area min-h-0 flex-1 px-3 py-4 sm:px-6">
            <div className="mx-auto max-w-3xl space-y-5">
              {emptyState && (
                <div className="pt-8">
                  <h2 className="text-lg font-semibold tracking-tight">
                    Ask anything from Lenny&apos;s Podcast
                  </h2>
                  <p className="mt-1.5 max-w-xl text-sm text-ink-muted">
                    Answers are grounded in transcript evidence and cite the episode they came
                    from. Ask for an essay or a document and it renders beside the chat.
                  </p>
                  <ul className="mt-5 grid gap-2 sm:grid-cols-2">
                    {STARTERS.map((prompt) => (
                      <li key={prompt}>
                        <button
                          onClick={() => send(prompt)}
                          className="card w-full p-3 text-left text-sm text-ink-muted transition-colors hover:border-accent/40 hover:text-ink"
                        >
                          {prompt}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} onOpenArtifact={openArtifact} />
              ))}

              {streamed && (
                <article className="animate-fade-up" aria-label="Assistant response in progress">
                  <div className="rounded-2xl rounded-tl-md border border-line bg-surface-raised px-4 py-3">
                    <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{streamed}</p>
                  </div>
                </article>
              )}

              <PipelineStatus stage={stage} detail={stageDetail} />
              <div ref={bottomRef} />
            </div>
          </div>

          {/* ------------------------------------------------------ composer */}
          <div className="shrink-0 border-t border-line bg-surface px-3 py-3 sm:px-6">
            <form
              className="mx-auto flex max-w-3xl items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void send(input);
              }}
            >
              <label htmlFor="composer" className="sr-only">
                Message the assistant
              </label>
              <textarea
                id="composer"
                ref={inputRef}
                rows={1}
                value={input}
                disabled={busy}
                onChange={(event) => {
                  setInput(event.target.value);
                  event.target.style.height = "auto";
                  event.target.style.height = `${Math.min(event.target.scrollHeight, 200)}px`;
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send(input);
                  }
                }}
                placeholder="Ask about product, growth, retention… or ask for an essay or artifact"
                className="max-h-48 min-h-[44px] flex-1 resize-none rounded-xl border border-line bg-surface-raised px-3.5 py-2.5 text-[15px] leading-relaxed placeholder:text-ink-faint focus:border-accent focus:outline-none disabled:opacity-60"
              />
              <button
                type="submit"
                className="btn btn-primary h-[44px] px-4"
                disabled={busy || !input.trim()}
              >
                {busy ? "Working…" : "Send"}
              </button>
            </form>
            <p className="mx-auto mt-2 max-w-3xl text-xs text-ink-faint">
              Grounded in Lenny&apos;s Podcast transcripts. Claims are checked against retrieved
              excerpts; unsupported answers are flagged or withheld.
            </p>
          </div>
        </main>

        <aside
          className={`${
            artifactOpen ? "absolute inset-0 z-30 bg-surface" : "hidden"
          } lg:static lg:block lg:w-[46%] lg:max-w-2xl lg:border-l lg:border-line`}
        >
          <ArtifactViewer artifact={artifact} onClose={() => setArtifactOpen(false)} />
        </aside>
      </div>

      <MemoryPanel userId={userId} open={memoryOpen} onClose={() => setMemoryOpen(false)} />
    </div>
  );
}
