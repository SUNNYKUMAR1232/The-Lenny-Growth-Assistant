/**
 * Mirrors backend/app/schemas/contracts.py.
 * If you change a contract on one side, change it on the other.
 */

export type Role = "user" | "assistant" | "system";
export type RouteName = "KNOWLEDGE_Q" | "SHIP30" | "ARTIFACT";
export type ArtifactType = "markdown" | "html";
export type MemoryType = "semantic" | "episodic";
export type HealthStatus = "ok" | "degraded" | "down";

export interface EvidenceItem {
  source_id: string;
  chunk_id: string;
  title: string;
  guest: string | null;
  source_url: string | null;
  chunk_index: number;
  text: string;
  score: number;
  vector_score: number | null;
  keyword_score: number | null;
  retrieval: "vector" | "keyword" | "hybrid" | "episode";
}

export interface GroundingReport {
  enabled: boolean;
  checked_claims: number;
  supported_claims: number;
  supported_ratio: number;
  revised: boolean;
  action: "accepted" | "annotated" | "refused" | "skipped";
}

export interface MemoryUsed {
  id: string;
  type: MemoryType;
  key: string;
  value: string;
  confidence: number;
  importance: number;
}

export interface MemoryRecord extends MemoryUsed {
  user_id: string;
  created_at: string;
  updated_at: string;
  source_session_id: string | null;
}

export interface ModelProviderOption {
  id: "ollama" | "anthropic" | "openai" | "pi";
  label: string;
  needs_api_key: boolean;
  needs_base_url: boolean;
  default_base_url: string | null;
  models: string[];
  help: string | null;
  /** Pi drives another provider underneath; empty for direct providers. */
  backends: string[];
}

export interface ModelOptionsResponse {
  configurable: boolean;
  providers: ModelProviderOption[];
}

/** Write-only: `api_key` is sent, never received. */
export interface ModelConfigRequest {
  provider: "ollama" | "anthropic" | "openai" | "pi";
  model?: string;
  /** Pi only: the backend Pi should drive. */
  agent_backend?: string;
  base_url?: string;
  api_key?: string;
}

export interface ModelConfigResponse {
  config: {
    source: "environment" | "runtime";
    provider?: string;
    model?: string | null;
    cloud_provider?: string | null;
    base_url?: string | null;
    api_key_set: boolean;
    api_key_hint?: string | null;
  };
  model: ModelInfo;
}

export interface ModelTestResponse {
  ok: boolean;
  detail: string;
  label: string;
}

export interface ModelInfo {
  provider: "ollama" | "cloud" | "pi" | "stub";
  model: string;
  label: string;
  cloud_provider: string | null;
  embedding_provider: string;
  embedding_model: string;
  available: boolean;
  detail: string | null;
  fallback?: string | null;
  /** Whether the live config came from `.env` or from the settings panel. */
  source: "environment" | "runtime";
  /** False when the deployment forbids runtime model changes. */
  configurable: boolean;
}

export interface MessageMetadata {
  route?: RouteName;
  route_method?: string;
  provider?: string;
  model?: string;
  model_label?: string;
  evidence?: EvidenceItem[];
  evidence_strategy?: string;
  retrieval_latency_ms?: number;
  grounding?: GroundingReport;
  memories_used?: { id: string; key: string; type: MemoryType }[];
  skill?: Record<string, unknown>;
  warnings?: string[];
  artifact_id?: string;
  request_id?: string;
}

export interface Message {
  id: string;
  session_id: string;
  role: Role;
  content: string;
  created_at: string;
  metadata: MessageMetadata;
}

export interface SessionSummary {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
  message_count: number;
}

export interface ArtifactSummary {
  id: string;
  session_id: string;
  type: ArtifactType;
  title: string;
  created_at: string;
}

export interface Artifact extends ArtifactSummary {
  content: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface SessionDetail {
  session: SessionSummary;
  messages: Message[];
  artifacts: ArtifactSummary[];
}

export interface ChatResponse {
  session_id: string;
  request_id: string;
  user_message: Message;
  message: Message;
  route: RouteName;
  evidence: EvidenceItem[];
  memories_used: MemoryUsed[];
  grounding: GroundingReport;
  artifact: Artifact | null;
  model: ModelInfo;
  latency_ms: number;
  warnings: string[];
}

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  environment: string;
  components: Record<string, { status: HealthStatus; detail: string | null; latency_ms: number | null }>;
}

export interface ApiErrorPayload {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

/** Progress events emitted by the SSE chat endpoint. */
export type StreamEvent =
  | { type: "route"; route: RouteName; method: string; confidence: number; model: string }
  | { type: "memory"; count: number; keys: string[] }
  | { type: "retrieval"; status: string }
  | { type: "evidence"; count: number; strategy: string; items: EvidenceItem[]; degraded: boolean }
  | { type: "token"; text: string }
  | { type: "final"; response: ChatResponse }
  | { type: "error"; error: ApiErrorPayload };
