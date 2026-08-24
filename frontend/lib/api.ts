/**
 * Typed API client.
 *
 * Every backend error arrives as `{ error: { code, message } }`; `ApiError`
 * carries the code so the UI can branch on it (MODEL_UNAVAILABLE renders
 * "start Ollama", RETRIEVAL_UNAVAILABLE renders something else) instead of
 * showing one generic failure toast.
 */

import type {
  Artifact,
  ChatResponse,
  HealthResponse,
  MemoryRecord,
  ModelConfigRequest,
  ModelConfigResponse,
  ModelInfo,
  ModelOptionsResponse,
  ModelTestResponse,
  RouteName,
  SessionDetail,
  SessionSummary,
  StreamEvent,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

export class ApiError extends Error {
  code: string;
  status: number;
  details?: Record<string, unknown>;

  constructor(code: string, message: string, status: number, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

/**
 * True when a request ended because someone cancelled it — Stop, a session
 * switch, or unmount — rather than because anything failed. Callers use this
 * to stay silent instead of showing an error the user caused on purpose.
 */
export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException ? err.name === "AbortError" : (err as Error)?.name === "AbortError";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      "NETWORK_ERROR",
      "Can't reach the API. Is the backend running on " + API_BASE + "?",
      0,
    );
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = body?.error;
    throw new ApiError(
      error?.code ?? "UNKNOWN_ERROR",
      error?.message ?? `Request failed with status ${response.status}.`,
      response.status,
      error?.details,
    );
  }
  return body as T;
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  model: () => request<ModelInfo>("/api/model"),

  // ------------------------------------------------- runtime model config
  modelOptions: () => request<ModelOptionsResponse>("/api/model/options"),

  testModel: (config: ModelConfigRequest) =>
    request<ModelTestResponse>("/api/model/test", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  setModelConfig: (config: ModelConfigRequest) =>
    request<ModelConfigResponse>("/api/model/config", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  resetModelConfig: () =>
    request<ModelConfigResponse>("/api/model/config", { method: "DELETE" }),

  listSessions: (externalUserId: string) =>
    request<{ sessions: SessionSummary[]; total: number }>(
      `/api/sessions?external_user_id=${encodeURIComponent(externalUserId)}`,
    ),

  createSession: (externalUserId: string, title?: string) =>
    request<SessionSummary>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ external_user_id: externalUserId, title }),
    }),

  getSession: (sessionId: string) => request<SessionDetail>(`/api/sessions/${sessionId}`),

  deleteSession: (sessionId: string) =>
    request<void>(`/api/sessions/${sessionId}`, { method: "DELETE" }),

  sendMessage: (
    sessionId: string,
    content: string,
    options: { routeHint?: RouteName; artifactFormat?: "markdown" | "html" } = {},
  ) =>
    request<ChatResponse>(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        content,
        route_hint: options.routeHint ?? null,
        artifact_format: options.artifactFormat ?? null,
        stream: false,
      }),
    }),

  getArtifact: (artifactId: string) => request<Artifact>(`/api/artifacts/${artifactId}`),

  listArtifacts: (sessionId: string) =>
    request<{ artifacts: Artifact[] }>(`/api/artifacts?session_id=${sessionId}`),

  listMemories: (externalUserId: string) =>
    request<{ memories: MemoryRecord[]; enabled: boolean }>(
      `/api/memories?external_user_id=${encodeURIComponent(externalUserId)}`,
    ),

  deleteMemory: (memoryId: string) =>
    request<void>(`/api/memories/${memoryId}`, { method: "DELETE" }),

  clearMemories: (externalUserId: string) =>
    request<{ deleted: number }>(
      `/api/memories?external_user_id=${encodeURIComponent(externalUserId)}`,
      { method: "DELETE" },
    ),

  ingestionStats: () =>
    request<{ documents: number; chunks: number; embedded_chunks: number; guests: number }>(
      "/api/ingestion/stats",
    ),
};

/**
 * Minimal SSE parser.
 *
 * `EventSource` cannot POST, so the stream is read off `fetch` directly.
 * Exported separately from the transport so it can be unit-tested against
 * fixture text without a server.
 */
export function parseSseChunk(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";

  for (const block of blocks) {
    let name = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) name = line.slice(7).trim();
      else if (line.startsWith("data: ")) data += line.slice(6);
    }
    if (!name || !data) continue;
    let payload: any;
    try {
      payload = JSON.parse(data);
    } catch {
      continue;
    }
    switch (name) {
      case "route":
      case "memory":
      case "retrieval":
      case "evidence":
      case "token":
        events.push({ type: name, ...payload } as StreamEvent);
        break;
      case "final":
        events.push({ type: "final", response: payload as ChatResponse });
        break;
      case "error":
        events.push({ type: "error", error: payload.error });
        break;
      default:
        break;
    }
  }
  return { events, rest };
}

export async function streamMessage(
  sessionId: string,
  content: string,
  onEvent: (event: StreamEvent) => void,
  options: { routeHint?: RouteName; artifactFormat?: "markdown" | "html"; signal?: AbortSignal } = {},
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content,
        route_hint: options.routeHint ?? null,
        artifact_format: options.artifactFormat ?? null,
        stream: true,
      }),
      signal: options.signal,
    });
  } catch (err) {
    // A deliberate cancel must stay distinguishable from a dead API: reporting
    // "can't reach the API" after the user pressed Stop is a lie.
    if (isAbortError(err)) throw err;
    throw new ApiError("NETWORK_ERROR", `Can't reach the API at ${API_BASE}.`, 0);
  }

  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      body?.error?.code ?? "UNKNOWN_ERROR",
      body?.error?.message ?? "The assistant could not start a response.",
      response.status,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // Stop delivering events the moment a cancel lands, so a late chunk
      // already in the pipe cannot be written into a session the user left.
      if (options.signal?.aborted) break;
      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseSseChunk(buffer);
      buffer = rest;
      events.forEach(onEvent);
    }
  } finally {
    // Releases the connection whether we finished, aborted, or threw.
    await reader.cancel().catch(() => {});
  }
}
