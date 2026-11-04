// Typed wrappers over the local FastAPI server. Errors carry FastAPI's `detail` text.

export type RunStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "aborted"
  | "budget_exhausted";

export interface Project {
  id: string;
  title: string;
  idea: string;
  status: RunStatus;
  workflow: string;
  mock: boolean;
  created: string | null;
  cost: number;
  thumbnail: string | null;
  gate: { id: string; title: string; stage: string } | null;
  busy: boolean;
}

export interface Setup {
  meta_api_key: boolean;
  chromium: boolean;
}

async function parse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
  }
  return data as T;
}

export async function getJson<T>(path: string): Promise<T> {
  return parse<T>(await fetch(path));
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  return parse<T>(
    await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export type AgentStatus = "waiting" | "working" | "needs_you" | "done";

export interface Agent {
  name: string;
  status: AgentStatus;
  needs_you: number;
  artifacts: number;
  calls: number;
  images: number;
  cost: number;
  last: string | null;
}

export interface FieldRule {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  suggestions?: string[];
  format?: string;
}

export interface Gate {
  id: string;
  title: string;
  stage: string;
  kind: "review" | "confirm_changes";
  status: "pending" | "approved" | "changes_requested" | "aborted" | "superseded";
  form_schema: { title?: string; properties?: Record<string, FieldRule>; required?: string[] };
  empty_feedback_approves: boolean;
  response: { action: string; text: string; form: Record<string, unknown> } | null;
}

export interface ArtifactMeta {
  id: string;
  name: string;
  mime: string;
}

export interface Play {
  version: number;
  url: string;
  sounds: boolean;
}

/** A change asked in an agent's chat, waiting for the open gate's Request changes. */
export interface QueuedChange {
  id: number;
  agent: string;
  slot: string;
  text: string;
}

export interface RunAgents {
  agents: Agent[];
  last_event: number;
  play: Play | null;
  status: RunStatus;
  busy: boolean;
  gate: Gate | null;
  queued: QueuedChange[];
  error: string | null;
}

interface ItemBase {
  id: number;
  at: string;
  agent: string;
}

export type ThreadItem = ItemBase &
  (
    | {
        kind: "chat";
        role: "user" | "agent";
        text: string;
        intent?: string;
        status?: string;
        error?: boolean;
      }
    | { kind: "progress"; text: string }
    | { kind: "artifact"; artifact: ArtifactMeta }
    | { kind: "gate"; gate: Gate; artifacts: ArtifactMeta[] }
    | {
        kind: "gate_response";
        gate_id: string;
        title: string;
        status: string;
        text: string;
        chat?: number;
      }
    | { kind: "run"; type: string; message: string | null }
  );

export const artifactUrl = (id: string) => `/api/artifacts/${id}`;

export const agentPath = (runId: string, agent: string) =>
  `/api/runs/${runId}/agents/${encodeURIComponent(agent)}`;
